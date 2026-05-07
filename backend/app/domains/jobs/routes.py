from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.schemas import JobCreate
from app.api.utils import commit_or_500, require
from backend.app.api.websockets_publishers import publish_job_status_changed
from app.db import models
from app.db.session import SessionLocal, get_db
from app.domains.jobs.queues import JOB_TYPES, default_idempotency_key, enqueue_job, queue_name_for_job
from app.domains.jobs.executor import (
    JobNotRunnableError,
    claim_job_for_execution,
    execute_job,
    interrupt_job,
    job_should_enqueue_for_retry,
    pause_job,
    queue_health_snapshot,
    recover_stale_running_jobs,
    requeue_job,
    resume_job,
    validate_job_payload,
    validate_job_type,
)
from backend.app.workers import celery_app as celery_app_module

from backend.app.core.redis_client import get_redis_client  # noqa: F401

router = APIRouter(prefix="/jobs", tags=["jobs"])
KNOWN_QUEUES = ["p0", "p1", "p2", "p3", "dead_letter"]


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_type: str
    queue_name: str
    status: str
    big_bang_id: UUID | None = None
    payload: dict[str, Any]
    result: dict[str, Any]
    error: str | None = None
    idempotency_key: str
    concurrency_key: str | None = None
    attempt_number: int
    max_attempts: int
    retryable: bool
    available_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    paused_at: datetime | None = None
    interrupt_requested_at: datetime | None = None
    interrupted_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


@router.get("/types", response_model=list[str])
def types():
    return sorted(JOB_TYPES)


