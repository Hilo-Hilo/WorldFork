from __future__ import annotations

import hashlib
import json
from uuid import UUID

JOB_TYPES = {
    "initialize_big_bang",
    "run_multiverse_tick",
    "simulate_multiverse_ticks",
    "generate_multiverse_report",
    "generate_final_big_bang_report",
    "evaluate_endpoint_ledger",
    "run_big_bang_until_complete",
}

QUEUE_NAMES = {
    "initialize_big_bang": "p1",
    "run_multiverse_tick": "p0",
    "simulate_multiverse_ticks": "p0",
    "generate_multiverse_report": "p2",
    "generate_final_big_bang_report": "p2",
    "evaluate_endpoint_ledger": "p2",
    "run_big_bang_until_complete": "p1",
}


def queue_name_for_job(job_type: str) -> str:
    return QUEUE_NAMES.get(job_type, "default")


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=_json_default)


def default_idempotency_key(
    job_type: str,
    big_bang_id: UUID | str | None,
    payload: dict | None,
) -> str:
    raw = canonical_json(
        {
            "big_bang_id": str(big_bang_id) if big_bang_id is not None else None,
            "job_type": job_type,
            "payload": payload or {},
        }
    )
    return f"job:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def enqueue_job(job_id: UUID | str) -> None:
    from backend.app.workers.celery_app import celery_app

    queue_name = _queue_name_for_persisted_job(job_id) or "p1"
    celery_app.send_task("worldfork.execute_job", args=[str(job_id)], queue=queue_name)


def _queue_name_for_persisted_job(job_id: UUID | str) -> str | None:
    from app.db import models
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        job = db.get(models.Job, job_id)
        return str(job.queue_name) if job and job.queue_name else None
    finally:
        db.close()


def _json_default(value):
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")
