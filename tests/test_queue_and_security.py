from __future__ import annotations

from datetime import timedelta

import pytest

from control_center.config import Settings
from control_center.db import Database
from control_center.maintenance import archive_old_events, cleanup_uploads
from control_center.models import Event, MediaUpload, ZaloAccount, utcnow
from control_center.queue import claim_job, complete_job, enqueue_job
from control_center.security import SecretBox


def test_secret_box_round_trip(settings):
    box = SecretBox(settings)
    encrypted = box.encrypt_json({"zpsid": "never-plain"})
    assert "never-plain" not in encrypted
    assert box.decrypt_json(encrypted) == {"zpsid": "never-plain"}
    blob = box.encrypt_bytes(b"archive payload")
    assert b"archive payload" not in blob
    assert box.decrypt_bytes(blob) == b"archive payload"


def test_queue_claims_a_job_once(settings):
    database = Database(settings)
    database.create_schema()
    box = SecretBox(settings)
    with database.session_factory() as session:
        account = ZaloAccount(
            name="Queue account",
            cookie_cipher=box.encrypt_json({"a": "b"}),
            imei_cipher=box.encrypt_text("imei-123"),
        )
        session.add(account)
        session.commit()
        job = enqueue_job(
            session,
            account_id=account.id,
            action="fetchAllFriends",
            payload={},
            created_by_id=None,
        )
        claimed = claim_job(session, "test-worker", postgres=False)
        assert claimed.id == job.id
        assert claim_job(session, "second-worker", postgres=False) is None
        complete_job(session, job.id, {"ok": True})
        assert session.get(type(job), job.id).status == "succeeded"


def test_archive_retains_metadata_and_encrypts_payload(settings):
    database = Database(settings)
    database.create_schema()
    box = SecretBox(settings)
    with database.session_factory() as session:
        session.add(
            Event(
                event_type="message.received",
                payload={"text": "old secret message"},
                created_at=utcnow() - timedelta(days=120),
            )
        )
        session.commit()
    assert archive_old_events(database, settings, box) == 1
    with database.session_factory() as session:
        event = session.query(Event).one()
        assert event.payload["archived"] is True
        archive_id = event.payload["archive_id"]
        archive_file = next(settings.archive_dir.glob("*.aes"))
        assert b"old secret message" not in archive_file.read_bytes()
        assert archive_id


def test_cleanup_never_removes_outside_upload_root(settings, tmp_path):
    database = Database(settings)
    database.create_schema()
    settings.ensure_directories()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    with database.session_factory() as session:
        session.add(
            MediaUpload(
                id="expired",
                original_name="outside.txt",
                stored_path=str(outside),
                size_bytes=4,
                expires_at=utcnow() - timedelta(hours=1),
            )
        )
        session.commit()
    assert cleanup_uploads(database, settings) == 1
    assert outside.exists()


def test_production_requires_postgres_tls(tmp_path):
    with pytest.raises(ValueError, match="external PostgreSQL"):
        Settings(
            app_env="production",
            database_url="sqlite:///local.db",
            data_dir=tmp_path,
            session_secret="x" * 40,
            cookie_secure=True,
        )
    with pytest.raises(ValueError, match="sslmode=require"):
        Settings(
            app_env="production",
            database_url="postgresql://user:pass@example.com/database",
            data_dir=tmp_path,
            session_secret="x" * 40,
            cookie_secure=True,
        )
