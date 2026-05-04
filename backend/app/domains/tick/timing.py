from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models


def duration_seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round(max(0.0, (end - start).total_seconds()), 4)


def tick_timing_payload(db: Session, tick: models.TickSnapshot) -> dict[str, Any]:
    executions = db.scalars(
        select(models.TickExecution)
        .where(models.TickExecution.tick_snapshot_id == tick.id)
        .order_by(models.TickExecution.created_at.asc())
    ).all()
    llm_calls = _llm_calls_for_tick(db, tick)
    return {
        "tick_snapshot_id": str(tick.id),
        "big_bang_id": str(tick.big_bang_id),
        "multiverse_id": str(tick.multiverse_id),
        "tick_index": tick.tick_index,
        "ui_label": tick.ui_label,
        "status": tick.status,
        "created_at": tick.created_at,
        "updated_at": tick.updated_at,
        "started_at": _first_non_null([execution.started_at for execution in executions]) or tick.created_at,
        "finished_at": _last_non_null([execution.finished_at for execution in executions])
        or (tick.updated_at if tick.status == "final" else None),
        "duration_seconds": duration_seconds(
            _first_non_null([execution.started_at for execution in executions]) or tick.created_at,
            _last_non_null([execution.finished_at for execution in executions])
            or (tick.updated_at if tick.status == "final" else None),
        ),
        "executions": [_execution_timing_payload(db, execution) for execution in executions],
        "llm_calls": [_llm_call_timing_payload(call) for call in llm_calls],
        "llm_summary": _llm_summary(llm_calls),
    }


def run_timing_payload(db: Session, big_bang: models.BigBang) -> dict[str, Any]:
    ticks = db.scalars(
        select(models.TickSnapshot)
        .where(models.TickSnapshot.big_bang_id == big_bang.id)
        .order_by(models.TickSnapshot.created_at.asc())
    ).all()
    initializer_calls = db.scalars(
        select(models.LLMCall)
        .where(
            models.LLMCall.big_bang_id == big_bang.id,
            models.LLMCall.purpose.like("initializer%"),
        )
        .order_by(models.LLMCall.created_at.asc())
    ).all()
    jobs = db.scalars(
        select(models.Job)
        .where(models.Job.big_bang_id == big_bang.id)
        .order_by(models.Job.created_at.asc())
    ).all()
    return {
        "big_bang_id": str(big_bang.id),
        "name": big_bang.name,
        "status": big_bang.status,
        "created_at": big_bang.created_at,
        "updated_at": big_bang.updated_at,
        "duration_seconds": duration_seconds(big_bang.created_at, big_bang.updated_at),
        "initializer": {
            "started_at": _first_non_null([call.created_at for call in initializer_calls]),
            "finished_at": _last_non_null([call.updated_at for call in initializer_calls]),
            "duration_seconds": duration_seconds(
                _first_non_null([call.created_at for call in initializer_calls]),
                _last_non_null([call.updated_at for call in initializer_calls]),
            ),
            "llm_calls": [_llm_call_timing_payload(call) for call in initializer_calls],
            "llm_summary": _llm_summary(initializer_calls),
        },
        "jobs": [_job_timing_payload(job) for job in jobs],
        "ticks": [tick_timing_payload(db, tick) for tick in ticks],
    }


def _execution_timing_payload(db: Session, execution: models.TickExecution) -> dict[str, Any]:
    nodes = db.scalars(
        select(models.ExecutionNode)
        .where(models.ExecutionNode.tick_execution_id == execution.id)
        .order_by(
            models.ExecutionNode.checkpoint_order.is_(None),
            models.ExecutionNode.checkpoint_order.asc(),
            models.ExecutionNode.created_at.asc(),
        )
    ).all()
    checkpoints = db.scalars(
        select(models.TickCheckpoint)
        .where(models.TickCheckpoint.tick_execution_id == execution.id)
        .order_by(models.TickCheckpoint.checkpoint_order.asc())
    ).all()
    attempts = db.scalars(
        select(models.NodeAttempt)
        .where(models.NodeAttempt.execution_node_id.in_([node.id for node in nodes]))
        .order_by(models.NodeAttempt.created_at.asc())
    ).all() if nodes else []
    return {
        "id": str(execution.id),
        "tick_index": execution.tick_index,
        "status": execution.status,
        "active_slot": execution.active_slot,
        "queue_job_id": str(execution.queue_job_id) if execution.queue_job_id else None,
        "created_at": execution.created_at,
        "updated_at": execution.updated_at,
        "started_at": execution.started_at,
        "finished_at": execution.finished_at,
        "interrupted_at": execution.interrupted_at,
        "duration_seconds": duration_seconds(execution.started_at, execution.finished_at),
        "stage_timings": [_node_timing_payload(node) for node in nodes],
        "checkpoint_timings": [_checkpoint_timing_payload(checkpoint) for checkpoint in checkpoints],
        "attempt_timings": [_attempt_timing_payload(attempt) for attempt in attempts],
    }


