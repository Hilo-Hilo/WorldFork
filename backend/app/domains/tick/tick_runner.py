from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import build_clock_context
from app.core.config import settings
from app.core.labels import tick_label
from app.db import models
from app.llm.audit import mark_stale_running_llm_calls_failed
from app.llm.prompt_builder import build_agent_prompt_context
from app.runtime import NodeKind, TickRuntimeState, build_tick_graph
from app.runtime.control import tool_call_node_key
from app.runtime.node_runner import TickNodeSpec
from app.runtime.validation import validate_node_output
from app.domains.actor.agent_engine import (
    EventValidationError,
    apply_social_actions,
    queue_agent_events,
    run_actor_decision,
    validate_and_repair_event_actions,
)
from app.domains.sociology.cohort_engine import (
    generate_emergence_candidates,
    generate_merge_candidates,
    generate_split_candidates,
)
from app.domains.sociology.emotion_observability_engine import update_emotion_observability_graphs
from app.domains.event.event_engine import (
    build_event_queue_prompt_context,
    execute_due_events,
    load_due_events,
    summarize_executed_events,
)
from app.domains.endpoint_ledger.service import (
    apply_god_endpoint_updates,
    latest_endpoint_ledger_prompt_payload,
    seed_endpoint_ledger,
)
from app.domains.governance.god_agent import review_provisional_tick
from app.domains.governance.god_tools import execute_tool_call
from app.domains.sociology.graph_engine import build_graph_prompt_summary, update_graph_layers
from app.domains.multiverse.runtime_config import branch_policy_for_multiverse, simulation_config_for_multiverse
from app.domains.sociology.sociology_engine import run_sociology_update
from app.domains.tick.tick_bundles import inherited_tick_bundle_ref
from app.storage.artifact_store import ArtifactStore


UNFINISHED_TICK_STATUSES = {"running", "provisional"}
RUNNABLE_MULTIVERSE_STATUSES = {"active", "candidate"}
TERMINAL_MULTIVERSE_STATUSES = {"completed", "terminated"}
TICK_EXECUTION_STALE_AFTER_SECONDS = 15 * 60


def run_next_tick(
    db: Session,
    *,
    multiverse: models.Multiverse,
    idempotency_key: str | None = None,
    force: bool = False,
    queue_job: models.Job | None = None,
) -> models.TickSnapshot:
    current_multiverse = db.get(models.Multiverse, multiverse.id)
    if current_multiverse:
        multiverse = current_multiverse

    big_bang = db.get(models.BigBang, multiverse.big_bang_id)
    if big_bang and big_bang.status == "paused":
        raise ValueError("big bang is paused")
    if multiverse.status not in RUNNABLE_MULTIVERSE_STATUSES:
        raise ValueError(f"multiverse is {multiverse.status}; only active or candidate timelines can run")
    if big_bang is not None:
        seed_endpoint_ledger(db, big_bang=big_bang, multiverse=multiverse)

    latest_tick = db.scalar(
        select(models.TickSnapshot)
        .where(models.TickSnapshot.multiverse_id == multiverse.id)
        .order_by(models.TickSnapshot.tick_index.desc())
        .limit(1)
    )
    simulation_config = simulation_config_for_multiverse(db, multiverse)
    branch_policy = branch_policy_for_multiverse(db, multiverse)
    max_ticks = simulation_config.get("max_ticks", 12)

    if latest_tick and latest_tick.status in UNFINISHED_TICK_STATUSES:
        tick = latest_tick
        next_index = tick.tick_index
        active_execution = _active_tick_execution(db, tick=tick, multiverse=multiverse)
        if active_execution is not None:
            if _tick_execution_is_stale(active_execution):
                _mark_tick_execution_stale(db, execution=active_execution)
                _commit_progress(db, queue_job=queue_job)
            elif queue_job is None and not force:
                _commit_progress(db)
                return tick
    else:
        next_index = 0 if latest_tick is None else latest_tick.tick_index + 1
        if next_index > max_ticks:
            multiverse.status = "completed"
            multiverse.report_status = "ready"
            multiverse.ended_at = multiverse.ended_at or datetime.now(timezone.utc)
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
        event_queue=build_event_queue_prompt_context(
            db,
            multiverse_id=multiverse.id,
            tick_index=next_index,
        ),
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
                    _commit_progress(db, queue_job=queue_job)
                    return tick
                _commit_progress(db, queue_job=queue_job)
                continue
            if spec.kind is NodeKind.BARRIER:
                _complete_observable_node(db, node, input_payload={"actor_nodes": list(plan.actor_nodes)})
                _commit_progress(db, queue_job=queue_job)
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
                _commit_progress(db, queue_job=queue_job)
                return tick

            checkpoint = checkpoints[node_key]
            if spec.kind is NodeKind.COHORT_DECISION:
                if checkpoint.status == "complete":
                    outputs[node_key] = checkpoint.payload or {}
                    continue
                _run_pending_cohort_decisions(
                    db,
                    execution=execution,
                    nodes=nodes,
                    checkpoints=checkpoints,
                    plan=plan,
                    big_bang=big_bang,
                    multiverse=multiverse,
                    tick=tick,
                    tick_index=next_index,
                    prompt_context=prompt_context,
                    outputs=outputs,
                    queue_job=queue_job,
                )
                continue
            if checkpoint.status == "complete":
                outputs[node_key] = checkpoint.payload or {}
                continue

            node_id = node.id
            checkpoint_id = checkpoint.id
            execution_id = execution.id
            try:
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
                    queue_job=queue_job,
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
                _commit_progress(db, queue_job=queue_job)
            except Exception as exc:
                _record_checkpoint_failure(
                    db,
                    execution_id=execution_id,
                    node_id=node_id,
                    checkpoint_id=checkpoint_id,
                    tick_index=next_index,
                    error=str(exc),
                    event_validation_error=exc if isinstance(exc, EventValidationError) else None,
                )
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


