from __future__ import annotations

import hashlib
import logging
import signal
import threading
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError

from zlapi import ThreadType, ZaloAPI

from .config import get_settings
from .connector import jsonable
from .db import Database
from .models import Event, ZaloAccount, utcnow
from .security import SecretBox

logger = logging.getLogger("zlapi.listener")


def listener_event_dedupe_key(
    event_type: str,
    thread_id: str | None,
    payload: dict,
) -> str | None:
    if event_type != "message.received" or not thread_id:
        return None
    message_object = payload.get("message_object")
    message_object = message_object if isinstance(message_object, dict) else {}
    identity = next(
        (
            str(value).strip()
            for value in (
                message_object.get("msgId"),
                message_object.get("messageId"),
                message_object.get("globalMsgId"),
                payload.get("message_id"),
                message_object.get("cliMsgId"),
                message_object.get("clientMsgId"),
            )
            if value not in (None, "") and str(value).strip()
        ),
        None,
    )
    if not identity:
        return None
    raw_key = f"{thread_id}\x1f{identity}".encode()
    return hashlib.sha256(raw_key).hexdigest()


def _mark_account_online(session, account_id: str) -> None:
    account = session.get(ZaloAccount, account_id)
    if account:
        account.status = "online"
        account.last_seen_at = utcnow()
        account.last_error = None


def store_listener_event(
    database: Database,
    account_id: str,
    event_type: str,
    payload: dict,
    thread_id: str | None = None,
) -> bool:
    normalized_payload = jsonable(payload)
    normalized_thread_id = str(thread_id) if thread_id is not None else None
    dedupe_key = listener_event_dedupe_key(
        event_type,
        normalized_thread_id,
        normalized_payload,
    )
    with database.session_factory() as session:
        if dedupe_key:
            existing = session.scalar(
                select(Event.id).where(
                    Event.account_id == account_id,
                    Event.event_type == event_type,
                    Event.dedupe_key == dedupe_key,
                )
            )
            if existing is not None:
                _mark_account_online(session, account_id)
                session.commit()
                return False
        session.add(
            Event(
                account_id=account_id,
                event_type=event_type,
                thread_id=normalized_thread_id,
                dedupe_key=dedupe_key,
                payload=normalized_payload,
            )
        )
        _mark_account_online(session, account_id)
        try:
            session.commit()
            return True
        except IntegrityError:
            session.rollback()

    # Another callback may have inserted the same event between the lookup and
    # commit. Keep the listener healthy and still refresh the account heartbeat.
    with database.session_factory() as session:
        _mark_account_online(session, account_id)
        session.commit()
    return False


def record_listener_failure(
    database: Database,
    account_id: str,
    error: Exception | str,
    *,
    timestamp=None,
) -> None:
    error_text = str(error)
    with database.session_factory() as session:
        account = session.get(ZaloAccount, account_id)
        if account:
            account.status = "error"
            account.last_error = error_text[:4000]
            account.listener_enabled = False
        session.add(
            Event(
                account_id=account_id,
                event_type="listener.error",
                payload={
                    "error": error_text,
                    "timestamp": timestamp,
                    "listener_disabled": True,
                    "requires_admin_credentials_update": True,
                },
            )
        )
        session.commit()


