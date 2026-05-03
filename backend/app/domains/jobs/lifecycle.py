from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, cast

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.db import models
from app.llm.audit import mark_stale_running_llm_calls_failed


CLAIMABLE_STATUSES = {"queued"}
JOB_LEASE_SECONDS = 15 * 60
DEFAULT_MAX_CONCURRENT_JOBS = 4
PAUSABLE_STATUSES = {"queued", "running"}
REQUEUEABLE_STATUSES = {"failed", "interrupted"}
INTERRUPT_TERMINAL_STATUSES = {"interrupt_requested", "interrupted", "cancelled"}


class JobNotRunnableError(RuntimeError):
    pass


def claim_job_for_execution(
    db: Session,
    job: models.Job,
    *,
    now: datetime | None = None,
    lease_owner: str = "local-worker",
) -> bool:
    current = now or datetime.now(timezone.utc)
    lease_cutoff = running_job_lease_cutoff(current)
    lease_expires_at = current + timedelta(seconds=JOB_LEASE_SECONDS)
    result = db.execute(
        update(models.Job)
        .where(
            models.Job.id == job.id,
            or_(
                models.Job.status.in_(CLAIMABLE_STATUSES),
                and_(
                    models.Job.status == "running",
                    or_(
                        models.Job.lease_expires_at <= current,
                        and_(
                            models.Job.lease_expires_at.is_(None),
                            models.Job.updated_at <= lease_cutoff,
                        ),
                    ),
                ),
            ),
        )
        .values(
            status="running",
            error=None,
            lease_owner=lease_owner,
            lease_expires_at=lease_expires_at,
            last_heartbeat_at=current,
            started_at=func.coalesce(models.Job.started_at, current),
            paused_at=None,
            interrupt_requested_at=None,
            interrupted_at=None,
        )
        .execution_options(synchronize_session=False)
    )
    db.flush()
    if cast(Any, result).rowcount != 1:
        db.refresh(job)
        return False
    if (
        job.status == "running"
        and job.big_bang_id is not None
        and getattr(job, "lease_expires_at", None) is not None
    ):
        mark_stale_running_llm_calls_failed(
            db,
            big_bang_id=job.big_bang_id,
            stale_after_seconds=JOB_LEASE_SECONDS,
            now=current,
            reason=f"expired job lease reclaimed for job {job.id}",
        )
    job.status = "running"
    job.error = None
    job.lease_owner = lease_owner
    job.lease_expires_at = lease_expires_at
    job.last_heartbeat_at = current
    job.started_at = getattr(job, "started_at", None) or current
    job.paused_at = None
    job.interrupt_requested_at = None
    job.interrupted_at = None
    return True


