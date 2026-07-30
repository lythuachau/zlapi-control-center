from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import requests
import zstandard
from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from .config import Settings, get_settings
from .db import Database
from .models import Archive, Event, Job, MediaUpload, ZaloAccount, utcnow
from .security import SecretBox, safe_child_path

logger = logging.getLogger("zlapi.maintenance")


def archive_old_events(database: Database, settings: Settings, box: SecretBox) -> int:
    cutoff = utcnow() - timedelta(days=settings.archive_after_days)
    archived = 0
    while True:
        with database.session_factory() as session:
            events = session.scalars(
                select(Event)
                .where(Event.created_at < cutoff, Event.archived_at.is_(None))
                .order_by(Event.id)
                .limit(1000)
            ).all()
            if not events:
                break
            lines = [
                json.dumps(
                    {
                        "id": event.id,
                        "account_id": event.account_id,
                        "event_type": event.event_type,
                        "thread_id": event.thread_id,
                        "payload": event.payload,
                        "created_at": event.created_at.isoformat(),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                for event in events
            ]
            raw = b"\n".join(lines) + b"\n"
            compressed = zstandard.ZstdCompressor(level=10).compress(raw)
            encrypted = box.encrypt_bytes(compressed)
            start = events[0].created_at
            end = events[-1].created_at
            filename = f"events-{events[0].id}-{events[-1].id}-{utcnow():%Y%m%dT%H%M%SZ}.jsonl.zst.aes"
            target = settings.archive_dir / filename
            target.write_bytes(encrypted)
            digest = hashlib.sha256(encrypted).hexdigest()
            archive = Archive(
                path=str(target),
                sha256=digest,
                record_count=len(events),
                range_start=start,
                range_end=end,
                size_bytes=len(encrypted),
                encrypted=True,
            )
            session.add(archive)
            session.flush()
            for event in events:
                event.payload = {"archived": True, "archive_id": archive.id}
                event.archived_at = utcnow()
            session.commit()
            archived += len(events)
    return archived


def cleanup_uploads(database: Database, settings: Settings) -> int:
    removed = 0
    with database.session_factory() as session:
        uploads = session.scalars(
            select(MediaUpload).where(MediaUpload.expires_at < utcnow())
        ).all()
        for upload in uploads:
            path = Path(upload.stored_path)
            if safe_child_path(settings.upload_dir, path):
                path.unlink(missing_ok=True)
            session.delete(upload)
            removed += 1
        session.commit()
    return removed


def _pg_environment(settings: Settings) -> dict[str, str]:
    url = make_url(settings.normalized_database_url)
    required = {
        "PGHOST": url.host,
        "PGPORT": str(url.port) if url.port else None,
        "PGUSER": url.username,
        "PGPASSWORD": url.password,
        "PGDATABASE": url.database,
        "PGSSLMODE": url.query.get("sslmode", "require"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(f"PostgreSQL URL is missing connection fields: {', '.join(missing)}")
    environment = os.environ.copy()
    environment.update({name: str(value) for name, value in required.items()})
    return environment


def create_backup(settings: Settings, box: SecretBox) -> Path:
    if not settings.is_postgres:
        raise RuntimeError("Logical backup requires PostgreSQL")
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    plain = settings.backup_dir / f"zlapi-{stamp}.dump"
    encrypted = plain.with_suffix(".dump.aes")
    subprocess.run(
        [
            settings.pg_dump_path,
            "--format=custom",
            "--no-owner",
            "--file",
            str(plain),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=1800,
        env=_pg_environment(settings),
    )
    try:
        encrypted.write_bytes(box.encrypt_bytes(plain.read_bytes()))
    finally:
        plain.unlink(missing_ok=True)
    if settings.rclone_remote:
        subprocess.run(
            ["rclone", "copyto", str(encrypted), f"{settings.rclone_remote.rstrip('/')}/{encrypted.name}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=1800,
        )
    return encrypted


def prune_backups(settings: Settings) -> int:
    files = sorted(settings.backup_dir.glob("zlapi-*.dump.aes"), reverse=True)
    now = utcnow()
    keep: set[Path] = set()
    weekly: set[tuple[int, int]] = set()
    monthly: set[tuple[int, int]] = set()
    for path in files:
        try:
            created = datetime.strptime(path.name[6:21], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
        except ValueError:
            continue
        age = now - created
        if age <= timedelta(days=7):
            keep.add(path)
            continue
        week = created.isocalendar()[:2]
        if age <= timedelta(weeks=5) and week not in weekly:
            weekly.add(week)
            keep.add(path)
            continue
        month = (created.year, created.month)
        if age <= timedelta(days=370) and month not in monthly:
            monthly.add(month)
            keep.add(path)
    removed = 0
    for path in files:
        if path not in keep:
            path.unlink(missing_ok=True)
            removed += 1
    return removed


def health_report(database: Database, settings: Settings) -> tuple[dict, list[str]]:
    alerts: list[str] = []
    with database.session_factory() as session:
        if settings.is_postgres:
            db_size = int(session.scalar(select(func.pg_database_size(func.current_database()))) or 0)
        else:
            db_size = 0
        since = utcnow() - timedelta(hours=24)
        total_jobs = int(session.scalar(select(func.count(Job.id)).where(Job.created_at >= since)) or 0)
        failed_jobs = int(
            session.scalar(
                select(func.count(Job.id)).where(Job.created_at >= since, Job.status == "failed")
            )
            or 0
        )
        error_accounts = int(
            session.scalar(select(func.count(ZaloAccount.id)).where(ZaloAccount.status == "error"))
            or 0
        )
    disk = shutil.disk_usage(settings.data_dir)
    usage = db_size / settings.database_cap_bytes * 100 if settings.database_cap_bytes else 0
    failure_rate = failed_jobs / total_jobs * 100 if total_jobs else 0
    if usage >= 70:
        alerts.append(f"PostgreSQL usage is {usage:.1f}%")
    if disk.free < 20 * 1024**3:
        alerts.append(f"Data disk has only {disk.free / 1024**3:.1f} GiB free")
    if failure_rate > 5 and total_jobs >= 10:
        alerts.append(f"24-hour job failure rate is {failure_rate:.1f}%")
    if error_accounts:
        alerts.append(f"{error_accounts} Zalo account(s) are in error state")
    return (
        {
            "timestamp": utcnow().isoformat(),
            "database_size_bytes": db_size,
            "database_usage_percent": round(usage, 2),
            "disk_free_bytes": disk.free,
            "jobs_24h": total_jobs,
            "failed_jobs_24h": failed_jobs,
            "failure_rate_percent": round(failure_rate, 2),
            "error_accounts": error_accounts,
        },
        alerts,
    )


def notify(settings: Settings, report: dict, alerts: list[str]) -> None:
    if not alerts or not settings.alert_webhook_url:
        return
    requests.post(
        settings.alert_webhook_url,
        json={"service": "zlapi-control-center", "alerts": alerts, "report": report},
        timeout=15,
    ).raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser(description="ZLAPI maintenance and backup")
    parser.add_argument(
        "task",
        choices=["all", "archive", "cleanup", "backup", "health"],
        default="all",
        nargs="?",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    settings.ensure_directories()
    database = Database(settings, pool_size=1, max_overflow=1)
    box = SecretBox(settings)
    failures: list[str] = []
    if args.task in {"all", "archive"}:
        try:
            logger.info("Archived %s events", archive_old_events(database, settings, box))
        except Exception as exc:
            failures.append(f"Archive failed: {exc}")
            logger.exception("Event archive failed")
    if args.task in {"all", "cleanup"}:
        try:
            logger.info("Removed %s expired uploads", cleanup_uploads(database, settings))
        except Exception as exc:
            failures.append(f"Cleanup failed: {exc}")
            logger.exception("Upload cleanup failed")
    if args.task in {"all", "backup"}:
        try:
            path = create_backup(settings, box)
            logger.info("Created backup %s", path)
            logger.info("Pruned %s old backups", prune_backups(settings))
        except Exception as exc:
            failures.append(f"Backup failed: {exc}")
            logger.exception("Database backup failed")
    if args.task in {"all", "health"}:
        try:
            report, alerts = health_report(database, settings)
            alerts.extend(failures)
            logger.info("Health report: %s", json.dumps(report))
            for alert in alerts:
                logger.warning(alert)
            notify(settings, report, alerts)
        except Exception as exc:
            failures.append(f"Health check failed: {exc}")
            logger.exception("Health check failed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