class ManagedZaloClient(ZaloAPI):
    def __init__(self, *, account_id: str, database: Database, **kwargs):
        self.managed_account_id = account_id
        self.database = database
        super().__init__(**kwargs)

    def _event(self, event_type: str, payload: dict, thread_id: str | None = None) -> None:
        store_listener_event(
            self.database,
            self.managed_account_id,
            event_type,
            payload,
            thread_id,
        )

    def onListening(self):
        self._event("listener.connected", {"status": "connected"})
        self._sync_group_metadata()

    def _sync_group_metadata(self) -> None:
        cutoff = utcnow() - timedelta(hours=1)
        with self.database.session_factory() as session:
            recent_snapshot = session.scalar(
                select(Event.id)
                .where(
                    Event.account_id == self.managed_account_id,
                    Event.event_type == "group.metadata.synced",
                    Event.created_at >= cutoff,
                )
                .order_by(desc(Event.id))
                .limit(1)
            )
        if recent_snapshot:
            return

        try:
            group_versions = jsonable(self.fetchAllGroups())
            group_ids = (
                group_versions.get("gridVerMap", {})
                if isinstance(group_versions, dict)
                else {}
            )
            if not isinstance(group_ids, dict):
                group_ids = {}
            group_info = jsonable(self.fetchGroupInfo(group_ids)) if group_ids else {}
            info_map = (
                group_info.get("gridInfoMap", {})
                if isinstance(group_info, dict)
                else {}
            )
            groups = {}
            if isinstance(info_map, dict):
                for group_id, raw in info_map.items():
                    if not isinstance(raw, dict):
                        continue
                    name = str(raw.get("name") or "").strip()
                    avatar_url = str(raw.get("avt") or raw.get("fullAvt") or "").strip()
                    member_count = raw.get("totalMember")
                    groups[str(group_id)] = {
                        "name": name or f"Nhóm Zalo • {str(group_id)[-6:]}",
                        "avatar_url": (
                            avatar_url
                            if avatar_url.startswith(("https://", "http://"))
                            else None
                        ),
                        "member_count": (
                            int(member_count)
                            if isinstance(member_count, (int, float, str))
                            and str(member_count).isdigit()
                            else None
                        ),
                    }
            self._event(
                "group.metadata.synced",
                {"groups": groups, "group_count": len(groups)},
            )
        except Exception as exc:
            logger.warning(
                "Could not sync group metadata for account %s: %s",
                self.managed_account_id,
                exc,
            )

    def onMessage(
        self,
        mid=None,
        author_id=None,
        message=None,
        message_object=None,
        thread_id=None,
        thread_type=ThreadType.USER,
    ):
        self._event(
            "message.received",
            {
                "message_id": mid,
                "author_id": author_id,
                "message": jsonable(message),
                "message_object": jsonable(message_object),
                "thread_type": getattr(thread_type, "name", str(thread_type)),
            },
            str(thread_id) if thread_id is not None else None,
        )

    def onEvent(self, event_data, event_type):
        self._event(
            "group.event",
            {"event_type": getattr(event_type, "name", str(event_type)), "data": jsonable(event_data)},
        )

    def onMessageDelivered(self, msg_ids, thread_id, thread_type, ts):
        self._event(
            "message.delivered",
            {"message_ids": jsonable(msg_ids), "thread_type": getattr(thread_type, "name", str(thread_type)), "timestamp": ts},
            str(thread_id),
        )

    def onMarkedSeen(self, msg_ids, thread_id, thread_type, ts):
        self._event(
            "message.seen",
            {"message_ids": jsonable(msg_ids), "thread_type": getattr(thread_type, "name", str(thread_type)), "timestamp": ts},
            str(thread_id),
        )

    def onErrorCallBack(self, error, ts=None):
        record_listener_failure(
            self.database,
            self.managed_account_id,
            error,
            timestamp=ts,
        )


@dataclass
class ListenerHandle:
    thread: threading.Thread
    client: ManagedZaloClient | None = None


class ListenerSupervisor:
    def __init__(self):
        self.settings = get_settings()
        self.settings.ensure_directories()
        self.database = Database(self.settings, pool_size=1, max_overflow=0)
        self.secret_box = SecretBox(self.settings)
        self.stop_event = threading.Event()
        self.handles: dict[str, ListenerHandle] = {}
        self.lock = threading.Lock()

    def stop(self, *_args) -> None:
        self.stop_event.set()
        with self.lock:
            handles = list(self.handles.values())
        for handle in handles:
            if handle.client:
                handle.client.stopListening()

    def run(self) -> None:
        while not self.stop_event.is_set():
            self.reconcile()
            self.stop_event.wait(10)
        for handle in list(self.handles.values()):
            handle.thread.join(timeout=10)
        self.database.dispose()

    def reconcile(self) -> None:
        with self.database.session_factory() as session:
            desired = {
                account.id
                for account in session.scalars(
                    select(ZaloAccount).where(
                        ZaloAccount.enabled.is_(True),
                        ZaloAccount.listener_enabled.is_(True),
                    )
                ).all()
            }
        with self.lock:
            for account_id, handle in list(self.handles.items()):
                if account_id not in desired or not handle.thread.is_alive():
                    if handle.client:
                        handle.client.stopListening()
                    self.handles.pop(account_id, None)
            for account_id in desired - set(self.handles):
                thread = threading.Thread(
                    target=self._run_account,
                    args=(account_id,),
                    daemon=True,
                    name=f"zalo-listener-{account_id[:8]}",
                )
                self.handles[account_id] = ListenerHandle(thread=thread)
                thread.start()

    def _run_account(self, account_id: str) -> None:
        client = None
        try:
            with self.database.session_factory() as session:
                account = session.get(ZaloAccount, account_id)
                if account is None:
                    return
                cookies = self.secret_box.decrypt_json(account.cookie_cipher)
                imei = self.secret_box.decrypt_text(account.imei_cipher)
                user_agent = account.user_agent
            client = ManagedZaloClient(
                account_id=account_id,
                database=self.database,
                phone="cookie-session",
                password="cookie-session",
                imei=imei,
                session_cookies=cookies,
                user_agent=user_agent,
                auto_login=True,
            )
            with self.lock:
                handle = self.handles.get(account_id)
                if handle:
                    handle.client = client
            if self.stop_event.is_set():
                return
            client.listen(type="websocket", run_forever=False, reconnect=5)
        except Exception as exc:
            logger.exception("Listener failed for account %s", account_id)
            record_listener_failure(self.database, account_id, exc)
        finally:
            if client:
                client.stopListening()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    supervisor = ListenerSupervisor()
    signal.signal(signal.SIGINT, supervisor.stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, supervisor.stop)
    supervisor.run()


if __name__ == "__main__":
    main()
