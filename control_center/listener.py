from __future__ import annotations

import hashlib
import logging
import signal
import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.exc import TimeoutError as SATimeoutError
from sqlalchemy.orm import Session

from zlapi import ThreadType, ZaloAPI

from .automations import queue_mention_sticker_automation
from .config import get_settings
from .connector import jsonable
from .db import Database
from .history_sync import queue_history_sync
from .models import Event, Job, ZaloAccount, utcnow
from .process_lock import ProcessAlreadyRunningError, ProcessSingleton
from .profiles import profile_ids_needing_refresh
from .security import SecretBox

logger = logging.getLogger("zlapi.listener")
PROFILE_SYNC_BATCH_SIZE = 100
PROFILE_SYNC_COALESCE_SECONDS = 2
DATABASE_RETRY_INITIAL_SECONDS = 5.0
DATABASE_RETRY_MAX_SECONDS = 60.0

# Serialize DB checkouts across supervisor + per-account callback threads.
# Listener is intentionally budgeted at pool_size=1; concurrent session_factory()
# without this lock raises QueuePool "size 1 overflow 0" timeouts.
_LISTENER_DB_LOCK = threading.RLock()


@contextmanager
def listener_db_session(database: Database) -> Generator[Session, None, None]:
    with _LISTENER_DB_LOCK:
        with database.session_factory() as session:
            yield session


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


def queue_interaction_profile_sync(session, account: ZaloAccount, payload: dict) -> Job | None:
    author_id = str(payload.get("author_id") or "").strip()
    due_ids = profile_ids_needing_refresh(
        session,
        account_id=account.id,
        user_ids=[author_id],
        account_uid=account.zalo_uid,
    )
    if not due_ids:
        return None
    author_id = due_ids[0]
    active_jobs = session.scalars(
        select(Job)
        .where(
            Job.account_id == account.id,
            Job.action == "__chat_profiles__",
            Job.status.in_(("queued", "running")),
        )
        .order_by(desc(Job.created_at))
        .limit(5)
    ).all()
    queued_target = None
    for job in active_jobs:
        job_ids = list(
            dict.fromkeys(
                str(value).strip()
                for value in (job.payload or {}).get("user_ids", [])
                if str(value).strip()
            )
        )
        if author_id in job_ids:
            return job
        if job.status == "queued" and len(job_ids) < PROFILE_SYNC_BATCH_SIZE:
            queued_target = (job, job_ids)
    if queued_target:
        job, job_ids = queued_target
        job.payload = {
            **(job.payload or {}),
            "user_ids": [*job_ids, author_id],
        }
        session.flush()
        return job

    job = Job(
        account_id=account.id,
        action="__chat_profiles__",
        payload={
            "user_ids": [author_id],
            "_chat_context": {"profile_trigger": "interaction"},
        },
        created_by_id=None,
        priority=65,
        max_attempts=2,
        next_run_at=utcnow() + timedelta(seconds=PROFILE_SYNC_COALESCE_SECONDS),
    )
    session.add(job)
    session.flush()
    return job


