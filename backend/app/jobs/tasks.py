from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from app.api.schemas import BigBangCreate
from app.db import models
from app.jobs.queues import JOB_TYPES
from app.simulation.initializer import create_big_bang
from app.simulation.tick_runner import run_next_tick


CLAIMABLE_STATUSES = {"queued"}
JOB_LEASE_SECONDS = 15 * 60
DEFAULT_MAX_CONCURRENT_JOBS = 4
PAUSABLE_STATUSES = {"queued", "running"}
REQUEUEABLE_STATUSES = {"failed", "interrupted"}
CHECKPOINTED_JOB_TYPES = {"run_multiverse_tick", "simulate_multiverse_ticks"}
INTERRUPT_TERMINAL_STATUSES = {"interrupt_requested", "interrupted", "cancelled"}


class JobNotRunnableError(RuntimeError):
    pass


def validate_job_type(job_type: str) -> None:
    if job_type not in JOB_TYPES:
        raise ValueError(f"unknown job_type: {job_type}")


def validate_job_payload(job_type: str, payload: dict | None, *, big_bang_id=None) -> None:
    validate_job_type(job_type)
    payload = payload or {}
    if job_type == "initialize_big_bang":
        try:
            BigBangCreate(**payload)
        except ValidationError as exc:
            raise ValueError(f"invalid initialize_big_bang payload: {exc}") from exc
        return
    multiverse_job_types = {
        "run_multiverse_tick",
        "simulate_multiverse_ticks",
        "generate_multiverse_report",
    }
    if job_type in multiverse_job_types:
        _require_uuid_payload(payload, "multiverse_id")
    if job_type in {"generate_final_big_bang_report", "run_big_bang_until_complete"}:
        _require_big_bang_id(payload, big_bang_id)
    if job_type == "simulate_multiverse_ticks" and "count" in payload:
        _require_positive_int(payload["count"], "count")
    if job_type == "run_big_bang_until_complete" and "max_total_ticks" in payload:
        _require_positive_int(payload["max_total_ticks"], "max_total_ticks")


def execute_job(db: Session, job: models.Job, *, commit_running: bool = False) -> models.Job:
    validate_job_type(job.job_type)
    if not claim_job_for_execution(db, job):
        raise JobNotRunnableError(
            f"job {job.id} is {job.status}; only queued or expired running jobs can run"
        )
    if commit_running:
        db.commit()
        db.refresh(job)
    try:
        validate_job_payload(job.job_type, job.payload, big_bang_id=job.big_bang_id)
        if job.job_type == "run_big_bang_until_complete":
            result = _execute_run_big_bang_until_complete_job(db, job)
        elif job.job_type in CHECKPOINTED_JOB_TYPES:
            result = _execute_job(db, job)
        else:
            with db.begin_nested():
                result = _execute_job(db, job)
        job.error = None
        terminal_status = _job_interrupt_status(db, job)
        if terminal_status == "cancelled":
            job.result = _terminal_result(result, "cancelled")
            job.status = "cancelled"
        elif terminal_status in {"interrupt_requested", "interrupted"} or (
            isinstance(result, dict) and result.get("status") == "interrupted"
        ):
            job.result = _terminal_result(result, "interrupted")
            job.status = "interrupted"
            job.interrupted_at = datetime.now(timezone.utc)
        else:
            job.result = result
            job.status = "succeeded"
        job.finished_at = datetime.now(timezone.utc)
        job.lease_owner = None
        job.lease_expires_at = None
        job.last_heartbeat_at = None
    except Exception as exc:
        job.result = {}
        job.error = str(exc)
        job.status = "failed"
        job.finished_at = datetime.now(timezone.utc)
        job.lease_owner = None
        job.lease_expires_at = None
        job.last_heartbeat_at = None
    db.flush()
    return job


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
                and_(models.Job.status == "running", models.Job.updated_at <= lease_cutoff),
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
    if result.rowcount != 1:
        db.refresh(job)
        return False
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


