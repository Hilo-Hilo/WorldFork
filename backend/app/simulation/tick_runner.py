from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import build_clock_context
from app.core.labels import tick_label
from app.db import models
from app.llm.prompt_builder import build_agent_prompt_context
from app.runtime import NodeKind, TickRuntimeState, build_tick_graph
from app.runtime.control import tool_call_node_key
from app.runtime.node_runner import TickNodeSpec
from app.runtime.validation import validate_node_output
from app.simulation.agent_engine import apply_social_actions, queue_agent_events, run_actor_decision
from app.simulation.cohort_engine import (
    generate_emergence_candidates,
    generate_merge_candidates,
    generate_split_candidates,
)
from app.simulation.emotion_observability_engine import update_emotion_observability_graphs
from app.simulation.event_engine import execute_due_events, load_due_events, summarize_executed_events
from app.simulation.god_agent import review_provisional_tick
from app.simulation.god_tools import execute_tool_call
from app.simulation.graph_engine import build_graph_prompt_summary, update_graph_layers
from app.simulation.sociology_engine import run_sociology_update
from app.storage.artifact_store import ArtifactStore


UNFINISHED_TICK_STATUSES = {"running", "provisional"}
RUNNABLE_MULTIVERSE_STATUSES = {"active", "candidate"}
TERMINAL_MULTIVERSE_STATUSES = {"completed", "terminated"}


@dataclass(frozen=True)
class TickContext:
    run_id: str
    universe_id: str
    tick: int
    attempt_number: int = 1


async def run_tick(
    ctx: TickContext,
    *,
    session: Any = None,
    ledger: Any = None,
    routing: Any = None,
    limiter: Any = None,
    memory: Any = None,
    dispatcher: Any = None,
) -> dict:
    """Compatibility async wrapper for legacy Celery/local-runner callers.

    The rewrite's canonical tick runner is synchronous and DB-canonical. Legacy
    task envelopes still call ``run_tick(ctx, ...)``; route them into the
    canonical runner when they carry UUID multiverse ids instead of crashing on
    the removed V1 ``TickContext`` contract.
    """

    del session, ledger, routing, limiter, memory, dispatcher

    def _run() -> dict:
        from app.db import models as current_models
        from app.db.session import SessionLocal

        try:
            multiverse_id = UUID(str(ctx.universe_id))
        except ValueError:
            return {
                "status": "failed",
                "error": f"multiverse {ctx.universe_id!r} is not a canonical UUID",
            }

        db = SessionLocal()
        try:
            multiverse = db.get(current_models.Multiverse, multiverse_id)
            if multiverse is None:
                return {"status": "failed", "error": f"multiverse {ctx.universe_id!r} not found"}
            tick = run_next_tick(
                db,
                multiverse=multiverse,
                idempotency_key=f"{ctx.universe_id}:tick:{ctx.tick}:attempt:{ctx.attempt_number}",
            )
            if tick.tick_index != ctx.tick:
                raise ValueError(
                    f"requested tick {ctx.tick} but canonical runner returned tick {tick.tick_index}"
                )
            db.commit()
            return {
                "status": "completed" if tick.status == "final" else tick.status,
                "tick_snapshot_id": str(tick.id),
                "tick": tick.tick_index,
                "ui_label": tick.ui_label,
            }
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    return await asyncio.to_thread(_run)


