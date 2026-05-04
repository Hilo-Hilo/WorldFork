from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import TickSnapshotOut
from app.api.utils import require
from app.db import models
from app.db.session import get_db
from app.domains.tick.tick_bundles import hydrate_tick_snapshot_for_read
from app.domains.tick.timing import tick_timing_payload

router = APIRouter(prefix="/ticks", tags=["ticks"])


@router.get("/{tick_snapshot_id}", response_model=TickSnapshotOut)
def get(tick_snapshot_id: UUID, db: Session = Depends(get_db)):
    return hydrate_tick_snapshot_for_read(db, require(db, models.TickSnapshot, tick_snapshot_id))


@router.get("/{tick_snapshot_id}/details")
def details(tick_snapshot_id: UUID, db: Session = Depends(get_db)):
    tick = hydrate_tick_snapshot_for_read(db, require(db, models.TickSnapshot, tick_snapshot_id))
    return {"tick": tick, "final_bundle": tick.final_bundle}


@router.get("/{tick_snapshot_id}/reasoning-traces")
def reasoning(tick_snapshot_id: UUID, db: Session = Depends(get_db)):
    require(db, models.TickSnapshot, tick_snapshot_id)
    return db.scalars(select(models.ReasoningTrace).where(models.ReasoningTrace.tick_snapshot_id == tick_snapshot_id)).all()


@router.get("/{tick_snapshot_id}/events")
def events(tick_snapshot_id: UUID, db: Session = Depends(get_db)):
    tick = require(db, models.TickSnapshot, tick_snapshot_id)
    return db.scalars(select(models.Event).where(models.Event.multiverse_id == tick.multiverse_id, models.Event.scheduled_tick == tick.tick_index)).all()


@router.get("/{tick_snapshot_id}/social")
def social(tick_snapshot_id: UUID, db: Session = Depends(get_db)):
    tick = require(db, models.TickSnapshot, tick_snapshot_id)
    posts = db.scalars(select(models.SocialPost).where(models.SocialPost.multiverse_id == tick.multiverse_id, models.SocialPost.tick_index == tick.tick_index)).all()
    oasis = db.scalars(select(models.OASISAction).where(models.OASISAction.multiverse_id == tick.multiverse_id, models.OASISAction.tick_index == tick.tick_index)).all()
    return {"posts": posts, "oasis_actions": oasis}


@router.get("/{tick_snapshot_id}/tool-calls")
def tool_calls(tick_snapshot_id: UUID, db: Session = Depends(get_db)):
    require(db, models.TickSnapshot, tick_snapshot_id)
    return db.scalars(select(models.ToolCall).where(models.ToolCall.tick_snapshot_id == tick_snapshot_id)).all()


@router.get("/{tick_snapshot_id}/runtime")
def runtime(tick_snapshot_id: UUID, db: Session = Depends(get_db)):
    require(db, models.TickSnapshot, tick_snapshot_id)
    executions = db.scalars(
        select(models.TickExecution)
        .where(models.TickExecution.tick_snapshot_id == tick_snapshot_id)
        .order_by(models.TickExecution.created_at.asc())
    ).all()
    payload = []
    for execution in executions:
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
        payload.append(
            {
                "id": execution.id,
                "status": execution.status,
                "tick_index": execution.tick_index,
                "queue_job_id": execution.queue_job_id,
                "runtime_meta": execution.runtime_meta,
                "started_at": execution.started_at,
                "finished_at": execution.finished_at,
                "interrupted_at": execution.interrupted_at,
                "nodes": [_node_payload(node) for node in nodes],
                "checkpoints": [_checkpoint_payload(checkpoint) for checkpoint in checkpoints],
                "attempts": [_attempt_payload(attempt) for attempt in attempts],
            }
        )
    return {"tick_snapshot_id": tick_snapshot_id, "executions": payload}


@router.get("/{tick_snapshot_id}/timing")
def timing(tick_snapshot_id: UUID, db: Session = Depends(get_db)):
    tick = require(db, models.TickSnapshot, tick_snapshot_id)
    return tick_timing_payload(db, tick)


def _node_payload(node: models.ExecutionNode) -> dict:
    return {
        "id": node.id,
        "node_key": node.node_key,
        "node_kind": node.node_kind,
        "status": node.status,
        "checkpoint_order": node.checkpoint_order,
        "input_payload": node.input_payload,
        "output_payload": node.output_payload,
        "started_at": node.started_at,
        "finished_at": node.finished_at,
        "interrupted_at": node.interrupted_at,
    }


def _checkpoint_payload(checkpoint: models.TickCheckpoint) -> dict:
    return {
        "id": checkpoint.id,
        "execution_node_id": checkpoint.execution_node_id,
        "checkpoint_key": checkpoint.checkpoint_key,
        "checkpoint_order": checkpoint.checkpoint_order,
        "status": checkpoint.status,
        "payload": checkpoint.payload,
        "started_at": checkpoint.started_at,
        "finished_at": checkpoint.finished_at,
        "interrupted_at": checkpoint.interrupted_at,
    }


def _attempt_payload(attempt: models.NodeAttempt) -> dict:
    return {
        "id": attempt.id,
        "execution_node_id": attempt.execution_node_id,
        "attempt_number": attempt.attempt_number,
        "status": attempt.status,
        "provider": attempt.provider,
        "model": attempt.model,
        "error": attempt.error,
        "meta": attempt.meta,
        "started_at": attempt.started_at,
        "finished_at": attempt.finished_at,
        "interrupted_at": attempt.interrupted_at,
    }


@router.get("/{tick_snapshot_id}/emotion-observability")
def emotion(tick_snapshot_id: UUID, db: Session = Depends(get_db)):
    require(db, models.TickSnapshot, tick_snapshot_id)
    return db.scalars(select(models.EmotionGraphSnapshot).where(models.EmotionGraphSnapshot.tick_snapshot_id == tick_snapshot_id)).all()


@router.get("/{tick_snapshot_id}/graph-deltas")
def graph_deltas(tick_snapshot_id: UUID, db: Session = Depends(get_db)):
    tick = require(db, models.TickSnapshot, tick_snapshot_id)
    return db.scalars(select(models.GraphSnapshot).where(models.GraphSnapshot.multiverse_id == tick.multiverse_id, models.GraphSnapshot.tick_index == tick.tick_index)).all()


@router.get("/{tick_snapshot_id}/sociology-signals")
def sociology(tick_snapshot_id: UUID, db: Session = Depends(get_db)):
    tick = require(db, models.TickSnapshot, tick_snapshot_id)
    return db.scalars(select(models.SociologySignal).where(models.SociologySignal.multiverse_id == tick.multiverse_id, models.SociologySignal.tick_index == tick.tick_index)).all()


@router.get("/{tick_snapshot_id}/god-review")
def god_review(tick_snapshot_id: UUID, db: Session = Depends(get_db)):
    require(db, models.TickSnapshot, tick_snapshot_id)
    review = db.scalar(select(models.GodAgentReview).where(models.GodAgentReview.tick_snapshot_id == tick_snapshot_id))
    if not review:
        raise HTTPException(status_code=404, detail="GodAgentReview not found")
    return review
