from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import BigBangCreate
from app.db import models
from app.domains.jobs.lifecycle import (
    CLAIMABLE_STATUSES,
    DEFAULT_MAX_CONCURRENT_JOBS,
    INTERRUPT_TERMINAL_STATUSES,
    JOB_LEASE_SECONDS,
    PAUSABLE_STATUSES,
    REQUEUEABLE_STATUSES,
    TERMINAL_JOB_STATUSES,
    JobNotRunnableError,
    _job_interrupt_requested,
    _job_interrupt_status,
    _mark_job_cancelled,
    _mark_job_interrupted,
    _terminal_result,
    claim_job_for_execution,
    interrupt_job,
    job_should_enqueue_for_retry,
    pause_job,
    queue_health_snapshot,
    requeue_job,
    resume_job,
    running_job_lease_cutoff,
)
from app.domains.jobs.queues import JOB_TYPES
from app.domains.big_bang.initializer import create_big_bang
from app.domains.tick.tick_runner import run_next_tick


CHECKPOINTED_JOB_TYPES = {"run_multiverse_tick", "simulate_multiverse_ticks"}
AUDITED_LONG_RUNNING_JOB_TYPES = {
    "initialize_big_bang",
    "generate_multiverse_report",
    "generate_final_big_bang_report",
    "evaluate_endpoint_ledger",
}

__all__ = [
    "CLAIMABLE_STATUSES",
    "JOB_LEASE_SECONDS",
    "DEFAULT_MAX_CONCURRENT_JOBS",
    "PAUSABLE_STATUSES",
    "REQUEUEABLE_STATUSES",
    "TERMINAL_JOB_STATUSES",
    "CHECKPOINTED_JOB_TYPES",
    "AUDITED_LONG_RUNNING_JOB_TYPES",
    "INTERRUPT_TERMINAL_STATUSES",
    "JobNotRunnableError",
    "validate_job_type",
    "validate_job_payload",
    "execute_job",
    "claim_job_for_execution",
    "running_job_lease_cutoff",
    "job_should_enqueue_for_retry",
    "pause_job",
    "resume_job",
    "interrupt_job",
    "requeue_job",
    "queue_health_snapshot",
    "_job_interrupt_requested",
    "_job_interrupt_status",
    "_terminal_result",
    "_mark_job_interrupted",
    "_mark_job_cancelled",
    "_execute_job",
    "_execute_run_big_bang_until_complete_job",
]


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
    if job_type in {"generate_final_big_bang_report", "run_big_bang_until_complete", "evaluate_endpoint_ledger"}:
        _require_big_bang_id(payload, big_bang_id)
    if job_type == "evaluate_endpoint_ledger":
        scope = payload.get("scope") or "big_bang"
        if scope not in {"big_bang", "multiverse"}:
            raise ValueError("scope must be big_bang or multiverse")
        if scope == "multiverse":
            _require_uuid_payload(payload, "multiverse_id")
    if job_type == "simulate_multiverse_ticks" and "count" in payload:
        _require_positive_int(payload["count"], "count")
    if job_type == "run_big_bang_until_complete" and "max_total_ticks" in payload:
        _require_positive_int(payload["max_total_ticks"], "max_total_ticks")


def reject_archived_big_bang(big_bang: models.BigBang) -> None:
    if big_bang.status == "archived":
        raise ValueError("big bang is archived")


