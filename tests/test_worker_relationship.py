from __future__ import annotations

from control_center.db import Database
from control_center.models import Job, ZaloAccount, ZaloContactProfile
from control_center.queue import enqueue_job
from control_center.worker import Worker


class SuccessfulRelationshipConnector:
    def execute(self, _session, _account, action, payload):
        assert action == "blockUser"
        assert payload["userId"] == "person-worker-1"
        return {"ok": True}


def test_worker_updates_relationship_only_after_connector_success(settings, monkeypatch):
    settings.ensure_directories()
    database = Database(settings)
    database.create_schema()
    with database.session_factory() as session:
        account = ZaloAccount(
            name="Worker relationship account",
            cookie_cipher="unused",
            imei_cipher="unused",
            status="online",
            enabled=True,
        )
        session.add(account)
        session.commit()
        account_id = account.id
        job = enqueue_job(
            session,
            account_id=account_id,
            action="blockUser",
            payload={"userId": "person-worker-1"},
            created_by_id=None,
            max_attempts=1,
        )
        job_id = job.id
    database.dispose()

    monkeypatch.setattr("control_center.worker.get_settings", lambda: settings)
    worker = Worker()
    worker.connector = SuccessfulRelationshipConnector()
    assert worker.run_once() is True

    with worker.database.session_factory() as session:
        job = session.get(Job, job_id)
        profile = session.get(
            ZaloContactProfile,
            (account_id, "person-worker-1"),
        )
        assert job.status == "succeeded"
        assert profile.relationship_status == "blocked"
        assert profile.blocked_by_me is True
    worker.database.dispose()