def _execute_job(db: Session, job: models.Job) -> dict:
    payload = job.payload or {}
    if job.job_type == "initialize_big_bang":
        big_bang = create_big_bang(db, BigBangCreate(**payload))
        return {
            "big_bang_id": str(big_bang.id),
            "source_snapshot_id": str(big_bang.source_snapshot_id),
        }
    if job.job_type == "run_multiverse_tick":
        multiverse = db.get(models.Multiverse, payload["multiverse_id"])
        if not multiverse:
            raise ValueError("multiverse not found")
        tick = run_next_tick(
            db,
            multiverse=multiverse,
            idempotency_key=payload.get("idempotency_key"),
            queue_job=job,
        )
        if job.status == "interrupted" or tick.status in {"running", "provisional"}:
            return {
                "status": "interrupted" if job.status == "interrupted" else tick.status,
                "tick_snapshot_id": str(tick.id),
                "ui_label": tick.ui_label,
            }
        return {"tick_snapshot_id": str(tick.id), "ui_label": tick.ui_label}
    if job.job_type == "simulate_multiverse_ticks":
        from app.simulation.run_orchestrator import simulate_ticks

        multiverse = db.get(models.Multiverse, payload["multiverse_id"])
        if not multiverse:
            raise ValueError("multiverse not found")
        ticks = simulate_ticks(db, multiverse=multiverse, count=int(payload.get("count", 1)), queue_job=job)
        return {"tick_snapshot_ids": [str(tick.id) for tick in ticks]}
    if job.job_type == "generate_multiverse_report":
        from app.simulation.report_engine import generate_multiverse_report

        multiverse = db.get(models.Multiverse, payload["multiverse_id"])
        if not multiverse:
            raise ValueError("multiverse not found")
        report = generate_multiverse_report(
            db,
            multiverse=multiverse,
            title=payload.get("title"),
            summary=payload.get("summary"),
        )
        return {"report_version_id": str(report.id)}
    if job.job_type == "generate_final_big_bang_report":
        from app.simulation.report_engine import generate_final_big_bang_report

        big_bang = db.get(models.BigBang, job.big_bang_id or payload.get("big_bang_id"))
        if not big_bang:
            raise ValueError("big bang not found")
        report = generate_final_big_bang_report(
            db,
            big_bang=big_bang,
            title=payload.get("title"),
            summary=payload.get("summary"),
        )
        return {"report_version_id": str(report.id)}
    if job.job_type == "run_big_bang_until_complete":
        from app.simulation.run_orchestrator import run_big_bang_until_complete

        big_bang = db.get(models.BigBang, job.big_bang_id or payload.get("big_bang_id"))
        if not big_bang:
            raise ValueError("big bang not found")
        return run_big_bang_until_complete(
            db,
            big_bang=big_bang,
            max_total_ticks=int(payload.get("max_total_ticks", 24)),
        )
    raise NotImplementedError(f"job_type has no executor: {job.job_type}")