def run_next_tick(
    db: Session,
    *,
    multiverse: models.Multiverse,
    idempotency_key: str | None = None,
    force: bool = False,
    queue_job: models.Job | None = None,
) -> models.TickSnapshot:
    locked_multiverse = db.scalar(
        select(models.Multiverse)
        .where(models.Multiverse.id == multiverse.id)
        .with_for_update()
    )
    if locked_multiverse:
        multiverse = locked_multiverse

    big_bang = db.get(models.BigBang, multiverse.big_bang_id)
    if big_bang and big_bang.status == "paused":
        raise ValueError("big bang is paused")
    if multiverse.status not in RUNNABLE_MULTIVERSE_STATUSES:
        raise ValueError(f"multiverse is {multiverse.status}; only active or candidate timelines can run")

    latest_tick = db.scalar(
        select(models.TickSnapshot)
        .where(models.TickSnapshot.multiverse_id == multiverse.id)
        .order_by(models.TickSnapshot.tick_index.desc())
        .limit(1)
    )
    config = db.scalar(
        select(models.BigBangConfig)
        .where(models.BigBangConfig.big_bang_id == multiverse.big_bang_id)
        .order_by(models.BigBangConfig.version.desc())
        .limit(1)
    )
    simulation_config = (config.simulation_config or {}) if config else {}
    branch_policy = (config.branch_policy or {}) if config else {}
    max_ticks = simulation_config.get("max_ticks", 12)

    if latest_tick and latest_tick.status in UNFINISHED_TICK_STATUSES:
        tick = latest_tick
        next_index = tick.tick_index
    else:
        next_index = 0 if latest_tick is None else latest_tick.tick_index + 1
        if next_index > max_ticks:
            multiverse.status = "completed"
            multiverse.report_status = "ready"
            db.flush()
            if latest_tick is not None:
                return latest_tick
            raise ValueError("multiverse has reached max_ticks")
        key = idempotency_key or f"{multiverse.id}:tick:{next_index}"
        existing = db.scalar(
            select(models.TickSnapshot).where(
                models.TickSnapshot.big_bang_id == multiverse.big_bang_id,
                models.TickSnapshot.multiverse_id == multiverse.id,
                models.TickSnapshot.idempotency_key == key,
            )
        )
        if existing:
            if existing.status in UNFINISHED_TICK_STATUSES:
                tick = existing
                next_index = tick.tick_index
            elif not force:
                return existing
            else:
                key = _force_idempotency_key(db, multiverse=multiverse, base_key=key, tick_index=next_index)
                tick = None
        else:
            tick = None
        if tick is None:
            tick = _create_running_tick(
                db,
                multiverse=multiverse,
                tick_index=next_index,
                idempotency_key=key,
            )

    execution = _get_or_create_tick_execution(db, tick=tick, multiverse=multiverse, queue_job=queue_job)
    outputs = _completed_checkpoint_payloads(db, execution)
    clock = build_clock_context(next_index, simulation_config.get("tick_duration", "1 day"))
    prior_influences = db.scalars(
        select(models.SociologyPromptInfluence).where(
            models.SociologyPromptInfluence.multiverse_id == multiverse.id,
            models.SociologyPromptInfluence.applies_to_tick_index == next_index,
        )
    ).all()
    prompt_context = build_agent_prompt_context(
        clock_context=clock,
        current_state=multiverse.state or {},
        sociology_prompt_influences=[item.influence for item in prior_influences],
    )

    tool_call_keys = _tool_call_keys_from_outputs(outputs)
    while True:
        plan = _build_runtime_plan(db, multiverse=multiverse, tick=tick, tool_call_keys=tool_call_keys)
        nodes, checkpoints = _ensure_runtime_rows(db, execution=execution, plan=plan)
        restart_with_dynamic_tools = False

        for node_key in plan.node_order:
            spec = plan.node_specs[node_key]
            node = nodes[node_key]
            if spec.kind is NodeKind.INTERRUPT_CHECK:
                _complete_observable_node(db, node, input_payload={"after": spec.upstream})
                if _job_interrupt_requested(db, queue_job):
                    _mark_execution_interrupted(
                        db,
                        execution=execution,
                        tick=tick,
                        nodes=nodes,
                        checkpoints=checkpoints,
                        queue_job=queue_job,
                    )
                    return tick
                continue
            if spec.kind is NodeKind.BARRIER:
                _complete_observable_node(db, node, input_payload={"actor_nodes": list(plan.actor_nodes)})
                continue
            if spec.kind is NodeKind.STATE_COMMIT:
                _commit_runtime_state(
                    db,
                    execution=execution,
                    node=node,
                    tick=tick,
                    multiverse=multiverse,
                    outputs=outputs,
                )
                return tick

            checkpoint = checkpoints[node_key]
            if checkpoint.status == "complete":
                outputs[node_key] = checkpoint.payload or {}
                continue

            try:
                with db.begin_nested():
                    payload, attempt = _run_checkpoint_node(
                        db,
                        spec=spec,
                        node=node,
                        checkpoint=checkpoint,
                        big_bang=big_bang,
                        multiverse=multiverse,
                        tick=tick,
                        tick_index=next_index,
                        clock_text=clock.as_prompt_text(),
                        prompt_context=prompt_context,
                        branch_policy=branch_policy,
                        outputs=outputs,
                    )
                    validation = validate_node_output(spec.kind.value, payload)
                    if not validation.ok:
                        raise ValueError(
                            f"runtime node {node_key} failed validation: {', '.join(validation.errors)}"
                        )
                    payload = {
                        **_runtime_jsonable(payload),
                        "validation": validation.model_dump(exclude={"payload"}),
                    }
                    _complete_checkpoint(db, node=node, checkpoint=checkpoint, attempt=attempt, payload=payload)
                    outputs[node_key] = payload
                    execution.runtime_meta = {
                        **(execution.runtime_meta or {}),
                        "completed_checkpoints": sorted(outputs),
                        "current_node": node_key,
                    }
                    db.flush()
            except Exception as exc:
                _fail_checkpoint(db, node=node, checkpoint=checkpoint, error=str(exc))
                raise

            if spec.kind is NodeKind.GOD_REVIEW:
                next_tool_call_keys = _tool_call_keys_from_review_payload(payload.get("review_payload", {}))
                if tuple(next_tool_call_keys) != tuple(tool_call_keys):
                    tool_call_keys = next_tool_call_keys
                    restart_with_dynamic_tools = True
                    break

        if not restart_with_dynamic_tools:
            break

    return tick