def store_listener_event(
    database: Database,
    account_id: str,
    event_type: str,
    payload: dict,
    thread_id: str | None = None,
    *,
    created_at=None,
    dedupe_key_override: str | None = None,
    process_message_side_effects: bool = True,
    update_account_heartbeat: bool = True,
) -> bool:
    normalized_payload = jsonable(payload)
    normalized_thread_id = str(thread_id) if thread_id is not None else None
    dedupe_key = dedupe_key_override or listener_event_dedupe_key(
        event_type,
        normalized_thread_id,
        normalized_payload,
    )
    with listener_db_session(database) as session:
        if dedupe_key:
            existing = session.scalar(
                select(Event.id).where(
                    Event.account_id == account_id,
                    Event.event_type == event_type,
                    Event.dedupe_key == dedupe_key,
                )
            )
            if existing is not None:
                if update_account_heartbeat:
                    _mark_account_online(session, account_id)
                session.commit()
                return False
        event_values = {
            "account_id": account_id,
            "event_type": event_type,
            "thread_id": normalized_thread_id,
            "dedupe_key": dedupe_key,
            "payload": normalized_payload,
        }
        if created_at is not None:
            event_values["created_at"] = created_at
        event = Event(
            **event_values,
        )
        session.add(event)
        try:
            session.flush()
            if update_account_heartbeat:
                _mark_account_online(session, account_id)
            if event_type == "message.received" and process_message_side_effects:
                account = session.get(ZaloAccount, account_id)
                if account:
                    try:
                        with session.begin_nested():
                            queue_mention_sticker_automation(
                                session,
                                account,
                                event,
                            )
                    except Exception as exc:
                        logger.warning(
                            "Could not evaluate chat automation for account %s: %s",
                            account_id,
                            exc,
                        )
                    try:
                        with session.begin_nested():
                            queue_interaction_profile_sync(
                                session,
                                account,
                                normalized_payload,
                            )
                    except Exception as exc:
                        logger.warning(
                            "Could not queue interaction profile sync for account %s: %s",
                            account_id,
                            exc,
                        )
            session.commit()
            return True
        except IntegrityError:
            session.rollback()

    # Another callback may have inserted the same event between the lookup and
    # commit. Keep the listener healthy and still refresh the account heartbeat.
    if update_account_heartbeat:
        with listener_db_session(database) as session:
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
    duplicate_connection = (
        "another web connection is active" in error_text.casefold()
    )
    with listener_db_session(database) as session:
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
                    "requires_admin_credentials_update": not duplicate_connection,
                    "failure_kind": (
                        "duplicate_listener" if duplicate_connection else "session_or_network"
                    ),
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
        self._queue_recent_history_sync()
        self._sync_group_metadata()

    def _queue_recent_history_sync(self) -> None:
        try:
            with listener_db_session(self.database) as session:
                queue_history_sync(
                    session,
                    account_id=self.managed_account_id,
                    reason="listener_connected",
                )
                session.commit()
        except Exception as exc:
            logger.warning(
                "Could not queue history sync for account %s: %s",
                self.managed_account_id,
                exc,
            )

    def _sync_group_metadata(self) -> None:
        cutoff = utcnow() - timedelta(hours=1)
        with listener_db_session(self.database) as session:
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
    generation: int
    cancel_event: threading.Event
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
        self.next_generation = 0

    def stop(self, *_args) -> None:
        self.stop_event.set()
        clients = []
        with self.lock:
            for handle in self.handles.values():
                handle.cancel_event.set()
                if handle.client:
                    clients.append(handle.client)
        for client in clients:
            client.stopListening()

    def _next_generation(self) -> int:
        self.next_generation += 1
        return self.next_generation

    def _new_handle(self, account_id: str) -> ListenerHandle:
        generation = self._next_generation()
        cancel_event = threading.Event()
        thread = threading.Thread(
            target=self._run_account,
            args=(account_id, generation, cancel_event),
            daemon=True,
            name=f"zalo-listener-{account_id[:8]}-{generation}",
        )
        return ListenerHandle(
            thread=thread,
            generation=generation,
            cancel_event=cancel_event,
        )

    @staticmethod
    def _stop_clients(clients: list[ManagedZaloClient]) -> None:
        for client in clients:
            client.stopListening()

    def _cancel_handle(
        self,
        handle: ListenerHandle,
        clients_to_stop: list[ManagedZaloClient],
    ) -> None:
        if handle.cancel_event.is_set():
            return
        handle.cancel_event.set()
        if handle.client:
            clients_to_stop.append(handle.client)

    def _handle_is_current(
        self,
        account_id: str,
        generation: int,
        cancel_event: threading.Event,
    ) -> ListenerHandle | None:
        handle = self.handles.get(account_id)
        if (
            handle is None
            or handle.generation != generation
            or handle.cancel_event is not cancel_event
        ):
            return None
        return handle

    def _should_start_client(
        self,
        account_id: str,
        generation: int,
        cancel_event: threading.Event,
        client: ManagedZaloClient,
    ) -> bool:
        with self.lock:
            handle = self._handle_is_current(
                account_id,
                generation,
                cancel_event,
            )
            if (
                handle is None
                or cancel_event.is_set()
                or self.stop_event.is_set()
            ):
                return False
            handle.client = client
            return True

    def _clear_client(
        self,
        account_id: str,
        generation: int,
        cancel_event: threading.Event,
        client: ManagedZaloClient,
    ) -> None:
        with self.lock:
            handle = self._handle_is_current(
                account_id,
                generation,
                cancel_event,
            )
            if handle and handle.client is client:
                handle.client = None

    def _join_handles(self) -> None:
        with self.lock:
            handles = list(self.handles.values())
        for handle in handles:
            handle.thread.join(timeout=10)

    def _cancel_all_handles(self) -> None:
        clients = []
        with self.lock:
            for handle in self.handles.values():
                self._cancel_handle(handle, clients)
        self._stop_clients(clients)

    def run(self) -> None:
        database_retry_delay = DATABASE_RETRY_INITIAL_SECONDS
        try:
            while not self.stop_event.is_set():
                try:
                    self.reconcile()
                except SATimeoutError as exc:
                    # Pool contention is not a dead engine; disposing would drop
                    # connections still checked out by account callback threads.
                    logger.warning(
                        "Database pool busy (%s); retrying listener in %.1f seconds",
                        type(exc).__name__,
                        database_retry_delay,
                    )
                    self.stop_event.wait(database_retry_delay)
                    database_retry_delay = min(
                        DATABASE_RETRY_MAX_SECONDS,
                        database_retry_delay * 2,
                    )
                    continue
                except SQLAlchemyError as exc:
                    self.database.dispose()
                    logger.warning(
                        "Database unavailable (%s); retrying listener in %.1f seconds",
                        type(exc).__name__,
                        database_retry_delay,
                    )
                    self.stop_event.wait(database_retry_delay)
                    database_retry_delay = min(
                        DATABASE_RETRY_MAX_SECONDS,
                        database_retry_delay * 2,
                    )
                    continue
                database_retry_delay = DATABASE_RETRY_INITIAL_SECONDS
                self.stop_event.wait(10)
        finally:
            self._cancel_all_handles()
            self._join_handles()
            self.database.dispose()

    def reconcile(self) -> None:
        with listener_db_session(self.database) as session:
            desired = {
                account.id
                for account in session.scalars(
                    select(ZaloAccount).where(
                        ZaloAccount.enabled.is_(True),
                        ZaloAccount.listener_enabled.is_(True),
                        ZaloAccount.zalo_uid.is_not(None),
                        ZaloAccount.status == "online",
                    )
                ).all()
            }
        clients_to_stop: list[ManagedZaloClient] = []
        with self.lock:
            for account_id, handle in list(self.handles.items()):
                if not handle.thread.is_alive():
                    self.handles.pop(account_id, None)
                    continue
                if account_id not in desired:
                    self._cancel_handle(handle, clients_to_stop)
            for account_id in desired - set(self.handles):
                handle = self._new_handle(account_id)
                self.handles[account_id] = handle
                handle.thread.start()
        self._stop_clients(clients_to_stop)

    def _run_account(
        self,
        account_id: str,
        generation: int,
        cancel_event: threading.Event,
    ) -> None:
        client = None
        try:
            if self.stop_event.is_set() or cancel_event.is_set():
                return
            with listener_db_session(self.database) as session:
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
            if not self._should_start_client(
                account_id,
                generation,
                cancel_event,
                client,
            ):
                return
            # A newly-created client starts with a clear stop event. Do not
            # reset it here: cancellation may race with the end of auto-login.
            client.listen(
                type="websocket",
                run_forever=False,
                reconnect=5,
                reset_stop=False,
            )
        except Exception as exc:
            if self.stop_event.is_set() or cancel_event.is_set():
                logger.info("Listener stopped for account %s", account_id)
            else:
                logger.exception("Listener failed for account %s", account_id)
                record_listener_failure(self.database, account_id, exc)
        finally:
            if client:
                client.stopListening()
                self._clear_client(
                    account_id,
                    generation,
                    cancel_event,
                    client,
                )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()
    settings.ensure_directories()
    singleton = ProcessSingleton(
        settings.data_dir / "run" / "listener.lock",
        role="listener",
    )
    try:
        singleton.acquire()
    except ProcessAlreadyRunningError as exc:
        logger.error("%s; refusing to start a duplicate service", exc)
        return

    try:
        supervisor = ListenerSupervisor()
        signal.signal(signal.SIGINT, supervisor.stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, supervisor.stop)
        supervisor.run()
    finally:
        singleton.release()


if __name__ == "__main__":
    main()
