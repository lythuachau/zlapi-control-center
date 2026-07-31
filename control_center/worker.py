from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import threading

from .automations import mark_automation_run
from .config import get_settings
from .connector import UnsupportedActionError, ZaloConnector
from .db import Database
from .history_sync import collect_recent_history, queue_history_sync
from .listener import store_listener_event
from .models import AuditLog, Event, Job, ZaloAccount
from .process_lock import ProcessAlreadyRunningError, ProcessSingleton
from .profiles import apply_relationship_action
from .queue import claim_job, complete_job, fail_job, recover_stale_jobs
from .security import SecretBox

logger = logging.getLogger("zlapi.worker")


class Worker:
    def __init__(self):
        self.settings = get_settings()
        self.settings.ensure_directories()
        self.database = Database(self.settings, pool_size=2, max_overflow=1)
        self.connector = ZaloConnector(self.settings, SecretBox(self.settings))
        self.stop_event = threading.Event()
        self.worker_id = self.settings.worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self.idle_delay = 0.5

    def stop(self, *_args) -> None:
        self.stop_event.set()

    def run(self, once: bool = False) -> None:
        with self.database.session_factory() as session:
            recovered = recover_stale_jobs(session)
            if recovered:
                logger.warning("Recovered %s stale jobs", recovered)
        while not self.stop_event.is_set():
            worked = self.run_once()
            if once:
                return
            if worked:
                self.idle_delay = 0.5
                continue
            self.stop_event.wait(self.idle_delay)
            self.idle_delay = min(5.0, self.idle_delay * 1.5)

    def run_once(self) -> bool:
        with self.database.session_factory() as session:
            job = claim_job(session, self.worker_id, self.settings.is_postgres)
            if job is None:
                return False
            job_id = job.id
            action = job.action
            account = session.get(ZaloAccount, job.account_id)
            if account is None:
                fail_job(session, job_id, "Zalo account no longer exists")
                self._emit(session, "job.failed", job, {"error": "Account missing"})
                return True
            try:
                if action == "__test_account__":
                    result = self.connector.test_account(session, account)
                    queue_history_sync(
                        session,
                        account_id=account.id,
                        reason="account_verified",
                        created_by_id=job.created_by_id,
                    )
                elif action == "__chat_history_sync__":
                    result = self._sync_recent_history(session, account)
                elif action in {
                    "__chat_reaction__",
                    "__chat_automation_reaction__",
                }:
                    result = self.connector.react_to_chat_message(
                        session,
                        account,
                        job.payload,
                    )
                    if isinstance(result, dict) and result.get("fallback") == "local":
                        context = (job.payload or {}).get("_chat_context")
                        context = context if isinstance(context, dict) else {}
                        session.add(
                            Event(
                                account_id=account.id,
                                event_type="chat.reaction.local",
                                thread_id=str((job.payload or {}).get("thread_id") or ""),
                                payload={
                                    "target_event_id": context.get("target_event_id"),
                                    "target_message_id": context.get("target_message_id"),
                                    "reaction_id": context.get("reaction_id"),
                                    "glyph": context.get("glyph"),
                                    "label": context.get("label"),
                                    "actor_id": context.get("actor_id") or job.created_by_id,
                                    "action": "add",
                                    "fallback_from_job_id": job.id,
                                },
                            )
                        )
                        session.commit()
                elif action == "__chat_forward__":
                    result = self.connector.forward_chat_message(
                        session,
                        account,
                        job.payload,
                    )
                elif action == "__chat_media__":
                    result = self.connector.send_chat_media(
                        session,
                        account,
                        job.payload,
                    )
                elif action == "__chat_rich_media__":
                    result = self.connector.send_chat_rich_media(
                        session,
                        account,
                        job.payload,
                    )
                elif action == "__chat_automation_sticker__":
                    result = self.connector.send_chat_automation_sticker(
                        session,
                        account,
                        job.payload,
                    )
                elif action == "__chat_group_info__":
                    result = self.connector.fetch_chat_group_info(
                        session,
                        account,
                        job.payload,
                    )
                elif action == "__chat_profiles__":
                    result = self.connector.fetch_chat_profiles(
                        session,
                        account,
                        job.payload,
                    )
                elif action == "__chat_group_pins__":
                    result = self.connector.fetch_chat_group_pins(
                        session,
                        account,
                        job.payload,
                    )
                elif action == "__chat_group_notes__":
                    result = self.connector.fetch_chat_group_notes(
                        session,
                        account,
                        job.payload,
                    )
                elif action == "__chat_group_polls__":
                    result = self.connector.fetch_chat_group_polls(
                        session,
                        account,
                        job.payload,
                    )
                elif action == "__chat_poll_detail__":
                    result = self.connector.fetch_chat_group_poll_detail(
                        session,
                        account,
                        job.payload,
                    )
                else:
                    result = self.connector.execute(session, account, action, job.payload)
                if action in {
                    "sendFriendRequest",
                    "acceptFriendRequest",
                    "blockUser",
                    "unblockUser",
                }:
                    user_id = str((job.payload or {}).get("userId") or "").strip()
                    if user_id:
                        apply_relationship_action(
                            session,
                            account_id=account.id,
                            user_id=user_id,
                            action=action,
                        )
                complete_job(session, job_id, result)
                mark_automation_run(session, job_id, "succeeded")
                refreshed = session.get(Job, job_id)
                self._emit(session, "job.succeeded", refreshed, {"result": result})
                logger.info("Job %s (%s) succeeded", job_id, action)
            except Exception as exc:
                retryable = not isinstance(exc, (UnsupportedActionError, TypeError, ValueError))
                state = fail_job(session, job_id, str(exc), retryable=retryable)
                mark_automation_run(
                    session,
                    job_id,
                    "queued" if state == "queued" else "failed",
                    error=str(exc),
                )
                refreshed = session.get(Job, job_id)
                self._emit(
                    session,
                    "job.retry" if state == "queued" else "job.failed",
                    refreshed,
                    {"error": str(exc), "status": state},
                )
                logger.exception("Job %s (%s) failed", job_id, action)
            return True

    def _sync_recent_history(self, session, account: ZaloAccount) -> dict:
        """Import a bounded recent snapshot without replaying chat automations."""

        client = self.connector.client_for(account)
        account_uid = self.connector.identity_from_payload({}, client.uid)
        self.connector.validate_identity(session, account, account_uid)
        if account.zalo_uid != account_uid:
            account.zalo_uid = account_uid
            session.commit()

        events = collect_recent_history(client, account_uid=account_uid)
        imported = 0
        duplicates = 0
        for event in events:
            created = store_listener_event(
                self.database,
                account.id,
                "message.received",
                event.payload,
                event.thread_id,
                created_at=event.occurred_at,
                dedupe_key_override=event.dedupe_key,
                process_message_side_effects=False,
                update_account_heartbeat=False,
            )
            if created:
                imported += 1
            else:
                duplicates += 1
        return {
            "source": "getLastMsgs",
            "best_effort": True,
            "scanned": len(events),
            "imported": imported,
            "duplicates": duplicates,
        }

    @staticmethod
    def _emit(session, event_type: str, job: Job, detail: dict) -> None:
        event = Event(
            account_id=job.account_id,
            event_type=event_type,
            payload={"job_id": job.id, "action": job.action, **detail},
        )
        audit = AuditLog(
            actor_type="system",
            action=event_type,
            resource_type="job",
            resource_id=job.id,
            details={"action": job.action, "attempts": job.attempts},
        )
        session.add_all([event, audit])
        session.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ZLAPI PostgreSQL job worker")
    parser.add_argument("--once", action="store_true", help="Process at most one queued job")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()
    settings.ensure_directories()
    singleton = ProcessSingleton(
        settings.data_dir / "run" / "worker.lock",
        role="worker",
    )
    try:
        singleton.acquire()
    except ProcessAlreadyRunningError as exc:
        logger.error("%s; refusing to start a duplicate service", exc)
        return

    try:
        worker = Worker()
        signal.signal(signal.SIGINT, worker.stop)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, worker.stop)
        worker.run(once=args.once)
    finally:
        singleton.release()


if __name__ == "__main__":
    main()