def _create_running_tick(
    db: Session,
    *,
    multiverse: models.Multiverse,
    tick_index: int,
    idempotency_key: str,
) -> models.TickSnapshot:
    tick = models.TickSnapshot(
        big_bang_id=multiverse.big_bang_id,
        multiverse_id=multiverse.id,
        tick_index=tick_index,
        ui_label=tick_label(multiverse.ui_label, tick_index),
        status="running",
        provisional_bundle={},
        final_bundle={},
        summary=f"Tick {tick_index} simulation in progress for {multiverse.ui_label}.",
        idempotency_key=idempotency_key,
    )
    try:
        with db.begin_nested():
            db.add(tick)
            db.flush()
    except IntegrityError:
        existing = db.scalar(
            select(models.TickSnapshot).where(
                models.TickSnapshot.big_bang_id == multiverse.big_bang_id,
                models.TickSnapshot.multiverse_id == multiverse.id,
                models.TickSnapshot.tick_index == tick_index,
            )
        )
        if existing:
            return existing
        raise
    return tick


def _get_or_create_tick_execution(
    db: Session,
    *,
    tick: models.TickSnapshot,
    multiverse: models.Multiverse,
    queue_job: models.Job | None,
) -> models.TickExecution:
    execution = db.scalar(
        select(models.TickExecution)
        .where(
            models.TickExecution.big_bang_id == multiverse.big_bang_id,
            models.TickExecution.multiverse_id == multiverse.id,
            models.TickExecution.tick_index == tick.tick_index,
            models.TickExecution.tick_snapshot_id == tick.id,
        )
        .order_by(models.TickExecution.created_at.desc())
        .limit(1)
    )
    now = datetime.now(timezone.utc)
    if execution is None:
        execution = models.TickExecution(
            big_bang_id=multiverse.big_bang_id,
            multiverse_id=multiverse.id,
            tick_snapshot_id=tick.id,
            queue_job_id=queue_job.id if queue_job else None,
            tick_index=tick.tick_index,
            status="running",
            active_slot="active",
            started_at=now,
            runtime_meta={"resumed": False},
        )
        db.add(execution)
    else:
        execution.status = "running"
        execution.active_slot = "active"
        execution.started_at = execution.started_at or now
        execution.finished_at = None
        execution.interrupted_at = None
        if queue_job and execution.queue_job_id is None:
            execution.queue_job_id = queue_job.id
        execution.runtime_meta = {**(execution.runtime_meta or {}), "resumed": True}
    db.flush()
    return execution


def _build_runtime_plan(
    db: Session,
    *,
    multiverse: models.Multiverse,
    tick: models.TickSnapshot,
    tool_call_keys: list[str],
):
    actors = db.scalars(
        select(models.Actor)
        .where(models.Actor.big_bang_id == multiverse.big_bang_id, models.Actor.status == "active")
        .order_by(models.Actor.created_at.asc(), models.Actor.id.asc())
    ).all()
    state = TickRuntimeState(
        run_id=str(multiverse.big_bang_id),
        multiverse_id=str(multiverse.id),
        tick_id=str(tick.id),
        status=tick.status,
        cohort_ids=[str(actor.id) for actor in actors if actor.actor_type != "hero"],
        hero_ids=[str(actor.id) for actor in actors if actor.actor_type == "hero"],
        tool_call_keys=tool_call_keys,
    )
    return build_tick_graph(state)


def _ensure_runtime_rows(db: Session, *, execution: models.TickExecution, plan) -> tuple[dict[str, models.ExecutionNode], dict[str, models.TickCheckpoint]]:
    existing_nodes = {
        node.node_key: node
        for node in db.scalars(
            select(models.ExecutionNode).where(models.ExecutionNode.tick_execution_id == execution.id)
        )
    }
    existing_checkpoints = {
        checkpoint.checkpoint_key: checkpoint
        for checkpoint in db.scalars(
            select(models.TickCheckpoint).where(models.TickCheckpoint.tick_execution_id == execution.id)
        )
    }

    nodes: dict[str, models.ExecutionNode] = {}
    checkpoints: dict[str, models.TickCheckpoint] = {}
    checkpoint_orders = {key: index + 1 for index, key in enumerate(plan.checkpoint_order)}
    for checkpoint_key, checkpoint in existing_checkpoints.items():
        new_order = checkpoint_orders.get(checkpoint_key)
        if new_order is not None and checkpoint.checkpoint_order != new_order and checkpoint.status != "complete":
            checkpoint.checkpoint_order = -new_order
    db.flush()
    for node_key in plan.node_order:
        spec = plan.node_specs[node_key]
        node = existing_nodes.get(node_key)
        if node is None:
            node = models.ExecutionNode(
                tick_execution_id=execution.id,
                node_key=node_key,
                node_kind=spec.kind.value,
                status="pending",
                checkpoint_order=checkpoint_orders.get(node_key),
                input_payload={"upstream": list(spec.upstream), "downstream": list(spec.downstream)},
            )
            db.add(node)
            db.flush()
            existing_nodes[node_key] = node
        nodes[node_key] = node
        if spec.checkpoint:
            checkpoint = existing_checkpoints.get(node_key)
            if checkpoint is None:
                checkpoint = models.TickCheckpoint(
                    tick_execution_id=execution.id,
                    execution_node_id=node.id,
                    checkpoint_key=node_key,
                    checkpoint_order=checkpoint_orders[node_key],
                    status="pending",
                    payload={},
                )
                db.add(checkpoint)
                db.flush()
                existing_checkpoints[node_key] = checkpoint
            elif checkpoint.execution_node_id is None:
                checkpoint.execution_node_id = node.id
            checkpoints[node_key] = checkpoint
            checkpoint.checkpoint_order = checkpoint_orders[node_key]
            node.checkpoint_order = checkpoint_orders[node_key]
    db.flush()
    return nodes, checkpoints