@router.get("", response_model=list[JobResponse], operation_id="canonical_list_jobs")
def list_jobs(
    status: str | None = None,
    big_bang_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(models.Job)
    if status:
        stmt = stmt.where(models.Job.status == status)
    if big_bang_id:
        stmt = stmt.where(models.Job.big_bang_id == big_bang_id)
    return db.scalars(stmt.order_by(models.Job.created_at.desc()).limit(limit).offset(offset)).all()


@router.post("", response_model=JobResponse)
def create_job(payload: JobCreate, db: Session = Depends(get_db)):
    return create_job_record(payload, db=db)


def create_job_record(payload: JobCreate, db: Session, background_tasks: BackgroundTasks | None = None):
    try:
        validate_job_type(payload.job_type)
        validate_job_payload(payload.job_type, payload.payload, big_bang_id=payload.big_bang_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    key = payload.idempotency_key or default_idempotency_key(
        payload.job_type,
        payload.big_bang_id,
        payload.payload,
    )
    existing = db.scalar(select(models.Job).where(models.Job.idempotency_key == key))
    if existing:
        enqueue_recoverable_job(db, existing)
        return existing
    job = models.Job(
        job_type=payload.job_type,
        queue_name=queue_name_for_job(payload.job_type),
        status="queued",
        big_bang_id=payload.big_bang_id,
        payload=payload.payload,
        idempotency_key=key,
        queued_at=datetime.now(timezone.utc),
        available_at=datetime.now(timezone.utc),
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(models.Job).where(models.Job.idempotency_key == key))
        if existing:
            enqueue_recoverable_job(db, existing)
            return existing
        raise HTTPException(status_code=409, detail="job idempotency key conflict") from None
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="could not create job") from exc
    publish_job_status_best_effort(job)
    enqueue_recoverable_job(db, job, force=True)
    return job


@router.get("/queue-health", response_model=dict[str, Any])
def get_queue_health(db: Session = Depends(get_db)):
    return queue_health_snapshot(db)


@router.get("/queues", response_model=dict[str, Any])
def get_queues() -> dict[str, Any]:
    try:
        inspect = celery_app_module.celery_app.control.inspect()
        active = inspect.active() or {}
        reserved = inspect.reserved() or {}
        scheduled = inspect.scheduled() or {}
        stats = {queue: {"active": 0, "reserved": 0, "scheduled": 0} for queue in KNOWN_QUEUES}
        for task_sets, key in ((active, "active"), (reserved, "reserved"), (scheduled, "scheduled")):
            for tasks in task_sets.values():
                for task in tasks or []:
                    queue = ((task.get("delivery_info") or {}).get("routing_key")) or "multiverse_ticks"
                    stats.setdefault(queue, {"active": 0, "reserved": 0, "scheduled": 0})
                    stats[queue][key] += 1
        return {
            "degraded": False,
            "queues": [{"queue": queue, **stats[queue]} for queue in sorted(stats)],
        }
    except Exception as exc:
        return {
            "degraded": True,
            "error": str(exc),
            "queues": [
                {"queue": queue, "active": 0, "reserved": 0, "scheduled": 0}
                for queue in KNOWN_QUEUES
            ],
        }


@router.get("/workers", response_model=dict[str, Any])
def get_workers() -> dict[str, Any]:
    try:
        inspect = celery_app_module.celery_app.control.inspect()
        stats = inspect.stats() or {}
        return {
            "degraded": False,
            "workers": [{"worker": name, "stats": payload} for name, payload in stats.items()],
        }
    except Exception as exc:
        return {"degraded": True, "error": str(exc), "workers": []}


@router.post("/recover-stale", response_model=dict[str, Any])
def recover_stale_jobs_route(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    summary = recover_stale_running_jobs(db, enqueue=enqueue_job, limit=limit)
    commit_or_500(db)
    return summary


@router.get("/{job_id}", response_model=JobResponse, operation_id="canonical_get_job")
def get_job(job_id: UUID, db: Session = Depends(get_db)):
    return require(db, models.Job, job_id)


@router.post("/{job_id}/claim", response_model=JobResponse)
def claim_job(job_id: UUID, db: Session = Depends(get_db)):
    job = require(db, models.Job, job_id)
    try:
        claimed = claim_job_for_execution(db, job)
    except JobNotRunnableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not claimed:
        raise HTTPException(status_code=409, detail=f"job {job.id} is not claimable")
    commit_or_500(db)
    db.refresh(job)
    publish_job_status_best_effort(job)
    return job


@router.post("/{job_id}/pause", response_model=JobResponse)
def pause_job_route(job_id: UUID, db: Session = Depends(get_db)):
    job = require(db, models.Job, job_id)
    try:
        pause_job(db, job)
    except JobNotRunnableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    commit_or_500(db)
    db.refresh(job)
    publish_job_status_best_effort(job)
    return job


@router.post("/{job_id}/resume", response_model=JobResponse)
def resume_job_route(job_id: UUID, db: Session = Depends(get_db)):
    job = require(db, models.Job, job_id)
    try:
        resume_job(db, job)
    except JobNotRunnableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    commit_or_500(db)
    enqueue_recoverable_job(db, job, force=True)
    db.refresh(job)
    publish_job_status_best_effort(job)
    return job


@router.post("/{job_id}/interrupt", response_model=JobResponse)
def interrupt_job_route(job_id: UUID, db: Session = Depends(get_db)):
    job = require(db, models.Job, job_id)
    try:
        interrupt_job(db, job)
    except JobNotRunnableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    commit_or_500(db)
    db.refresh(job)
    publish_job_status_best_effort(job)
    return job


@router.post("/{job_id}/requeue", response_model=JobResponse)
def requeue_job_route(job_id: UUID, db: Session = Depends(get_db)):
    job = require(db, models.Job, job_id)
    try:
        requeue_job(db, job)
    except JobNotRunnableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    commit_or_500(db)
    enqueue_recoverable_job(db, job, force=True)
    db.refresh(job)
    publish_job_status_best_effort(job)
    return job


@router.post("/{job_id}/retry", response_model=JobResponse)
def retry_job_route(
    job_id: UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    job = require(db, models.Job, job_id)
    try:
        requeue_job(db, job)
    except JobNotRunnableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    commit_or_500(db)
    if not enqueue_recoverable_job(db, job, force=True):
        schedule_local_fallback(background_tasks, job.id)
    db.refresh(job)
    publish_job_status_best_effort(job)
    return job


@router.post("/{job_id}/cancel", response_model=JobResponse)
def cancel_job_route(job_id: UUID, db: Session = Depends(get_db)):
    job = require(db, models.Job, job_id)
    if job.status in {"succeeded", "failed", "cancelled", "interrupted"}:
        raise HTTPException(status_code=409, detail=f"job {job.id} is already terminal")
    job.status = "cancelled"
    job.finished_at = datetime.now(timezone.utc)
    commit_or_500(db)
    db.refresh(job)
    publish_job_status_best_effort(job)
    return job


@router.post("/{job_id}/run", response_model=JobResponse)
def run_job(
    job_id: UUID,
    background_tasks: BackgroundTasks,
    inline: bool = True,
    db: Session = Depends(get_db),
):
    job = require(db, models.Job, job_id)
    if not inline:
        if job.status in {"failed", "interrupted"}:
            try:
                requeue_job(db, job)
            except JobNotRunnableError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            commit_or_500(db)
            publish_job_status_best_effort(job)
        if job.status not in {"queued", "running"}:
            raise HTTPException(status_code=409, detail=f"job {job.id} is {job.status}; cannot dispatch")
        schedule_local_fallback(background_tasks, job.id)
        return job
    try:
        execute_job(db, job, commit_running=True)
    except JobNotRunnableError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    commit_or_500(db)
    publish_job_status_best_effort(job)
    if job.status == "failed":
        raise HTTPException(status_code=500, detail="job execution failed")
    return job


@router.post("/queues/{queue}/pause", response_model=dict[str, Any])
async def pause_queue(queue: str) -> dict[str, Any]:
    redis = get_redis_client()
    await redis.set(f"jobs:queue:{queue}:paused", "1")
    return {"queue": queue, "paused": True}


@router.post("/queues/{queue}/resume", response_model=dict[str, Any])
async def resume_queue(queue: str) -> dict[str, Any]:
    redis = get_redis_client()
    await redis.delete(f"jobs:queue:{queue}:paused")
    return {"queue": queue, "paused": False}


def enqueue_recoverable_job(db: Session, job: models.Job, *, force: bool = False) -> bool:
    if not force and not job_should_enqueue_for_retry(job):
        return True
    try:
        enqueue_job(job.id)
    except Exception:
        job.error = "enqueue failed; job remains queued; fix the queue or run it explicitly"
        commit_or_500(db)
        return False
    if job.error:
        job.error = None
        commit_or_500(db)
    return True


def schedule_local_fallback(background_tasks: BackgroundTasks | None, job_id: UUID) -> None:
    if background_tasks is not None:
        background_tasks.add_task(run_job_local_fallback, job_id)


def publish_job_status_best_effort(job: models.Job) -> None:
    try:
        asyncio.run(
            publish_job_status_changed(
                job_id=str(job.id),
                job_type=job.job_type,
                status=job.status,
                queue=job.queue_name,
                error=job.error,
            )
        )
    except Exception:
        pass


def run_job_local_fallback(job_id: UUID) -> None:
    db = SessionLocal()
    try:
        job = db.get(models.Job, job_id)
        if not job:
            return
        execute_job(db, job, commit_running=True)
        db.commit()
    finally:
        db.close()