def _active_tick_execution(
    db: Session,
    *,
    tick: models.TickSnapshot,
    multiverse: models.Multiverse,
) -> models.TickExecution | None:
    return db.scalar(
        select(models.TickExecution)
        .where(
            models.TickExecution.big_bang_id == multiverse.big_bang_id,
            models.TickExecution.multiverse_id == multiverse.id,
            models.TickExecution.tick_index == tick.tick_index,
            models.TickExecution.tick_snapshot_id == tick.id,
            models.TickExecution.status == "running",
            models.TickExecution.active_slot == "active",
        )
        .order_by(models.TickExecution.updated_at.desc())
        .limit(1)
    )


def _tick_execution_is_stale(execution: models.TickExecution) -> bool:
    reference = execution.updated_at or execution.started_at or execution.created_at
    if reference is None:
        return False
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return reference <= datetime.now(timezone.utc) - timedelta(seconds=TICK_EXECUTION_STALE_AFTER_SECONDS)


def _mark_tick_execution_stale(db: Session, *, execution: models.TickExecution) -> None:
    now = datetime.now(timezone.utc)
    stale_llm_calls = mark_stale_running_llm_calls_failed(
        db,
        big_bang_id=execution.big_bang_id,
        stale_after_seconds=TICK_EXECUTION_STALE_AFTER_SECONDS,
        now=now,
        reason="stale tick execution reclaimed",
    )
    execution.status = "failed"
    execution.active_slot = None
    execution.finished_at = now
    execution.runtime_meta = {
        **(execution.runtime_meta or {}),
        "stale_reclaimed_at": now.isoformat(),
        "stale_reclaim_reason": "active execution exceeded stale cutoff",
    }
    nodes = db.scalars(
        select(models.ExecutionNode).where(
            models.ExecutionNode.tick_execution_id == execution.id,
            models.ExecutionNode.status == "running",
        )
    ).all()
    checkpoints = db.scalars(
        select(models.TickCheckpoint).where(
            models.TickCheckpoint.tick_execution_id == execution.id,
            models.TickCheckpoint.status == "running",
        )
    ).all()
    attempts = db.scalars(
        select(models.NodeAttempt)
        .join(models.ExecutionNode)
        .where(
            models.ExecutionNode.tick_execution_id == execution.id,
            models.NodeAttempt.status == "running",
        )
    ).all()
    for node in nodes:
        node.status = "failed"
        node.finished_at = now
    for checkpoint in checkpoints:
        checkpoint.status = "failed"
        checkpoint.finished_at = now
    for attempt in attempts:
        attempt.status = "failed"
        attempt.error = "stale tick execution reclaimed"
        attempt.finished_at = now
    db.add(
        models.OperationLog(
            big_bang_id=execution.big_bang_id,
            multiverse_id=execution.multiverse_id,
            tick_execution_id=execution.id,
            event_type="runtime_execution_stale_reclaimed",
            level="warning",
            body={
                "stale_after_seconds": TICK_EXECUTION_STALE_AFTER_SECONDS,
                "stale_llm_calls_reclaimed": stale_llm_calls,
            },
        )
    )
    db.flush()


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
    queue_job: models.Job | None,
) -> tuple[dict, models.NodeAttempt]:
    attempt = _start_checkpoint_attempt(db, node=node, checkpoint=checkpoint, queue_job=queue_job)
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
    _attach_attempt_provider_metadata(db, attempt=attempt, spec=spec, payload=payload)
    return payload, attempt