def _completed_checkpoint_payloads(db: Session, execution: models.TickExecution) -> dict[str, dict]:
    return {
        checkpoint.checkpoint_key: checkpoint.payload or {}
        for checkpoint in db.scalars(
            select(models.TickCheckpoint).where(
                models.TickCheckpoint.tick_execution_id == execution.id,
                models.TickCheckpoint.status == "complete",
            )
        )
    }


def _run_checkpoint_node(
    db: Session,
    *,
    spec: TickNodeSpec,
    node: models.ExecutionNode,
    checkpoint: models.TickCheckpoint,
    big_bang: models.BigBang | None,
    multiverse: models.Multiverse,
    tick: models.TickSnapshot,
    tick_index: int,
    clock_text: str,
    prompt_context: dict,
    branch_policy: dict,
    outputs: dict[str, dict],
) -> tuple[dict, models.NodeAttempt]:
    now = datetime.now(timezone.utc)
    node.status = "running"
    node.started_at = node.started_at or now
    checkpoint.status = "running"
    checkpoint.started_at = checkpoint.started_at or now
    attempt = models.NodeAttempt(
        execution_node_id=node.id,
        attempt_number=_next_attempt_number(db, node),
        status="running",
        started_at=now,
        meta={"checkpoint_key": checkpoint.checkpoint_key},
    )
    db.add(attempt)
    db.flush()
    payload = _execute_checkpoint_payload(
        db,
        spec=spec,
        big_bang=big_bang,
        multiverse=multiverse,
        tick=tick,
        tick_index=tick_index,
        clock_text=clock_text,
        prompt_context=prompt_context,
        branch_policy=branch_policy,
        outputs=outputs,
    )
    if llm_call_id := payload.get("llm_call_id"):
        llm_call = db.get(models.LLMCall, llm_call_id)
        if llm_call:
            attempt.provider = llm_call.provider
            attempt.model = llm_call.model
            attempt.request_artifact_id = llm_call.request_artifact_id
            attempt.response_artifact_id = llm_call.response_artifact_id
    if spec.kind is NodeKind.TOOL_CALL:
        attempt.provider = "tool"
        attempt.model = payload.get("tool_name")
    return payload, attempt