def reject_non_terminal_multiverses(db: Session, big_bang: models.BigBang) -> None:
    from app.domains.tick.tick_runner import TERMINAL_MULTIVERSE_STATUSES

    non_terminal = db.scalars(
        select(models.Multiverse)
        .where(
            models.Multiverse.big_bang_id == big_bang.id,
            models.Multiverse.status.notin_(TERMINAL_MULTIVERSE_STATUSES),
        )
        .order_by(models.Multiverse.ui_label)
    ).all()
    if non_terminal:
        labels = ", ".join(item.ui_label for item in non_terminal[:5])
        suffix = f": {labels}" if labels else ""
        raise ValueError(f"final report requires terminal multiverses{suffix}")


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
        elif job.job_type in CHECKPOINTED_JOB_TYPES or job.job_type in AUDITED_LONG_RUNNING_JOB_TYPES:
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
            force=bool(payload.get("force", False)),
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
        from app.domains.big_bang.run_orchestrator import simulate_ticks

        multiverse = db.get(models.Multiverse, payload["multiverse_id"])
        if not multiverse:
            raise ValueError("multiverse not found")
        ticks = simulate_ticks(db, multiverse=multiverse, count=int(payload.get("count", 1)), queue_job=job)
        return {"tick_snapshot_ids": [str(tick.id) for tick in ticks]}
    if job.job_type == "generate_multiverse_report":
        from app.domains.report.engine import generate_multiverse_report

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
        from app.domains.report.engine import generate_final_big_bang_report

        big_bang = db.get(models.BigBang, job.big_bang_id or payload.get("big_bang_id"))
        if not big_bang:
            raise ValueError("big bang not found")
        reject_archived_big_bang(big_bang)
        reject_non_terminal_multiverses(db, big_bang)
        report = generate_final_big_bang_report(
            db,
            big_bang=big_bang,
            title=payload.get("title"),
            summary=payload.get("summary"),
        )
        return {"report_version_id": str(report.id)}
    if job.job_type == "evaluate_endpoint_ledger":
        from app.domains.endpoint_ledger.service import evaluate_endpoint_ledger

        big_bang = db.get(models.BigBang, job.big_bang_id or payload.get("big_bang_id"))
        if not big_bang:
            raise ValueError("big bang not found")
        multiverse = None
        if payload.get("scope") == "multiverse" or payload.get("multiverse_id"):
            multiverse = db.get(models.Multiverse, payload.get("multiverse_id"))
            if not multiverse:
                raise ValueError("multiverse not found")
            if multiverse.big_bang_id != big_bang.id:
                raise ValueError("multiverse does not belong to the requested big bang")
        ledger = evaluate_endpoint_ledger(
            db,
            big_bang=big_bang,
            multiverse=multiverse,
            source_type="posthoc_job",
            created_by="endpoint_ledger_job",
            candidate_endpoint=payload.get("candidate_endpoint"),
        )
        return {"ledger_version_id": str(ledger.id)}
    if job.job_type == "run_big_bang_until_complete":
        from app.domains.big_bang.run_orchestrator import run_big_bang_until_complete

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

    from app.domains.report.engine import generate_final_big_bang_report, generate_multiverse_reports_parallel
    from app.domains.tick.tick_runner import TERMINAL_MULTIVERSE_STATUSES, UNFINISHED_TICK_STATUSES

    payload = job.payload or {}
    max_total_ticks = int(payload.get("max_total_ticks", 24))
    if max_total_ticks < 1:
        raise ValueError("max_total_ticks must be a positive integer")

    big_bang = db.get(models.BigBang, job.big_bang_id or payload.get("big_bang_id"))
    if not big_bang:
        raise ValueError("big bang not found")
    reject_archived_big_bang(big_bang)
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
        select(func.count(models.TickSnapshot.id)).where(
            models.TickSnapshot.big_bang_id == big_bang.id,
            models.TickSnapshot.status.in_(UNFINISHED_TICK_STATUSES),
        )
    )
    non_terminal = [mv for mv in multiverses if mv.status not in TERMINAL_MULTIVERSE_STATUSES]

    report_version_ids: list[str] = []
    final_report_version_id: str | None = None
    if not unfinished_ticks and not non_terminal and multiverses:
        report_targets = [mv for mv in multiverses if mv.report_status in {"ready", "not_ready"}]
        job.result = {
            **make_progress("generating_single_reports"),
            "phase": "generating_single_reports",
            "report_progress": {"completed": 0, "total": len(report_targets)},
        }
        db.add(job)
        db.flush()
        db.commit()
        report_versions = generate_multiverse_reports_parallel(db, multiverses=list(multiverses))
        report_version_ids = [str(report_version.id) for report_version in report_versions]
        job.result = {
            **make_progress("generating_final_report"),
            "phase": "generating_final_report",
            "report_progress": {"completed": len(report_version_ids), "total": len(report_targets)},
        }
        db.add(job)
        db.flush()
        db.commit()
        big_bang.status = "completed"
        final_report_version = generate_final_big_bang_report(db, big_bang=big_bang)
        final_report_version_id = str(final_report_version.id)
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