def _start_checkpoint_attempt(
    db: Session,
    *,
    node: models.ExecutionNode,
    checkpoint: models.TickCheckpoint,
    queue_job: models.Job | None,
) -> models.NodeAttempt:
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
    _commit_progress(db, queue_job=queue_job)
    return attempt


def _attach_attempt_provider_metadata(
    db: Session,
    *,
    attempt: models.NodeAttempt,
    spec: TickNodeSpec,
    payload: dict,
) -> None:
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


def _run_pending_cohort_decisions(
    db: Session,
    *,
    execution: models.TickExecution,
    nodes: dict[str, models.ExecutionNode],
    checkpoints: dict[str, models.TickCheckpoint],
    plan,
    big_bang: models.BigBang | None,
    multiverse: models.Multiverse,
    tick: models.TickSnapshot,
    tick_index: int,
    prompt_context: dict,
    outputs: dict[str, dict],
    queue_job: models.Job | None,
) -> None:
    if big_bang is None:
        raise ValueError("big bang not found")
    pending_keys = [
        node_key
        for node_key in plan.cohort_nodes
        if checkpoints[node_key].status != "complete"
    ]
    if not pending_keys:
        return

    bind = db.get_bind()
    worker_func = _execute_actor_decision_payload_in_worker
    max_workers = max(1, int(settings.max_parallel_cohort_decisions))
    if bind.dialect.name == "sqlite" and getattr(worker_func, "__module__", None) == __name__:
        max_workers = 1
    for batch_start in range(0, len(pending_keys), max_workers):
        batch_keys = pending_keys[batch_start : batch_start + max_workers]
        attempts: dict[str, models.NodeAttempt] = {}
        for node_key in batch_keys:
            attempts[node_key] = _start_checkpoint_attempt(
                db,
                node=nodes[node_key],
                checkpoint=checkpoints[node_key],
                queue_job=queue_job,
            )

        futures = {}
        with ThreadPoolExecutor(max_workers=len(batch_keys), thread_name_prefix="cohort-decision") as pool:
            for node_key in batch_keys:
                spec = plan.node_specs[node_key]
                futures[
                    pool.submit(
                        worker_func,
                        bind,
                        big_bang_id=big_bang.id,
                        multiverse_id=multiverse.id,
                        actor_id=UUID(str(spec.actor_id)),
                        tick_index=tick_index,
                        prompt_context=deepcopy(prompt_context),
                    )
                ] = node_key

            failures: list[tuple[str, Exception]] = []
            completed_payloads: dict[str, dict] = {}
            for future in as_completed(futures):
                node_key = futures[future]
                try:
                    completed_payloads[node_key] = future.result()
                except Exception as exc:
                    failures.append((node_key, exc))

        for node_key in batch_keys:
            if node_key not in completed_payloads:
                continue
            spec = plan.node_specs[node_key]
            payload = completed_payloads[node_key]
            validation = validate_node_output(spec.kind.value, payload)
            if not validation.ok:
                failures.append(
                    (
                        node_key,
                        ValueError(
                            f"runtime node {node_key} failed validation: {', '.join(validation.errors)}"
                        ),
                    )
                )
                continue
            payload = {
                **_runtime_jsonable(payload),
                "validation": validation.model_dump(exclude={"payload"}),
            }
            _attach_attempt_provider_metadata(db, attempt=attempts[node_key], spec=spec, payload=payload)
            _complete_checkpoint(
                db,
                node=nodes[node_key],
                checkpoint=checkpoints[node_key],
                attempt=attempts[node_key],
                payload=payload,
            )
            outputs[node_key] = payload
            execution.runtime_meta = {
                **(execution.runtime_meta or {}),
                "completed_checkpoints": sorted(outputs),
                "current_node": node_key,
                "max_parallel_cohort_decisions": max_workers,
            }
            db.flush()

        _commit_progress(db, queue_job=queue_job)
        if failures:
            node_key, exc = failures[0]
            _record_checkpoint_failure(
                db,
                execution_id=execution.id,
                node_id=nodes[node_key].id,
                checkpoint_id=checkpoints[node_key].id,
                tick_index=tick_index,
                error=str(exc),
                event_validation_error=exc if isinstance(exc, EventValidationError) else None,
            )
            raise exc