def _execute_checkpoint_payload(
    db: Session,
    *,
    spec: TickNodeSpec,
    big_bang: models.BigBang | None,
    multiverse: models.Multiverse,
    tick: models.TickSnapshot,
    tick_index: int,
    clock_text: str,
    prompt_context: dict,
    branch_policy: dict,
    outputs: dict[str, dict],
) -> dict:
    if spec.kind in {NodeKind.COHORT_DECISION, NodeKind.HERO_DECISION}:
        if big_bang is None:
            raise ValueError("big bang not found")
        actor = db.get(models.Actor, UUID(str(spec.actor_id)))
        if actor is None:
            raise ValueError(f"actor {spec.actor_id} not found")
        return run_actor_decision(
            db,
            big_bang=big_bang,
            multiverse=multiverse,
            actor=actor,
            tick_index=tick_index,
            prompt_context=prompt_context,
        )

    if spec.kind is NodeKind.EVENT_GENERATION:
        agent_result = _agent_result_from_outputs(outputs)
        social_observations = apply_social_actions(
            db,
            big_bang_id=multiverse.big_bang_id,
            multiverse_id=multiverse.id,
            tick_index=tick_index,
            parsed_actions=agent_result["parsed_actions"],
        )
        queued_events = queue_agent_events(
            db,
            big_bang_id=multiverse.big_bang_id,
            multiverse_id=multiverse.id,
            tick_index=tick_index,
            parsed_actions=agent_result["parsed_actions"],
        )
        due_events = load_due_events(db, multiverse.id, tick_index)
        executed_events = execute_due_events(db, due_events, tick_snapshot_id=tick.id)
        event_summaries = summarize_executed_events(
            db,
            due_events,
            tick_snapshot_id=tick.id,
            big_bang_id=multiverse.big_bang_id,
            local_tick_context={"clock": clock_text, "social_observations": social_observations},
        )
        return {
            "agent_result": agent_result,
            "social_observations": social_observations,
            "queued_events": queued_events,
            "executed_events": executed_events,
            "event_summaries": event_summaries,
            "clock": clock_text,
        }

    if spec.kind is NodeKind.SOCIOLOGY_UPDATE:
        event_payload = outputs.get(NodeKind.EVENT_GENERATION.value, {})
        return {
            "sociology_result": run_sociology_update(
                db,
                big_bang_id=multiverse.big_bang_id,
                multiverse_id=multiverse.id,
                tick_index=tick_index,
                executed_events=event_payload.get("executed_events", []),
                social_observations=event_payload.get("social_observations", []),
            )
        }

    if spec.kind is NodeKind.GRAPH_UPDATE:
        event_payload = outputs.get(NodeKind.EVENT_GENERATION.value, {})
        sociology_result = outputs.get(NodeKind.SOCIOLOGY_UPDATE.value, {}).get("sociology_result", {})
        graphs = update_graph_layers(
            db,
            big_bang_id=multiverse.big_bang_id,
            multiverse_id=multiverse.id,
            tick_index=tick_index,
            social_observations=event_payload.get("social_observations", []),
            executed_events=event_payload.get("executed_events", []),
        )
        sociology_result = {
            **sociology_result,
            "graph_summary": build_graph_prompt_summary(db, multiverse_id=multiverse.id),
        }
        agent_result = event_payload.get("agent_result", {})
        emotion_graph = update_emotion_observability_graphs(
            db,
            big_bang_id=multiverse.big_bang_id,
            multiverse_id=multiverse.id,
            tick_index=tick_index,
            emotion_self_ratings=agent_result.get("emotion_self_ratings", []),
            event_summaries=event_payload.get("event_summaries", []),
        )
        split_candidates = generate_split_candidates(
            db,
            big_bang_id=multiverse.big_bang_id,
            multiverse_id=multiverse.id,
            tick_index=tick_index,
            sociology_result=sociology_result,
        )
        merge_candidates = generate_merge_candidates(
            db,
            big_bang_id=multiverse.big_bang_id,
            multiverse_id=multiverse.id,
            tick_index=tick_index,
            sociology_result=sociology_result,
        )
        emergence_candidates = generate_emergence_candidates(
            db,
            big_bang_id=multiverse.big_bang_id,
            multiverse_id=multiverse.id,
            tick_index=tick_index,
            sociology_result=sociology_result,
        )
        candidate_scores = [
            float((item.get("payload") or {}).get("score") or 0)
            for item in [*split_candidates, *merge_candidates, *emergence_candidates]
        ]
        executed_events = event_payload.get("executed_events", [])
        social_observations = event_payload.get("social_observations", [])
        queued_events = event_payload.get("queued_events", [])
        event_pressure = min(0.64, 0.24 + 0.12 * len(executed_events)) if executed_events else 0.0
        branch_score = round(max([event_pressure, *candidate_scores] or [0.0]), 4)
        idle_assessment = _assess_idle_state(
            multiverse_state=multiverse.state or {},
            branch_policy=branch_policy,
            branch_score=branch_score,
            queued_events=queued_events,
            executed_events=executed_events,
            social_observations=social_observations,
            split_candidates=split_candidates,
            merge_candidates=merge_candidates,
            emergence_candidates=emergence_candidates,
            sociology_result=sociology_result,
        )
        provisional = {
            "multiverse_id": str(multiverse.id),
            "tick_index": tick_index,
            "clock": event_payload.get("clock", clock_text),
            "agent_outputs": agent_result.get("actor_outputs", []),
            "queued_events": queued_events,
            "social_observations": social_observations,
            "executed_events": executed_events,
            "event_summaries": event_payload.get("event_summaries", []),
            "sociology_result": sociology_result,
            "graph_snapshots": graphs,
            "emotion_observability": emotion_graph,
            "split_candidates": split_candidates,
            "merge_candidates": merge_candidates,
            "emergence_candidates": emergence_candidates,
            "branch_score": branch_score,
            "idle_assessment": idle_assessment,
        }
        return {"provisional_bundle": provisional}

    if spec.kind is NodeKind.GOD_REVIEW:
        provisional = outputs.get(NodeKind.GRAPH_UPDATE.value, {}).get("provisional_bundle", {})
        tick.status = "provisional"
        tick.provisional_bundle = provisional
        tick.summary = f"Tick {tick_index} simulated for {multiverse.ui_label}."
        db.flush()
        review_payload, review_call = review_provisional_tick(db, multiverse, provisional)
        review = models.GodAgentReview(
            big_bang_id=multiverse.big_bang_id,
            multiverse_id=multiverse.id,
            tick_snapshot_id=tick.id,
            decision=review_payload["decision"],
            rationale=review_payload["rationale"],
            confidence=review_payload["confidence"],
            input_summary=review_payload["input_summary"],
            output=review_payload,
        )
        db.add(review)
        db.flush()
        return {
            "review_payload": review_payload,
            "god_review_id": str(review.id),
            "llm_call_id": str(review_call.id) if review_call else None,
        }

    if spec.kind is NodeKind.TOOL_CALL:
        call = _tool_call_for_node(spec.key, outputs)
        review_payload = outputs.get(NodeKind.GOD_REVIEW.value, {})
        tool = execute_tool_call(
            db,
            big_bang_id=multiverse.big_bang_id,
            multiverse=multiverse,
            tick_snapshot_id=tick.id,
            god_review_id=review_payload.get("god_review_id"),
            tool_name=call["tool_name"],
            arguments=call.get("arguments", {}),
            idempotency_key=call.get("idempotency_key")
            or f"god:{review_payload.get('god_review_id')}:{spec.key}:{call['tool_name']}",
        )
        return {
            "tool_name": tool.tool_name,
            "status": tool.status,
            "result": tool.result,
            "error": tool.error,
            "tool_call_id": str(tool.id),
            "idempotency_key": tool.idempotency_key,
        }

    if spec.kind is NodeKind.TICK_SUMMARY:
        provisional = outputs.get(NodeKind.GRAPH_UPDATE.value, {}).get("provisional_bundle", {})
        review_payload = outputs.get(NodeKind.GOD_REVIEW.value, {})
        god_review = review_payload.get("review_payload", {})
        tool_results = _tool_results_from_outputs(outputs)
        emotion_graph = update_emotion_observability_graphs(
            db,
            big_bang_id=multiverse.big_bang_id,
            multiverse_id=multiverse.id,
            tick_index=tick_index,
            tick_snapshot_id=tick.id,
            emotion_self_ratings=[],
            event_summaries=provisional.get("event_summaries", []),
            god_agent_review=god_review,
        )
        final = {
            **provisional,
            "god_review_id": review_payload.get("god_review_id"),
            "god_review": god_review,
            "tool_results": tool_results,
            "emotion_observability_after_god_review": emotion_graph,
        }
        if review_payload.get("llm_call_id"):
            final["god_review_llm_call_id"] = review_payload["llm_call_id"]
        return {"final_bundle": final}

    raise NotImplementedError(f"runtime node has no executor: {spec.kind.value}")


