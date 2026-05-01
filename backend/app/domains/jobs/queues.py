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
    "initialize_big_bang": "big_bang_init",
    "run_multiverse_tick": "multiverse_ticks",
    "simulate_multiverse_ticks": "multiverse_ticks",
    "generate_multiverse_report": "reports",
    "generate_final_big_bang_report": "reports",
    "evaluate_endpoint_ledger": "reports",
    "run_big_bang_until_complete": "big_bang_control",
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
    from app.jobs.workers import run_job as run_job_actor

    run_job_actor.send(str(job_id))


def _json_default(value):
    if isinstance(value, UUID):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")