def _execute_run_big_bang_until_complete_job(db: Session, job: models.Job) -> dict:
    from sqlalchemy import func

    from app.simulation.report_engine import generate_final_big_bang_report, generate_multiverse_report
    from app.simulation.tick_runner import TERMINAL_MULTIVERSE_STATUSES, UNFINISHED_TICK_STATUSES

    payload = job.payload or {}
    max_total_ticks = int(payload.get("max_total_ticks", 24))
    if max_total_ticks < 1:
        raise ValueError("max_total_ticks must be a positive integer")

    big_bang = db.get(models.BigBang, job.big_bang_id or payload.get("big_bang_id"))
    if not big_bang:
        raise ValueError("big bang not found")
    if big_bang.status == "paused":
        raise ValueError("big bang is paused")

    tick_ids: list[str] = []
    latest_tick_id: str | None = None
    latest_tick_label: str | None = None
    stopped_reason: str | None = None

    def make_progress(stopped: str | None = None) -> dict:
        multiverse_count = db.scalar(
            select(func.count(models.Multiverse.id)).where(models.Multiverse.big_bang_id == big_bang.id)
        )
        return {
            "big_bang_id": str(big_bang.id),
            "ticks_run": len(tick_ids),
            "latest_tick_id": latest_tick_id,
            "latest_tick_label": latest_tick_label,
            "multiverse_count": int(multiverse_count or 0),
            "stopped_reason": stopped,
            "progress": {
                "completed_ticks": len(tick_ids),
                "requested_ticks": max_total_ticks,
                "percent": min(100, round((len(tick_ids) / max_total_ticks) * 100, 2)),
            },
        }

    for _ in range(max_total_ticks):
        interrupt_status = _job_interrupt_status(db, job)
        if interrupt_status == "cancelled":
            return _mark_job_cancelled(db, job, result=make_progress("cancelled"))
        if interrupt_status:
            return _mark_job_interrupted(db, job, result={**make_progress("interrupted"), "status": "interrupted"})
        active_multiverses = db.scalars(
            select(models.Multiverse)
            .where(
                models.Multiverse.big_bang_id == big_bang.id,
                ~models.Multiverse.status.in_(TERMINAL_MULTIVERSE_STATUSES),
            )
            .order_by(models.Multiverse.created_at.asc())
        ).all()
        if not active_multiverses:
            stopped_reason = "all_multiverses_terminal"
            break

        made_progress = False
        for multiverse in active_multiverses:
            interrupt_status = _job_interrupt_status(db, job)
            if interrupt_status == "cancelled":
                return _mark_job_cancelled(db, job, result=make_progress("cancelled"))
            if interrupt_status:
                return _mark_job_interrupted(db, job, result={**make_progress("interrupted"), "status": "interrupted"})
            tick = run_next_tick(db, multiverse=multiverse, queue_job=job)
            if job.status in {"interrupted", "cancelled"}:
                return {
                    **make_progress(job.status),
                    "status": job.status,
                    "latest_tick_id": str(tick.id),
                }
            if tick.status in UNFINISHED_TICK_STATUSES:
                continue
            tick_id = str(tick.id)
            if tick_id in tick_ids:
                continue
            tick_ids.append(tick_id)
            latest_tick_id = tick_id
            latest_tick_label = tick.ui_label
            made_progress = True
            job.result = make_progress()
            db.add(job)
            db.flush()
            db.commit()

        if not made_progress:
            stopped_reason = "no_tick_progress"
            break

    multiverses = db.scalars(
        select(models.Multiverse)
        .where(models.Multiverse.big_bang_id == big_bang.id)
        .order_by(models.Multiverse.created_at.asc())
    ).all()
    unfinished_ticks = db.scalar(
        select(func.count(models.Tick.id)).where(
            models.Tick.big_bang_id == big_bang.id,
            models.Tick.status.in_(UNFINISHED_TICK_STATUSES),
        )
    )
    non_terminal = [mv for mv in multiverses if mv.status not in TERMINAL_MULTIVERSE_STATUSES]

    report_version_ids: list[str] = []
    final_report_version_id: str | None = None
    if not unfinished_ticks and not non_terminal and multiverses:
        for multiverse in multiverses:
            report_version = generate_multiverse_report(db, multiverse=multiverse)
            report_version_ids.append(str(report_version.id))
        final_report_version = generate_final_big_bang_report(db, big_bang=big_bang)
        final_report_version_id = str(final_report_version.id)
        big_bang.status = "completed"
        stopped_reason = "completed"
    elif stopped_reason is None:
        stopped_reason = "max_total_ticks_reached"

    result = make_progress(stopped_reason)
    result["report_version_ids"] = report_version_ids
    result["final_report_version_id"] = final_report_version_id
    return result


def _require_uuid_payload(payload: dict, key: str) -> UUID:
    value = payload.get(key)
    if value is None or value == "":
        raise ValueError(f"{key} is required")
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except ValueError as exc:
        raise ValueError(f"{key} must be a UUID") from exc


def _require_big_bang_id(payload: dict, big_bang_id) -> UUID:
    value = big_bang_id or payload.get("big_bang_id")
    if value is None or value == "":
        raise ValueError("big_bang_id is required")
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except ValueError as exc:
        raise ValueError("big_bang_id must be a UUID") from exc


def _require_positive_int(value, key: str) -> None:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{key} must be a positive integer")