def _commit_runtime_state(
    db: Session,
    *,
    execution: models.TickExecution,
    node: models.ExecutionNode,
    tick: models.TickSnapshot,
    multiverse: models.Multiverse,
    outputs: dict[str, dict],
) -> None:
    if tick.status == "final":
        return
    final = outputs.get(NodeKind.TICK_SUMMARY.value, {}).get("final_bundle", {})
    sociology_result = final.get("sociology_result", {})
    idle_assessment = final.get("idle_assessment", {})
    multiverse.state = {
        **(multiverse.state or {}),
        "last_tick_index": tick.tick_index,
        "last_executed_events": final.get("executed_events", []),
        "last_sociology": sociology_result,
        "graph_summary": sociology_result.get("graph_summary", {}),
        "cohort_current_states": sociology_result.get("cohort_state_updates", []),
        "hero_current_states": sociology_result.get("hero_state_updates", []),
        "idle_assessment": idle_assessment,
        "idle_streak": idle_assessment.get("idle_streak", 0),
    }
    artifact = ArtifactStore().write_json(
        db,
        big_bang_id=multiverse.big_bang_id,
        relative_path=f"big_bang_{multiverse.big_bang_id}/multiverses/{multiverse.ui_label}/ticks/T{tick.tick_index}.json",
        payload=final,
        kind="tick_snapshot",
    )
    tick.status = "final"
    tick.final_bundle = final
    tick.artifact_id = artifact.id
    tick.summary = f"Tick {tick.tick_index} simulated for {multiverse.ui_label}."
    _sync_forked_children_after_tick(db, parent=multiverse, tick=tick)
    now = datetime.now(timezone.utc)
    node.status = "complete"
    node.started_at = node.started_at or now
    node.finished_at = now
    execution.status = "succeeded"
    execution.active_slot = None
    execution.finished_at = now
    execution.runtime_meta = {**(execution.runtime_meta or {}), "final_tick_snapshot_id": str(tick.id)}
    db.flush()


def _next_attempt_number(db: Session, node: models.ExecutionNode) -> int:
    value = db.scalar(
        select(func.count(models.NodeAttempt.id)).where(models.NodeAttempt.execution_node_id == node.id)
    )
    return int(value or 0) + 1


def _complete_checkpoint(
    db: Session,
    *,
    node: models.ExecutionNode,
    checkpoint: models.TickCheckpoint,
    attempt: models.NodeAttempt,
    payload: dict,
) -> None:
    now = datetime.now(timezone.utc)
    attempt.status = "complete"
    attempt.meta = {**(attempt.meta or {}), "validation": payload.get("validation", {})}
    attempt.finished_at = now
    node.status = "complete"
    node.output_payload = payload
    node.finished_at = now
    checkpoint.status = "complete"
    checkpoint.payload = payload
    checkpoint.finished_at = now
    db.flush()