def _node_timing_payload(node: models.ExecutionNode) -> dict[str, Any]:
    return {
        "id": str(node.id),
        "node_key": node.node_key,
        "node_kind": node.node_kind,
        "status": node.status,
        "checkpoint_order": node.checkpoint_order,
        "created_at": node.created_at,
        "updated_at": node.updated_at,
        "started_at": node.started_at,
        "finished_at": node.finished_at,
        "interrupted_at": node.interrupted_at,
        "duration_seconds": duration_seconds(node.started_at, node.finished_at),
    }


def _checkpoint_timing_payload(checkpoint: models.TickCheckpoint) -> dict[str, Any]:
    return {
        "id": str(checkpoint.id),
        "checkpoint_key": checkpoint.checkpoint_key,
        "checkpoint_order": checkpoint.checkpoint_order,
        "status": checkpoint.status,
        "started_at": checkpoint.started_at,
        "finished_at": checkpoint.finished_at,
        "interrupted_at": checkpoint.interrupted_at,
        "duration_seconds": duration_seconds(checkpoint.started_at, checkpoint.finished_at),
    }


def _attempt_timing_payload(attempt: models.NodeAttempt) -> dict[str, Any]:
    return {
        "id": str(attempt.id),
        "execution_node_id": str(attempt.execution_node_id),
        "attempt_number": attempt.attempt_number,
        "status": attempt.status,
        "provider": attempt.provider,
        "model": attempt.model,
        "started_at": attempt.started_at,
        "finished_at": attempt.finished_at,
        "interrupted_at": attempt.interrupted_at,
        "duration_seconds": duration_seconds(attempt.started_at, attempt.finished_at),
    }


def _llm_call_timing_payload(call: models.LLMCall) -> dict[str, Any]:
    return {
        "id": str(call.id),
        "purpose": call.purpose,
        "provider": call.provider,
        "model": call.model,
        "status": call.status,
        "created_at": call.created_at,
        "updated_at": call.updated_at,
        "duration_seconds": duration_seconds(call.created_at, call.updated_at),
        "attempts": call.meta.get("attempts") if isinstance(call.meta, dict) else None,
        "last_error": call.meta.get("last_error") if isinstance(call.meta, dict) else None,
    }


def _job_timing_payload(job: models.Job) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "job_type": job.job_type,
        "status": job.status,
        "created_at": job.created_at,
        "queued_at": job.queued_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "interrupted_at": job.interrupted_at,
        "duration_seconds": duration_seconds(job.started_at, job.finished_at),
    }


def _llm_calls_for_tick(db: Session, tick: models.TickSnapshot) -> list[models.LLMCall]:
    query = (
        select(models.LLMCall)
        .where(models.LLMCall.big_bang_id == tick.big_bang_id)
        .order_by(models.LLMCall.created_at.asc())
    )
    calls = db.scalars(query).all()
    start = tick.created_at
    end = tick.updated_at
    marker = f"_tick_{tick.tick_index}"
    event_prefix = "event_summary_"
    god_prefix = f"god_review_{tick.multiverse_id}_tick_{tick.tick_index}"
    return [
        call
        for call in calls
        if marker in call.purpose
        or call.purpose.startswith(event_prefix)
        or call.purpose.startswith(god_prefix)
        or (start is not None and end is not None and start <= call.created_at <= end)
    ]


def _llm_summary(calls: list[models.LLMCall]) -> dict[str, Any]:
    durations = [
        value
        for value in (duration_seconds(call.created_at, call.updated_at) for call in calls)
        if value is not None
    ]
    return {
        "total_calls": len(calls),
        "status_counts": _counts([call.status for call in calls]),
        "total_duration_seconds": round(sum(durations), 4),
        "max_duration_seconds": max(durations) if durations else None,
        "models": _counts([f"{call.provider}/{call.model}" for call in calls]),
    }


def _counts(values: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return result


def _first_non_null(values: list[Any]) -> Any | None:
    for value in values:
        if value is not None:
            return value
    return None


def _last_non_null(values: list[Any]) -> Any | None:
    for value in reversed(values):
        if value is not None:
            return value
    return None
