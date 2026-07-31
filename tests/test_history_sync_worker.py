from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from control_center.connector import ZaloConnector
from control_center.db import Database
from control_center.history_sync import HISTORY_SYNC_ACTION, queue_history_sync
from control_center.models import Event, Job, ZaloAccount
from control_center.worker import Worker


class _HistoryClient:
    uid = "self-history"

    def getLastMsgs(self):
        return {
            "msgs": [
                {
                    "msgId": "recent-user-1",
                    "uidFrom": "friend-history",
                    "idTo": "0",
                    "content": "missed while offline",
                    "ts": "1785456000000",
                }
            ],
            "groupMsgs": [
                {
                    "msgId": "recent-group-1",
                    "uidFrom": "member-history",
                    "idTo": "group-history",
                    "content": "group gap",
                    "ts": "1785456001000",
                }
            ],
        }


def _history_account(session) -> ZaloAccount:
    account = ZaloAccount(
        name="History reconnect",
        zalo_uid="self-history",
        cookie_cipher="unused-cookie",
        imei_cipher="unused-imei",
        status="online",
        enabled=True,
    )
    session.add(account)
    session.commit()
    return account


def test_history_sync_jobs_are_coalesced_per_account(settings):
    database = Database(settings)
    database.create_schema()
    with database.session_factory() as session:
        account = _history_account(session)
        first = queue_history_sync(
            session,
            account_id=account.id,
            reason="session_updated",
        )
        second = queue_history_sync(
            session,
            account_id=account.id,
            reason="listener_connected",
        )
        assert second.id == first.id
        assert first.action == HISTORY_SYNC_ACTION
        first.status = "succeeded"
        session.commit()

        third = queue_history_sync(
            session,
            account_id=account.id,
            reason="later_reconnect",
        )
        session.commit()
        assert third.id != first.id
        assert third.payload["reason"] == "later_reconnect"
    database.dispose()


def test_worker_backfill_is_idempotent_and_does_not_replay_side_effects(settings):
    database = Database(settings)
    database.create_schema()
    connector = ZaloConnector(settings, object())
    connector.client_for = lambda _account: _HistoryClient()
    worker = Worker.__new__(Worker)
    worker.database = database
    worker.connector = connector

    with database.session_factory() as session:
        account = _history_account(session)
        account_id = account.id
        first = worker._sync_recent_history(session, account)
        assert first == {
            "source": "getLastMsgs",
            "best_effort": True,
            "scanned": 2,
            "imported": 2,
            "duplicates": 0,
        }

    with database.session_factory() as session:
        account = session.get(ZaloAccount, account_id)
        account.status = "error"
        account.last_error = "session expired after sync was queued"
        account.listener_enabled = False
        session.commit()
        second = worker._sync_recent_history(session, account)
        assert second["imported"] == 0
        assert second["duplicates"] == 2

    with database.session_factory() as session:
        events = session.scalars(
            select(Event)
            .where(
                Event.account_id == account_id,
                Event.event_type == "message.received",
            )
            .order_by(Event.created_at)
        ).all()
        assert [event.thread_id for event in events] == [
            "friend-history",
            "group-history",
        ]
        assert events[0].created_at.replace(tzinfo=UTC) == datetime(
            2026, 7, 31, 0, 0, tzinfo=UTC
        )
        assert all(event.payload["source"] == "history_backfill" for event in events)
        account = session.get(ZaloAccount, account_id)
        assert account.status == "error"
        assert account.listener_enabled is False
        assert account.last_error == "session expired after sync was queued"
        side_effect_jobs = session.scalars(
            select(Job).where(
                Job.account_id == account_id,
                Job.action.in_((
                    "__chat_profiles__",
                    "__chat_automation_sticker__",
                    "__chat_automation_reaction__",
                )),
            )
        ).all()
        assert side_effect_jobs == []
    database.dispose()