def _fail_checkpoint(
    db: Session,
    *,
    node: models.ExecutionNode,
    checkpoint: models.TickCheckpoint,
    error: str,
) -> None:
    now = datetime.now(timezone.utc)
    node.status = "failed"
    node.finished_at = now
    checkpoint.status = "failed"
    checkpoint.finished_at = now
    db.add(
        models.OperationLog(
            big_bang_id=None,
            tick_execution_id=node.tick_execution_id,
            execution_node_id=node.id,
            checkpoint_id=checkpoint.id,
            event_type="runtime_checkpoint_failed",
            level="error",
            body={"node_key": node.node_key, "error": error},
        )
    )
    db.flush()


def _complete_observable_node(db: Session, node: models.ExecutionNode, *, input_payload: dict | None = None) -> None:
    if node.status == "complete":
        return
    now = datetime.now(timezone.utc)
    node.status = "complete"
    node.input_payload = input_payload or node.input_payload or {}
    node.started_at = node.started_at or now
    node.finished_at = now
    db.flush()


def _mark_execution_interrupted(
    db: Session,
    *,
    execution: models.TickExecution,
    tick: models.TickSnapshot,
    nodes: dict[str, models.ExecutionNode],
    checkpoints: dict[str, models.TickCheckpoint],
    queue_job: models.Job | None,
) -> None:
    now = datetime.now(timezone.utc)
    execution.status = "interrupted"
    execution.active_slot = None
    execution.interrupted_at = now
    execution.finished_at = now
    tick.summary = f"{tick.ui_label} interrupted; resume will continue from the first unfinished checkpoint."
    for node in nodes.values():
        if node.status not in {"complete", "failed"}:
            node.status = "interrupted"
            node.interrupted_at = now
    for checkpoint in checkpoints.values():
        if checkpoint.status != "complete":
            checkpoint.status = "interrupted"
            checkpoint.interrupted_at = now
    if queue_job is not None:
        queue_job.status = "interrupted"
        queue_job.interrupted_at = now
        queue_job.finished_at = now
        queue_job.lease_owner = None
        queue_job.lease_expires_at = None
        queue_job.last_heartbeat_at = None
        queue_job.result = {
            **(queue_job.result or {}),
            "status": "interrupted",
            "tick_execution_id": str(execution.id),
            "tick_snapshot_id": str(tick.id),
        }
    db.add(
        models.OperationLog(
            big_bang_id=execution.big_bang_id,
            multiverse_id=execution.multiverse_id,
            tick_execution_id=execution.id,
            event_type="runtime_execution_interrupted",
            level="warning",
            body={"resume": "first_unfinished_checkpoint"},
        )
    )
    db.flush()


def _job_interrupt_requested(db: Session, queue_job: models.Job | None) -> bool:
    if queue_job is None:
        return False
    db.flush()
    status = db.scalar(select(models.Job.status).where(models.Job.id == queue_job.id))
    if status == "interrupt_requested":
        return True
    return getattr(queue_job, "status", None) == "interrupt_requested"


def _agent_result_from_outputs(outputs: dict[str, dict]) -> dict:
    actor_outputs = []
    parsed_actions = []
    emotion_self_ratings = []
    for key, payload in outputs.items():
        if not key.startswith(("cohort:", "hero:")):
            continue
        actor_outputs.append(payload.get("actor_output", {}))
        parsed_actions.extend(payload.get("parsed_actions", []))
        emotion_self_ratings.extend(payload.get("emotion_self_ratings", []))
    return {
        "actor_outputs": actor_outputs,
        "parsed_actions": parsed_actions,
        "emotion_self_ratings": emotion_self_ratings,
    }


def _tool_call_keys_from_outputs(outputs: dict[str, dict]) -> list[str]:
    review = outputs.get(NodeKind.GOD_REVIEW.value, {}).get("review_payload", {})
    return _tool_call_keys_from_review_payload(review)


def _tool_call_keys_from_review_payload(review_payload: dict) -> list[str]:
    calls = review_payload.get("tool_calls") if isinstance(review_payload, dict) else []
    return [
        tool_call_node_key(index, str(call.get("tool_name") or "unknown"))
        for index, call in enumerate(calls or [])
        if isinstance(call, dict)
    ]


def _tool_call_for_node(node_key: str, outputs: dict[str, dict]) -> dict:
    try:
        index = int(node_key.split(":", 2)[1])
    except (IndexError, ValueError) as exc:
        raise ValueError(f"invalid tool call node key: {node_key}") from exc
    calls = outputs.get(NodeKind.GOD_REVIEW.value, {}).get("review_payload", {}).get("tool_calls", [])
    if index >= len(calls) or not isinstance(calls[index], dict):
        raise ValueError(f"tool call index {index} is not available")
    return calls[index]


