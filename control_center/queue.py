from __future__ import annotations

from datetime import timedelta

from sqlalchemy import Select, select, update
from sqlalchemy.orm import Session

from .models import Job, utcnow

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


def enqueue_job(
    session: Session,
    *,
    account_id: str,
    action: str,
    payload: dict,
    created_by_id: str | None,
    priority: int = 100,
    max_attempts: int = 3,
) -> Job:
    job = Job(
        account_id=account_id,
        action=action,
        payload=payload,
        created_by_id=created_by_id,
        priority=priority,
        max_attempts=max_attempts,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def claim_job(session: Session, worker_id: str, postgres: bool) -> Job | None:
    now = utcnow()
    statement: Select[tuple[Job]] = (
        select(Job)
        .where(Job.status == "queued", Job.next_run_at <= now)
        .order_by(Job.priority.asc(), Job.created_at.asc())
        .limit(1)
    )
    if postgres:
        statement = statement.with_for_update(skip_locked=True)
    job = session.scalar(statement)
    if job is None:
        session.rollback()
        return None
    job.status = "running"
    job.locked_by = worker_id
    job.locked_at = now
    job.started_at = job.started_at or now
    job.attempts += 1
    session.commit()
    session.refresh(job)
    return job


def complete_job(session: Session, job_id: str, result) -> None:
    job = session.get(Job, job_id)
    if job is None:
        return
    job.status = "succeeded"
    job.result = result
    job.error = None
    job.finished_at = utcnow()
    job.locked_at = None
    job.locked_by = None
    session.commit()


def fail_job(session: Session, job_id: str, error: str, *, retryable: bool = True) -> str:
    job = session.get(Job, job_id)
    if job is None:
        return "missing"
    job.error = error[:8000]
    job.locked_at = None
    job.locked_by = None
    if retryable and job.attempts < job.max_attempts:
        job.status = "queued"
        job.next_run_at = utcnow() + timedelta(seconds=min(60, 2**job.attempts))
    else:
        job.status = "failed"
        job.finished_at = utcnow()
    session.commit()
    return job.status


def cancel_job(session: Session, job: Job) -> bool:
    if job.status not in {"queued"}:
        return False
    job.status = "cancelled"
    job.finished_at = utcnow()
    session.commit()
    return True


def retry_job(session: Session, job: Job) -> bool:
    if job.status not in {"failed", "cancelled"}:
        return False
    job.status = "queued"
    job.attempts = 0
    job.error = None
    job.result = None
    job.next_run_at = utcnow()
    job.finished_at = None
    session.commit()
    return True


def recover_stale_jobs(session: Session, older_than_minutes: int = 15) -> int:
    threshold = utcnow() - timedelta(minutes=older_than_minutes)
    result = session.execute(
        update(Job)
        .where(Job.status == "running", Job.locked_at < threshold)
        .values(
            status="queued",
            locked_at=None,
            locked_by=None,
            next_run_at=utcnow(),
            error="Recovered after stale worker lock",
        )
    )
    session.commit()
    return result.rowcount or 0