def running_job_lease_cutoff(now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    return current - timedelta(seconds=JOB_LEASE_SECONDS)


def job_should_enqueue_for_retry(job: models.Job, *, now: datetime | None = None) -> bool:
    if job.status == "queued":
        return True
    if job.status in {"paused", "interrupt_requested"}:
        return False
    if job.status != "running":
        return False
    lease_expires_at = getattr(job, "lease_expires_at", None)
    if lease_expires_at is not None:
        if lease_expires_at.tzinfo is None:
            lease_expires_at = lease_expires_at.replace(tzinfo=timezone.utc)
        return lease_expires_at <= (now or datetime.now(timezone.utc))
    updated_at = getattr(job, "updated_at", None)
    if updated_at is None:
        return False
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return updated_at <= running_job_lease_cutoff(now)


def pause_job(db: Session, job: models.Job, *, now: datetime | None = None) -> models.Job:
    current = now or datetime.now(timezone.utc)
    if job.status == "queued":
        job.status = "paused"
        job.paused_at = current
        job.lease_owner = None
        job.lease_expires_at = None
        job.last_heartbeat_at = None
        db.add(job)
        db.flush()
        return job
    if job.status == "running":
        job.status = "interrupt_requested"
        job.paused_at = current
        job.interrupt_requested_at = current
        db.add(job)
        db.flush()
        return job
    raise JobNotRunnableError(f"job {job.id} is {job.status}; only queued or running jobs can pause")


def resume_job(db: Session, job: models.Job, *, now: datetime | None = None) -> models.Job:
    current = now or datetime.now(timezone.utc)
    if job.status not in {"paused", "interrupted"}:
        raise JobNotRunnableError(f"job {job.id} is {job.status}; only paused or interrupted jobs can resume")
    job.status = "queued"
    job.available_at = current
    job.queued_at = current
    job.paused_at = None
    job.interrupt_requested_at = None
    job.interrupted_at = None
    job.lease_owner = None
    job.lease_expires_at = None
    job.last_heartbeat_at = None
    db.add(job)
    db.flush()
    return job


def interrupt_job(db: Session, job: models.Job, *, now: datetime | None = None) -> models.Job:
    current = now or datetime.now(timezone.utc)
    if job.status == "running":
        job.status = "interrupt_requested"
        job.interrupt_requested_at = current
        db.add(job)
        db.flush()
        return job
    if job.status in {"queued", "paused"}:
        job.status = "interrupted"
        job.interrupted_at = current
        job.interrupt_requested_at = current
        job.lease_owner = None
        job.lease_expires_at = None
        db.add(job)
        db.flush()
        return job
    raise JobNotRunnableError(
        f"job {job.id} is {job.status}; only queued, paused, or running jobs can interrupt"
    )


def requeue_job(db: Session, job: models.Job, *, now: datetime | None = None) -> models.Job:
    current = now or datetime.now(timezone.utc)
    if job.status not in REQUEUEABLE_STATUSES:
        raise JobNotRunnableError(
            f"job {job.id} is {job.status}; only failed or interrupted jobs can requeue"
        )
    if not job.retryable:
        raise JobNotRunnableError(f"job {job.id} is not retryable")
    if job.attempt_number + 1 > job.max_attempts:
        raise JobNotRunnableError(
            f"job {job.id} exceeded max attempts ({job.attempt_number}/{job.max_attempts})"
        )
    job.status = "queued"
    job.attempt_number += 1
    job.available_at = current
    job.queued_at = current
    job.error = None
    job.finished_at = None
    job.interrupt_requested_at = None
    job.interrupted_at = None
    job.paused_at = None
    job.lease_owner = None
    job.lease_expires_at = None
    job.last_heartbeat_at = None
    db.add(job)
    db.flush()
    return job


def queue_health_snapshot(db: Session) -> dict:
    counts_by_status: dict[str, int] = {}
    for status, count in db.execute(
        select(models.Job.status, func.count(models.Job.id)).group_by(models.Job.status)
    ):
        counts_by_status[str(status)] = int(count)
    running = counts_by_status.get("running", 0)
    return {
        "counts_by_status": counts_by_status,
        "capacity": {
            "max_concurrent_jobs": DEFAULT_MAX_CONCURRENT_JOBS,
            "running_jobs": running,
            "available_slots": max(0, DEFAULT_MAX_CONCURRENT_JOBS - running),
        },
    }


def _job_interrupt_requested(db: Session, job: models.Job) -> bool:
    return _job_interrupt_status(db, job) is not None


def _job_interrupt_status(db: Session, job: models.Job) -> str | None:
    db.flush()
    status = db.scalar(select(models.Job.status).where(models.Job.id == job.id))
    if status in INTERRUPT_TERMINAL_STATUSES:
        return str(status)
    local_status = getattr(job, "status", None)
    if local_status in INTERRUPT_TERMINAL_STATUSES:
        return str(local_status)
    return None


def _terminal_result(result: dict | None, status: str) -> dict:
    if isinstance(result, dict):
        return {**result, "status": status}
    return {"status": status}


def _mark_job_interrupted(db: Session, job: models.Job, *, result: dict | None = None) -> dict:
    current = datetime.now(timezone.utc)
    job.status = "interrupted"
    job.interrupted_at = current
    job.finished_at = current
    job.lease_owner = None
    job.lease_expires_at = None
    job.last_heartbeat_at = None
    if result is not None:
        job.result = result
    db.add(job)
    db.flush()
    return job.result or {"status": "interrupted"}


def _mark_job_cancelled(db: Session, job: models.Job, *, result: dict | None = None) -> dict:
    current = datetime.now(timezone.utc)
    job.status = "cancelled"
    job.finished_at = current
    job.lease_owner = None
    job.lease_expires_at = None
    job.last_heartbeat_at = None
    job.result = _terminal_result(result, "cancelled")
    db.add(job)
    db.flush()
    return job.result or {"status": "cancelled"}