def _tool_results_from_outputs(outputs: dict[str, dict]) -> list[dict]:
    return [
        {
            "tool_name": payload.get("tool_name"),
            "status": payload.get("status"),
            "result": payload.get("result", {}),
            "error": payload.get("error"),
            "tool_call_id": payload.get("tool_call_id"),
        }
        for key, payload in sorted(outputs.items())
        if key.startswith("tool_call:")
    ]


def _runtime_jsonable(value):
    if isinstance(value, dict):
        return {str(key): _runtime_jsonable(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_runtime_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_runtime_jsonable(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    return value


def _sync_forked_children_after_tick(
    db: Session,
    *,
    parent: models.Multiverse,
    tick: models.TickSnapshot,
) -> None:
    children = db.scalars(
        select(models.Multiverse).where(
            models.Multiverse.parent_multiverse_id == parent.id,
            models.Multiverse.fork_tick_index == tick.tick_index,
        )
    ).all()
    for child in children:
        branch_state = deepcopy((child.state or {}).get("branch") or {})
        child.state = deepcopy(parent.state or {})
        child.state["branch"] = branch_state or {
            "parent_multiverse_id": str(parent.id),
            "fork_tick_index": tick.tick_index,
            "reason": child.branch_reason,
        }
        child_tick = db.scalar(
            select(models.TickSnapshot).where(
                models.TickSnapshot.multiverse_id == child.id,
                models.TickSnapshot.tick_index == tick.tick_index,
            )
        )
        if child_tick is None:
            child_tick = models.TickSnapshot(
                big_bang_id=parent.big_bang_id,
                multiverse_id=child.id,
                tick_index=tick.tick_index,
                ui_label=tick_label(child.ui_label, tick.tick_index),
                idempotency_key=f"{child.id}:tick:{tick.tick_index}:inherited",
            )
            db.add(child_tick)
        child_tick.status = tick.status
        child_tick.provisional_bundle = _inherited_tick_bundle(
            tick.provisional_bundle, parent=parent, child=child, tick=tick
        )
        child_tick.final_bundle = _inherited_tick_bundle(tick.final_bundle, parent=parent, child=child, tick=tick)
        child_tick.summary = tick.summary
        child_tick.artifact_id = None


def _force_idempotency_key(
    db: Session,
    *,
    multiverse: models.Multiverse,
    base_key: str,
    tick_index: int,
) -> str:
    prefix = f"{base_key}:force:{tick_index}"
    for attempt in range(1, 100):
        candidate = f"{prefix}:{attempt}"
        if len(candidate) > 160:
            candidate = f"{base_key[:120]}:force:{tick_index}:{attempt}"
        exists = db.scalar(
            select(models.TickSnapshot).where(
                models.TickSnapshot.big_bang_id == multiverse.big_bang_id,
                models.TickSnapshot.multiverse_id == multiverse.id,
                models.TickSnapshot.idempotency_key == candidate,
            )
        )
        if not exists:
            return candidate
    raise ValueError("unable to allocate unique forced tick idempotency key")


def _inherited_tick_bundle(
    bundle: dict | None,
    *,
    parent: models.Multiverse,
    child: models.Multiverse,
    tick: models.TickSnapshot,
) -> dict:
    inherited = deepcopy(bundle or {})
    inherited["multiverse_id"] = str(child.id)
    inherited["inherited_from"] = {
        "source_multiverse_id": str(parent.id),
        "source_tick_snapshot_id": str(tick.id),
        "source_ui_label": tick.ui_label,
    }
    return inherited


def _assess_idle_state(
    *,
    multiverse_state: dict,
    branch_policy: dict,
    branch_score: float,
    queued_events: list[dict],
    executed_events: list[dict],
    social_observations: list[dict],
    split_candidates: list[dict],
    merge_candidates: list[dict],
    emergence_candidates: list[dict],
    sociology_result: dict,
) -> dict:
    threshold = int(branch_policy.get("idle_termination_ticks", 5) or 5)
    metrics = sociology_result.get("metrics", {}) if isinstance(sociology_result, dict) else {}
    candidate_count = len(split_candidates) + len(merge_candidates) + len(emergence_candidates)
    low_motion = (
        branch_score <= 0.4
        and not queued_events
        and len(executed_events) <= 1
        and candidate_count == 0
        and len(social_observations) <= int(branch_policy.get("idle_social_observation_limit", 8) or 8)
        and float(metrics.get("mobilization_readiness") or 0) < 0.5
    )
    previous = int(multiverse_state.get("idle_streak") or 0)
    idle_streak = previous + 1 if low_motion else 0
    return {
        "is_idle_tick": low_motion,
        "idle_streak": idle_streak,
        "termination_threshold": threshold,
        "should_terminate": idle_streak >= threshold,
        "evidence": {
            "branch_score": branch_score,
            "queued_events": len(queued_events),
            "executed_events": len(executed_events),
            "social_observations": len(social_observations),
            "candidate_count": candidate_count,
            "mobilization_readiness": metrics.get("mobilization_readiness"),
        },
    }
