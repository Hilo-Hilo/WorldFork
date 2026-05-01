from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

import app.api.jobs as current_jobs
from app.db import models as current_models
from app.domains.legacy.compat_db import table_has_columns
from backend.app.workers import celery_app as celery_app_module
from backend.app.core.db import get_session
from backend.app.models.jobs import JobModel
from app.domains.jobs.queues import JOB_TYPES
from app.domains.legacy.logs import _first_non_null, sanitize_public_job_payload

router = APIRouter(prefix="/api/jobs", tags=["jobs-legacy"])
DbSession = Annotated[AsyncSession, Depends(get_session)]
KNOWN_QUEUES = ["p0", "p1", "p2", "p3", "dead_letter"]


def _job_to_dict(row: JobModel) -> dict[str, Any]:
    return {
        "job_id": row.job_id,
        "idempotency_key": row.idempotency_key,
        "job_type": row.job_type,
        "priority": row.priority,
        "run_id": row.run_id,
        "universe_id": row.universe_id,
        "tick": row.tick,
        "attempt_number": row.attempt_number,
        "payload": sanitize_public_job_payload(dict(row.payload or {})),
        "status": row.status,
        "enqueued_at": row.enqueued_at.isoformat() if row.enqueued_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "error": row.error,
        "result_summary": dict(row.result_summary or {}) if row.result_summary else None,
        "artifact_path": row.artifact_path,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _current_job_to_dict(row: current_models.Job) -> dict[str, Any]:
    payload = dict(row.payload or {})
    return {
        "job_id": str(row.id),
        "idempotency_key": row.idempotency_key,
        "job_type": row.job_type,
        "priority": row.queue_name,
        "run_id": str(row.big_bang_id) if row.big_bang_id else "",
        "universe_id": _first_non_null(payload, "universe_id", "multiverse_id"),
        "tick": _first_non_null(payload, "tick", "tick_index"),
        "attempt_number": row.attempt_number,
        "payload": sanitize_public_job_payload(payload),
        "status": row.status,
        "enqueued_at": row.queued_at.isoformat() if row.queued_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "error": row.error,
        "result_summary": dict(row.result or {}) if row.result else None,
        "artifact_path": None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def _uses_current_jobs_table(session: AsyncSession) -> bool:
    return (
        await table_has_columns(session, "jobs", ["id", "queue_name", "big_bang_id"])
        and not await table_has_columns(session, "jobs", ["job_id"])
    )


async def _get_job_or_404(job_id: str, session: AsyncSession) -> JobModel:
    row = await session.get(JobModel, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
    return row


async def _get_current_job_or_404(job_id: str, session: AsyncSession) -> current_models.Job:
    try:
        parsed = UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"job {job_id!r} not found") from exc
    row = await session.get(current_models.Job, parsed)
    if row is None:
        raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
    return row


@router.get("/queues")
async def get_queues() -> dict[str, Any]:
    try:
        inspect = celery_app_module.celery_app.control.inspect()
        active = inspect.active() or {}
        reserved = inspect.reserved() or {}
        scheduled = inspect.scheduled() or {}
        stats = {q: {"active": 0, "reserved": 0, "scheduled": 0} for q in KNOWN_QUEUES}
        for task_sets, key in ((active, "active"), (reserved, "reserved"), (scheduled, "scheduled")):
            for tasks in task_sets.values():
                for task in tasks or []:
                    queue = ((task.get("delivery_info") or {}).get("routing_key")) or "p1"
                    stats.setdefault(queue, {"active": 0, "reserved": 0, "scheduled": 0})
                    stats[queue][key] += 1
        queues = [{"queue": q, **stats[q]} for q in sorted(stats)]
        return {"degraded": False, "queues": queues}
    except Exception as exc:
        return {
            "degraded": True,
            "error": str(exc),
            "queues": [{"queue": q, "active": 0, "reserved": 0, "scheduled": 0} for q in KNOWN_QUEUES],
        }


@router.get("/workers")
async def get_workers() -> dict[str, Any]:
    try:
        inspect = celery_app_module.celery_app.control.inspect()
        stats = inspect.stats() or {}
        workers = [{"worker": name, "stats": payload} for name, payload in stats.items()]
        return {"degraded": False, "workers": workers}
    except Exception as exc:
        return {"degraded": True, "error": str(exc), "workers": []}


@router.get("/types")
async def get_job_types_compat() -> list[str]:
    return sorted(JOB_TYPES)


@router.get("")
async def list_jobs(
    session: DbSession,
    status: str | None = None,
    run_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    if await _uses_current_jobs_table(session):
        stmt = select(current_models.Job)
        count_stmt = select(func.count()).select_from(current_models.Job)
        if status:
            stmt = stmt.where(current_models.Job.status == status)
            count_stmt = count_stmt.where(current_models.Job.status == status)
        if run_id:
            try:
                parsed_run_id = UUID(run_id)
            except ValueError:
                return {"jobs": [], "total": 0, "limit": limit, "offset": offset}
            stmt = stmt.where(current_models.Job.big_bang_id == parsed_run_id)
            count_stmt = count_stmt.where(current_models.Job.big_bang_id == parsed_run_id)
        stmt = stmt.order_by(current_models.Job.created_at.desc()).limit(limit).offset(offset)
        result = await session.execute(stmt)
        total = int((await session.execute(count_stmt)).scalar_one())
        return {
            "jobs": [_current_job_to_dict(row) for row in result.scalars().all()],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    stmt = select(JobModel)
    count_stmt = select(func.count()).select_from(JobModel)
    if status:
        stmt = stmt.where(JobModel.status == status)
        count_stmt = count_stmt.where(JobModel.status == status)
    if run_id:
        stmt = stmt.where(JobModel.run_id == run_id)
        count_stmt = count_stmt.where(JobModel.run_id == run_id)
    stmt = stmt.order_by(JobModel.created_at.desc()).limit(limit).offset(offset)
    result = await session.execute(stmt)
    total = int((await session.execute(count_stmt)).scalar_one())
    return {"jobs": [_job_to_dict(row) for row in result.scalars().all()], "total": total, "limit": limit, "offset": offset}


@router.get("/queue-health")
def get_queue_health_compat(db: Session = Depends(current_jobs.get_db)) -> dict[str, Any]:
    return current_jobs.queue_health_snapshot(db)


@router.get("/{job_id}")
async def get_job(job_id: str, session: DbSession) -> dict[str, Any]:
    if await _uses_current_jobs_table(session):
        return _current_job_to_dict(await _get_current_job_or_404(job_id, session))
    return _job_to_dict(await _get_job_or_404(job_id, session))


@router.post("/{job_id}/retry")
async def retry_job(job_id: str, background_tasks: BackgroundTasks, session: DbSession) -> dict[str, Any]:
    if await _uses_current_jobs_table(session):
        try:
            row = await session.run_sync(
                lambda sync_session: (
                    current_jobs.requeue_job(sync_session, current_row)
                    if (current_row := sync_session.get(current_models.Job, UUID(job_id))) is not None
                    else None
                )
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=f"job {job_id!r} not found") from exc
        except current_jobs.JobNotRunnableError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if row is None:
            raise HTTPException(status_code=404, detail=f"job {job_id!r} not found")
        await session.commit()
        try:
            current_jobs.enqueue_job(row.id)
        except Exception:
            row.error = "enqueue failed; running with local worker fallback"
            await session.commit()
            current_jobs.schedule_local_fallback(background_tasks, row.id)
        data = _current_job_to_dict(row)
        data["new_task_id"] = str(row.id)
        return data

    row = await _get_job_or_404(job_id, session)
    if row.status not in {"failed", "interrupted"}:
        raise HTTPException(status_code=409, detail=f"job {job_id!r} is {row.status}; only failed or interrupted jobs can retry")
    row.attempt_number += 1
    row.status = "queued"
    row.enqueued_at = datetime.now(UTC)
    await session.commit()
    data = _job_to_dict(row)
    data["new_task_id"] = row.job_id
    return data


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, session: DbSession) -> dict[str, Any]:
    if await _uses_current_jobs_table(session):
        row = await _get_current_job_or_404(job_id, session)
        row.status = "cancelled"
        row.finished_at = datetime.now(UTC)
        await session.commit()
        return _current_job_to_dict(row)

    row = await _get_job_or_404(job_id, session)
    row.status = "cancelled"
    row.finished_at = datetime.now(UTC)
    await session.commit()
    return _job_to_dict(row)


@router.post("/queues/{queue}/pause")
async def pause_queue(queue: str) -> dict[str, Any]:
    redis = current_jobs.get_redis_client()
    await redis.set(f"jobs:queue:{queue}:paused", "1")
    return {"queue": queue, "paused": True}


@router.post("/queues/{queue}/resume")
async def resume_queue(queue: str) -> dict[str, Any]:
    redis = current_jobs.get_redis_client()
    await redis.delete(f"jobs:queue:{queue}:paused")
    return {"queue": queue, "paused": False}