def _execute_actor_decision_payload_in_worker(
    bind,
    *,
    big_bang_id: UUID,
    multiverse_id: UUID,
    actor_id: UUID,
    tick_index: int,
    prompt_context: dict,
) -> dict:
    worker_db = Session(bind=bind, expire_on_commit=False)
    try:
        big_bang = worker_db.get(models.BigBang, big_bang_id)
        multiverse = worker_db.get(models.Multiverse, multiverse_id)
        actor = worker_db.get(models.Actor, actor_id)
        if big_bang is None:
            raise ValueError("big bang not found")
        if multiverse is None:
            raise ValueError(f"multiverse {multiverse_id} not found")
        if actor is None:
            raise ValueError(f"actor {actor_id} not found")
        payload = run_actor_decision(
            worker_db,
            big_bang=big_bang,
            multiverse=multiverse,
            actor=actor,
            tick_index=tick_index,
            prompt_context=prompt_context,
            release_db_connection_before_llm=True,
        )
        worker_db.commit()
        return payload
    except Exception:
        worker_db.rollback()
        raise
    finally:
        worker_db.close()


def _record_checkpoint_failure(
    db: Session,
    *,
    execution_id: UUID,
    node_id: UUID,
    checkpoint_id: UUID,
    tick_index: int,
    error: str,
    event_validation_error: EventValidationError | None,
) -> None:
    db.rollback()
    execution = db.get(models.TickExecution, execution_id)
    node = db.get(models.ExecutionNode, node_id)
    checkpoint = db.get(models.TickCheckpoint, checkpoint_id)
    if execution is None or node is None or checkpoint is None:
        return
    now = datetime.now(timezone.utc)
    attempt = db.scalar(
        select(models.NodeAttempt)
        .where(
            models.NodeAttempt.execution_node_id == node.id,
            models.NodeAttempt.status == "running",
        )
        .order_by(models.NodeAttempt.created_at.desc())
        .limit(1)
    )
    if attempt is not None:
        attempt.status = "failed"
        attempt.error = error
        attempt.finished_at = now
    if event_validation_error is not None:
        _log_final_event_validation_failure(
            db,
            execution=execution,
            node=node,
            checkpoint=checkpoint,
            tick_index=tick_index,
            error=error,
            invalid_events=event_validation_error.invalid_events,
            attempts=event_validation_error.attempts,
        )
    _fail_checkpoint(db, node=node, checkpoint=checkpoint, error=error)
    execution.status = "failed"
    execution.active_slot = None
    execution.finished_at = now
    _commit_progress(db)


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
        if big_bang is None:
            raise ValueError("big bang not found")
        agent_result = validate_and_repair_event_actions(
            db,
            big_bang=big_bang,
            multiverse=multiverse,
            tick_index=tick_index,
            prompt_context=prompt_context,
            agent_result=agent_result,
            max_retries=3,
        )
        social_observations = apply_social_actions(
            db,
            big_bang_id=multiverse.big_bang_id,
            multiverse_id=multiverse.id,
            tick_index=tick_index,
            parsed_actions=agent_result["parsed_actions"],
        )
        simulation_config = simulation_config_for_multiverse(db, multiverse)
        forecast_metadata = (big_bang.scenario_input or {}).get("forecast_metadata") if isinstance(big_bang.scenario_input, dict) else {}
        max_scheduled_tick = None
        if isinstance(forecast_metadata, dict) and forecast_metadata.get("tick_horizon_policy") in {None, "deadline_aware"}:
            max_ticks = simulation_config.get("max_ticks")
            if max_ticks is not None:
                max_scheduled_tick = int(max_ticks)
        queued_events = queue_agent_events(
            db,
            big_bang_id=multiverse.big_bang_id,
            multiverse_id=multiverse.id,
            tick_index=tick_index,
            parsed_actions=agent_result["parsed_actions"],
            max_scheduled_tick=max_scheduled_tick,
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
            "event_validation": agent_result.get("event_validation", {}),
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
        simulation_config = simulation_config_for_multiverse(db, multiverse)
        max_ticks = int(simulation_config.get("max_ticks") or 0)
        forecast_review_context = _forecast_review_context(
            big_bang=big_bang,
            prompt_context=prompt_context,
            executed_events=executed_events,
            event_summaries=event_payload.get("event_summaries", []),
            social_observations=social_observations,
        )
        provisional = {
            "multiverse_id": str(multiverse.id),
            "tick_index": tick_index,
            "clock": event_payload.get("clock", clock_text),
            "endpoint_ledger": latest_endpoint_ledger_prompt_payload(
                db,
                big_bang_id=multiverse.big_bang_id,
                multiverse_id=multiverse.id,
            ),
            "agent_outputs": agent_result.get("actor_outputs", []),
            "queued_events": queued_events,
            "social_observations": social_observations,
            "executed_events": executed_events,
            "event_summaries": event_payload.get("event_summaries", []),
            "event_validation": event_payload.get("event_validation", {}),
            "sociology_result": sociology_result,
            "graph_snapshots": graphs,
            "emotion_observability": emotion_graph,
            "forecast_review_context": forecast_review_context,
            "split_candidates": split_candidates,
            "merge_candidates": merge_candidates,
            "emergence_candidates": emergence_candidates,
            "branch_score": branch_score,
            "idle_assessment": idle_assessment,
            "final_tick_context": {
                "is_final_allowed_tick": bool(max_ticks and tick_index >= max_ticks),
                "max_ticks": max_ticks or None,
                "current_tick_index": tick_index,
                "ledger_instruction": (
                    "At the final allowed tick for a deadline-aware forecast, settle explicit yes/no candidate "
                    "endpoints from the simulated path evidence and the original forecast-card source packet. "
                    "Do not mark yes/no insufficient_ticks merely because external proof is unavailable inside "
                    "the simulation; reserve insufficient_ticks for genuinely unmodeled outcomes after making "
                    "a best-effort binary settlement. Do not open new branches at the final horizon."
                ),
            },
        }
        return {"provisional_bundle": provisional}

    if spec.kind is NodeKind.GOD_REVIEW:
        provisional = outputs.get(NodeKind.GRAPH_UPDATE.value, {}).get("provisional_bundle", {})
        tick.status = "provisional"
        tick.provisional_bundle = provisional
        tick.summary = f"Tick {tick_index} simulated for {multiverse.ui_label}."
        db.flush()
        review_payload, review_call = review_provisional_tick(
            db,
            multiverse,
            provisional,
            tick_snapshot_id=tick.id,
        )
        review_payload = _prune_final_tick_branch_tool_calls(review_payload, provisional)
        ledger = apply_god_endpoint_updates(
            db,
            big_bang_id=multiverse.big_bang_id,
            multiverse_id=multiverse.id,
            tick_snapshot_id=tick.id,
            review_payload=review_payload,
        )
        if ledger is not None:
            review_payload = {**review_payload, "endpoint_ledger_version_id": str(ledger.id)}
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


def _commit_progress(db: Session, *, queue_job: models.Job | None = None) -> None:
    """Persist checkpoint progress before any long external wait.

    Tick execution is intentionally resumable. Committing between checkpoints
    prevents request-scoped transactions from holding multiverse/tick locks
    while provider calls are in flight.
    """
    if queue_job is not None and queue_job.status == "running":
        now = datetime.now(timezone.utc)
        queue_job.last_heartbeat_at = now
        queue_job.lease_expires_at = now + timedelta(seconds=TICK_EXECUTION_STALE_AFTER_SECONDS)
    db.commit()


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


def _log_final_event_validation_failure(
    db: Session,
    *,
    execution: models.TickExecution,
    node: models.ExecutionNode,
    checkpoint: models.TickCheckpoint,
    tick_index: int,
    error: str,
    invalid_events: list[dict],
    attempts: list[dict],
) -> None:
    for item in invalid_events:
        db.add(
            models.OperationLog(
                big_bang_id=execution.big_bang_id,
                multiverse_id=execution.multiverse_id,
                tick_execution_id=execution.id,
                execution_node_id=node.id,
                checkpoint_id=checkpoint.id,
                event_type="god_event_sanity_rejected",
                level="error",
                body={
                    "tick_index": tick_index,
                    "final": True,
                    "error": error,
                    "attempts": attempts,
                    **item,
                },
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


def _prune_final_tick_branch_tool_calls(review_payload: dict, provisional: dict) -> dict:
    final_tick_context = provisional.get("final_tick_context") if isinstance(provisional, dict) else {}
    if not isinstance(final_tick_context, dict) or not final_tick_context.get("is_final_allowed_tick"):
        return review_payload
    calls = review_payload.get("tool_calls") if isinstance(review_payload, dict) else []
    if not isinstance(calls, list):
        return review_payload
    retained = [call for call in calls if not (isinstance(call, dict) and call.get("tool_name") == "create_branch")]
    if len(retained) == len(calls):
        return review_payload
    rationale = str(review_payload.get("rationale") or "")
    suffix = (
        "create_branch suppressed at final allowed tick; terminal endpoint settlement should use the endpoint ledger "
        "instead of opening a zero-horizon branch."
    )
    return {
        **review_payload,
        "decision": "continue" if review_payload.get("decision") == "branch" else review_payload.get("decision", "continue"),
        "rationale": f"{rationale} {suffix}".strip(),
        "tool_calls": retained,
    }


def _forecast_review_context(
    *,
    big_bang: models.BigBang | None,
    prompt_context: dict,
    executed_events: list,
    event_summaries: list,
    social_observations: list,
) -> dict:
    if big_bang is None or not isinstance(big_bang.scenario_input, dict):
        return {}
    scenario = big_bang.scenario_input
    forecast_metadata = scenario.get("forecast_metadata") if isinstance(scenario.get("forecast_metadata"), dict) else {}
    candidate_endpoints = scenario.get("candidate_endpoints") if isinstance(scenario.get("candidate_endpoints"), list) else []
    if not forecast_metadata and not candidate_endpoints:
        return {}
    compact_candidates = _compact_candidate_endpoints(candidate_endpoints)
    compact_source_packet = _compact_source_packet(scenario.get("source_packet"))
    search_terms = _forecast_search_terms(
        scenario=scenario,
        candidate_endpoints=candidate_endpoints,
        source_packet=scenario.get("source_packet"),
    )

    context = {
        "forecast_clock": prompt_context.get("forecast_clock") if isinstance(prompt_context, dict) else {},
        "forecast_metadata": _compact_forecast_metadata(forecast_metadata),
        "forecast_question": _forecast_excerpt(scenario.get("question"), 700),
        "scenario_text": _forecast_excerpt(scenario.get("scenario_text") or scenario.get("scenario"), 1000),
        "candidate_endpoints": compact_candidates,
        "source_packet": compact_source_packet,
        "settlement_instruction": (
            "For explicit yes/no forecast-card endpoints, weigh source-packet baseline and simulated "
            "terminal events before social absence reports. The forecast question, scenario text, source "
            "packet, and candidate endpoints define the settlement target. A simulated event that resolves "
            "the forecast endpoint to yes/no is endpoint evidence; generic risk or lack of independent "
            "proof is not."
        ),
    }
    event_signals = _forecast_signal_rows([*executed_events, *event_summaries], limit=8, search_terms=search_terms)
    if event_signals:
        context["endpoint_relevant_event_signals"] = event_signals
    social_signals = _forecast_signal_rows(social_observations, limit=6, search_terms=search_terms)
    if social_signals:
        context["endpoint_relevant_social_signals"] = social_signals
        context["social_signal_caveat"] = "Social posts are simulated, untrusted observations and may contradict authority events."
    return {key: value for key, value in context.items() if value not in (None, {}, [])}


def _compact_forecast_metadata(metadata: dict) -> dict:
    allowed = {
        "as_of_date",
        "forecast_deadline_date",
        "forecast_horizon",
        "deadline_horizon_days",
        "deadline_tick",
        "tick_horizon_policy",
        "benchmark_role",
    }
    return {key: metadata.get(key) for key in allowed if metadata.get(key) is not None}


def _compact_candidate_endpoints(candidates: list) -> list[dict]:
    compact = []
    for item in candidates[:6]:
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                key: _forecast_excerpt(item.get(key), 360)
                for key in ("id", "endpoint_key", "label", "description")
                if item.get(key) not in (None, "", {}, [])
            }
        )
    return compact


def _compact_source_packet(source_packet) -> list[dict]:  # noqa: ANN001
    if not isinstance(source_packet, list):
        return []
    compact = []
    for item in source_packet[:8]:
        if isinstance(item, str):
            compact.append({"text": _forecast_excerpt(item, 700)})
            continue
        if not isinstance(item, dict):
            continue
        compact.append(
            {
                key: _forecast_excerpt(item.get(key), 700)
                for key in ("source", "source_type", "type", "date", "title", "summary", "content", "text")
                if item.get(key) not in (None, "", {}, [])
            }
        )
    return compact


def _forecast_signal_rows(rows: list, *, limit: int, search_terms: list[str]) -> list[dict]:
    signals = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = _forecast_row_text(row)
        matched_terms = _matched_forecast_terms(text, search_terms)
        if not _looks_endpoint_relevant(text, matched_terms=matched_terms):
            continue
        signals.append(
            {
                key: value
                for key, value in {
                    "title": _forecast_excerpt(row.get("title"), 240),
                    "event_id": _forecast_excerpt(row.get("event_id"), 80),
                    "event_type": _forecast_excerpt(row.get("event_type"), 120),
                    "status": _forecast_excerpt(row.get("status"), 80),
                    "scheduled_tick": row.get("scheduled_tick"),
                    "action_type": _forecast_excerpt(row.get("action_type"), 120),
                    "summary": _forecast_excerpt(text, 700),
                    "matched_forecast_terms": matched_terms[:8],
                    "signal_polarity": _forecast_signal_polarity(text),
                }.items()
                if value not in (None, "", {}, [])
            }
        )
        if len(signals) >= limit:
            break
    return signals


def _forecast_row_text(row: dict) -> str:
    parts = [
        row.get("title"),
        row.get("summary"),
        row.get("post"),
        row.get("body"),
        row.get("description"),
    ]
    actual = row.get("actual_impact") if isinstance(row.get("actual_impact"), dict) else {}
    parts.extend([actual.get("summary"), actual.get("rationale"), actual.get("description")])
    parsed = row.get("parsed") if isinstance(row.get("parsed"), dict) else {}
    parts.extend([parsed.get("what_happened"), parsed.get("outcome")])
    return " ".join(str(part) for part in parts if part not in (None, "", {}, []))


def _looks_endpoint_relevant(text: str, *, matched_terms: list[str]) -> bool:
    lowered = text.lower()
    cues = (
        "forecast endpoint",
        "forecast question",
        "candidate endpoint",
        "deadline",
        "resolved to yes",
        "resolved to no",
        "settled as yes",
        "settled as no",
        "missed deadline",
        "missed the deadline",
    )
    if any(cue in lowered for cue in cues):
        return True
    return len(matched_terms) >= 2 or any(len(term) >= 9 for term in matched_terms)


def _forecast_signal_polarity(text: str) -> str | None:
    lowered = text.lower()
    terminal_yes = re.search(
        r"\b(resolve|resolves|resolved|settle|settles|settled)\b.{0,120}"
        r"\bforecast(?: question| endpoint)?\b.{0,120}\b(?:as|to) yes\b",
        lowered,
    )
    terminal_no = re.search(
        r"\b(resolve|resolves|resolved|settle|settles|settled)\b.{0,120}"
        r"\bforecast(?: question| endpoint)?\b.{0,120}\b(?:as|to) no\b",
        lowered,
    )
    if terminal_yes:
        return "supports_yes"
    if terminal_no or "missed the deadline" in lowered or "missed deadline" in lowered:
        return "supports_no_or_contradiction"
    return None


_FORECAST_TERM_STOPWORDS = {
    "about",
    "after",
    "against",
    "answer",
    "before",
    "benchmark",
    "candidate",
    "candidates",
    "context",
    "deadline",
    "does",
    "endpoint",
    "endpoints",
    "event",
    "forecast",
    "from",
    "hidden",
    "information",
    "occur",
    "occurs",
    "only",
    "outcome",
    "packet",
    "probability",
    "question",
    "resolved",
    "return",
    "scenario",
    "source",
    "stated",
    "text",
    "that",
    "the",
    "this",
    "through",
    "using",
    "will",
    "with",
    "yes",
    "and",
    "not",
    "no",
}


def _forecast_search_terms(*, scenario: dict, candidate_endpoints: list, source_packet) -> list[str]:  # noqa: ANN001
    parts: list[str] = []
    for key in ("question", "scenario_text", "forecast_horizon"):
        if scenario.get(key):
            parts.append(str(scenario[key]))
    for item in candidate_endpoints[:8]:
        if not isinstance(item, dict):
            continue
        for key in ("label", "description", "realization_criteria"):
            value = item.get(key)
            if isinstance(value, list):
                parts.extend(str(part) for part in value)
            elif value:
                parts.append(str(value))
    if isinstance(source_packet, list):
        for item in source_packet[:8]:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            for key in ("source", "source_type", "type", "title", "summary", "content", "text"):
                if item.get(key):
                    parts.append(str(item[key]))

    counts: dict[str, int] = {}
    order: list[str] = []
    first_index: dict[str, int] = {}
    for token in re.findall(r"[a-z0-9][a-z0-9'-]{2,}", " ".join(parts).lower()):
        term = token.strip("-'")
        if len(term) < 4 or term.isdigit() or term in _FORECAST_TERM_STOPWORDS:
            continue
        if term not in counts:
            first_index[term] = len(order)
            order.append(term)
        counts[term] = counts.get(term, 0) + 1
    order.sort(key=lambda item: (-counts[item], first_index[item]))
    return order[:32]


def _matched_forecast_terms(text: str, search_terms: list[str]) -> list[str]:
    lowered = text.lower()
    matches = []
    for term in search_terms:
        if re.search(rf"\b{re.escape(term)}\b", lowered):
            matches.append(term)
    return matches


def _forecast_excerpt(value, limit: int) -> str | None:  # noqa: ANN001
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "..."


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
            "branch_probability": child.branch_probability,
            "path_probability": child.path_probability,
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
        _ensure_tick_lineage_ref(db, parent=parent, child=child, tick=tick)
        child_tick.status = tick.status
        child_tick.provisional_bundle = inherited_tick_bundle_ref(
            parent=parent,
            child=child,
            source_tick=tick,
            bundle_field="provisional_bundle",
        )
        child_tick.final_bundle = inherited_tick_bundle_ref(
            parent=parent,
            child=child,
            source_tick=tick,
            bundle_field="final_bundle",
        )
        child_tick.summary = tick.summary
        child_tick.artifact_id = None
        _sync_forked_children_after_tick(db, parent=child, tick=child_tick)


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


def _ensure_tick_lineage_ref(
    db: Session,
    *,
    parent: models.Multiverse,
    child: models.Multiverse,
    tick: models.TickSnapshot,
) -> None:
    ref = db.scalar(
        select(models.TickLineageRef).where(
            models.TickLineageRef.child_multiverse_id == child.id,
            models.TickLineageRef.inherited_tick_index == tick.tick_index,
        )
    )
    if ref is None:
        db.add(
            models.TickLineageRef(
                child_multiverse_id=child.id,
                source_multiverse_id=parent.id,
                source_tick_snapshot_id=tick.id,
                inherited_tick_index=tick.tick_index,
                inherited_ui_label=tick_label(child.ui_label, tick.tick_index),
            )
        )
        return
    ref.source_multiverse_id = parent.id
    ref.source_tick_snapshot_id = tick.id
    ref.inherited_ui_label = tick_label(child.ui_label, tick.tick_index)


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
