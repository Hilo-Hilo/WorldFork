from __future__ import annotations

import types
import warnings
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.schemas import MultiverseLineageOut
from app.db import models
from app.domains.jobs import executor as job_tasks
from app.domains.multiverse import branch_engine
from app.domains.governance import god_agent, god_tools
from app.domains.tick.timing import run_timing_payload, tick_timing_payload
from app.domains.tick import tick_runner
from app.llm.schemas import LLMResponse


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Can't sort tables for DROP", category=SAWarning)
            models.Base.metadata.drop_all(engine)


def _seed_world(
    db: Session,
    *,
    max_ticks: int = 12,
    branch_policy: dict | None = None,
) -> tuple[models.BigBang, models.Multiverse]:
    big_bang = models.BigBang(
        name="Timeline safety",
        description=None,
        scenario_input={},
        status="active",
        current_config_version=1,
    )
    db.add(big_bang)
    db.flush()
    db.add(
        models.BigBangConfig(
            big_bang_id=big_bang.id,
            version=1,
            simulation_config={"max_ticks": max_ticks},
            model_config={},
            branch_policy=branch_policy or {},
        )
    )
    root = models.Multiverse(
        big_bang_id=big_bang.id,
        parent_multiverse_id=None,
        fork_tick_index=None,
        ui_label="M1",
        depth=0,
        status="active",
        branch_reason="Root timeline",
        state={},
    )
    db.add(root)
    db.flush()
    return big_bang, root


class _FakeArtifactStore:
    def write_json(self, db, *, big_bang_id, relative_path, payload, kind):
        artifact = models.Artifact(
            big_bang_id=big_bang_id,
            kind=kind,
            path=relative_path,
            content_type="application/json",
            content_hash="test",
            size_bytes=2,
            debug_only=False,
            meta={},
        )
        db.add(artifact)
        db.flush()
        return artifact


def _patch_successful_tick(monkeypatch):
    def fake_actor_decision(db, *, big_bang, multiverse, actor, tick_index, prompt_context):
        return {
            "actor_output": {"actor_id": str(actor.id), "llm_call_id": None, "parsed": {}},
            "parsed_actions": [],
            "emotion_self_ratings": [],
            "llm_call_id": None,
            "parsed": {},
        }

    monkeypatch.setattr(tick_runner, "run_actor_decision", fake_actor_decision)
    monkeypatch.setattr(
        tick_runner, "apply_social_actions", lambda *args, **kwargs: [{"body": "social"}] if kwargs.get("parsed_actions") else []
    )
    monkeypatch.setattr(tick_runner, "queue_agent_events", lambda *args, **kwargs: [])
    monkeypatch.setattr(tick_runner, "load_due_events", lambda *args, **kwargs: [])
    monkeypatch.setattr(tick_runner, "execute_due_events", lambda *args, **kwargs: [])
    monkeypatch.setattr(tick_runner, "summarize_executed_events", lambda *args, **kwargs: [])
    monkeypatch.setattr(tick_runner, "update_graph_layers", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        tick_runner,
        "run_sociology_update",
        lambda *args, **kwargs: {
            "graph_summary": {},
            "cohort_state_updates": [],
            "hero_state_updates": [],
            "metrics": {},
        },
    )
    monkeypatch.setattr(tick_runner, "update_emotion_observability_graphs", lambda *args, **kwargs: {})
    monkeypatch.setattr(tick_runner, "generate_split_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(tick_runner, "generate_merge_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(tick_runner, "generate_emergence_candidates", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        tick_runner,
        "review_provisional_tick",
        lambda *args, **kwargs: (
            {
                "decision": "continue",
                "rationale": "test",
                "confidence": 1,
                "input_summary": {},
                "tool_calls": [],
            },
            None,
        ),
    )
    monkeypatch.setattr(tick_runner, "ArtifactStore", lambda: _FakeArtifactStore())


def _load_run_orchestrator_with_report_stub():
    from app.domains.big_bang import run_orchestrator

    run_orchestrator.generate_final_big_bang_report = lambda *args, **kwargs: types.SimpleNamespace(id="final")
    run_orchestrator.generate_multiverse_report = lambda *args, **kwargs: types.SimpleNamespace(id="multiverse")
    return run_orchestrator


def test_mark_ready_for_report_completes_timeline(db):
    big_bang, root = _seed_world(db)

    call = god_tools.execute_tool_call(
        db,
        big_bang_id=big_bang.id,
        multiverse=root,
        tick_snapshot_id=None,
        god_review_id=None,
        tool_name="mark_ready_for_report",
        arguments={"reason": "endpoint ledger resolved"},
        idempotency_key="mark-ready-completes",
    )
    db.commit()

    assert call.status == "succeeded"
    assert call.result == {"status": "ready_for_report", "multiverse_status": "completed"}
    assert root.report_status == "ready"
    assert root.status == "completed"
    assert root.ended_at is not None


def test_complete_universe_decision_normalizes_to_mark_ready_tool(db):
    _, root = _seed_world(db)

    calls = god_agent._prepare_tool_calls(
        db,
        multiverse=root,
        provisional_bundle={"branch_score": 0.0},
        parsed={"decision": "complete_universe", "tool_calls": []},
        tick_index=3,
    )

    assert calls == [
        {
            "tool_name": "mark_ready_for_report",
            "arguments": {"reason": "God Agent marked the timeline complete."},
            "idempotency_key": f"god:{root.id}:tick:3:mark_ready_for_report",
        }
    ]


def test_final_tick_skips_heuristic_branch_tool(db):
    _, root = _seed_world(db, max_ticks=5)

    calls = god_agent._prepare_tool_calls(
        db,
        multiverse=root,
        provisional_bundle={
            "branch_score": 0.99,
            "final_tick_context": {
                "is_final_allowed_tick": True,
                "max_ticks": 5,
                "current_tick_index": 5,
            },
        },
        parsed={"decision": "branch", "tool_calls": []},
        tick_index=5,
    )

    assert [call["tool_name"] for call in calls] == ["continue_timeline"]
    assert "Final allowed tick reached" in calls[0]["arguments"]["reason"]


def test_final_tick_removes_explicit_branch_tool_before_execution(db):
    _, root = _seed_world(db, max_ticks=5)

    calls = god_agent._prepare_tool_calls(
        db,
        multiverse=root,
        provisional_bundle={
            "branch_score": 0.99,
            "final_tick_context": {
                "is_final_allowed_tick": True,
                "max_ticks": 5,
                "current_tick_index": 5,
            },
        },
        parsed={
            "decision": "branch",
            "tool_calls": [
                {
                    "tool_name": "create_branch",
                    "arguments": {"reason": "zero-horizon fork", "fork_tick_index": 5},
                }
            ],
        },
        tick_index=5,
    )

    assert [call["tool_name"] for call in calls] == ["continue_timeline"]
    assert "Final allowed tick reached" in calls[0]["arguments"]["reason"]


def test_near_horizon_branch_runway_policy_suppresses_explicit_branch_tool(db):
    _, root = _seed_world(db, max_ticks=3, branch_policy={"min_branch_runway_ticks": 2})

    calls = god_agent._prepare_tool_calls(
        db,
        multiverse=root,
        provisional_bundle={
            "branch_score": 0.99,
            "final_tick_context": {
                "is_final_allowed_tick": False,
                "max_ticks": 3,
                "current_tick_index": 2,
            },
        },
        parsed={
            "decision": "branch",
            "tool_calls": [
                {
                    "tool_name": "create_branch",
                    "arguments": {"reason": "one tick runway", "fork_tick_index": 2},
                }
            ],
        },
        tick_index=2,
    )

    assert [call["tool_name"] for call in calls] == ["continue_timeline"]
    assert "Branch runway too short" in calls[0]["arguments"]["reason"]


def test_disabled_branch_policy_suppresses_explicit_branch_tool(db):
    _, root = _seed_world(db, max_ticks=5, branch_policy={"branching_enabled": False})

    calls = god_agent._prepare_tool_calls(
        db,
        multiverse=root,
        provisional_bundle={
            "branch_score": 0.99,
            "final_tick_context": {
                "is_final_allowed_tick": False,
                "max_ticks": 5,
                "current_tick_index": 2,
            },
        },
        parsed={
            "decision": "branch",
            "tool_calls": [
                {
                    "tool_name": "create_branch",
                    "arguments": {"reason": "no-branch condition should suppress this", "fork_tick_index": 2},
                }
            ],
        },
        tick_index=2,
    )

    assert [call["tool_name"] for call in calls] == ["continue_timeline"]
    assert "Branching disabled" in calls[0]["arguments"]["reason"]


def test_branch_engine_rejects_near_horizon_branch_runway(db):
    _big_bang, root = _seed_world(db, max_ticks=3, branch_policy={"min_branch_runway_ticks": 2})
    db.add(
        models.TickSnapshot(
            big_bang_id=root.big_bang_id,
            multiverse_id=root.id,
            tick_index=2,
            ui_label="M1:T2",
            status="final",
            provisional_bundle={},
            final_bundle={},
            summary="Tick 2",
            idempotency_key="tick-2",
        )
    )
    db.flush()

    with pytest.raises(ValueError, match="min_branch_runway_ticks"):
        branch_engine.create_branch(
            db,
            parent=root,
            fork_tick_index=2,
            reason="too late",
            idempotency_key="too-late-branch",
        )


def test_branch_engine_rejects_disabled_branch_policy(db):
    _big_bang, root = _seed_world(db, max_ticks=5, branch_policy={"branching_enabled": False})
    db.add(
        models.TickSnapshot(
            big_bang_id=root.big_bang_id,
            multiverse_id=root.id,
            tick_index=2,
            ui_label="M1:T2",
            status="final",
            provisional_bundle={},
            final_bundle={},
            summary="Tick 2",
            idempotency_key="tick-2-disabled-branch",
        )
    )
    db.flush()

    with pytest.raises(ValueError, match="branching disabled"):
        branch_engine.create_branch(
            db,
            parent=root,
            fork_tick_index=2,
            reason="disabled branch",
            idempotency_key="disabled-branch",
        )


def test_branch_child_state_carries_prompt_visible_branch_premise(db):
    _big_bang, root = _seed_world(db, max_ticks=5)
    db.add(
        models.TickSnapshot(
            big_bang_id=root.big_bang_id,
            multiverse_id=root.id,
            tick_index=1,
            ui_label="M1:T1",
            status="final",
            provisional_bundle={},
            final_bundle={
                "executed_events": [{"title": "Authority delay", "event_type": "procedural_update"}],
                "sociology_result": {"graph_summary": {"conflict": 0.3}},
                "idle_assessment": {"idle_streak": 0},
            },
            summary="Tick 1",
            idempotency_key="tick-1-branch-premise",
        )
    )
    db.flush()

    child = branch_engine.create_branch(
        db,
        parent=root,
        fork_tick_index=1,
        reason="Candidate A wins the authority announcement before the deadline.",
        idempotency_key="branch-premise",
        branch_probability=0.35,
        probability_basis={"source": "god_agent", "basis": "Forecast-card yes path remains plausible."},
    )

    branch_state = child.state["branch"]
    assert branch_state["branch_premise"] == "Candidate A wins the authority announcement before the deadline."
    assert branch_state["probability_basis"] == {"source": "god_agent", "basis": "Forecast-card yes path remains plausible."}
    assert "local timeline premise" in branch_state["prompt_instruction"]


def test_inherited_child_endpoint_ledger_records_branch_premise(db):
    big_bang, root = _seed_world(db, max_ticks=5)
    ledger = models.EndpointLedgerVersion(
        big_bang_id=big_bang.id,
        multiverse_id=root.id,
        scope="multiverse",
        version=1,
        status="completed",
        source_type="test",
        created_by="test",
        summary="Parent ledger.",
        payload={},
    )
    db.add(ledger)
    db.flush()
    db.add(
        models.EndpointLedgerEntry(
            ledger_version_id=ledger.id,
            endpoint_key="yes",
            label="The event occurs by the deadline",
            status="active",
            probability=0.5,
            realization_criteria=["Authority confirms yes."],
            authority_refs=[],
            evidence_refs=[],
            negative_evidence_refs=[],
            blockers=[],
            status_basis="scenario_candidate_endpoint",
            contradiction_notes=None,
            rationale="Parent active candidate.",
            last_observed_tick_index=None,
            meta={"endpoint_role": "primary_candidate", "candidate_endpoint_id": "yes"},
        )
    )
    db.add(
        models.TickSnapshot(
            big_bang_id=root.big_bang_id,
            multiverse_id=root.id,
            tick_index=1,
            ui_label="M1:T1",
            status="final",
            provisional_bundle={},
            final_bundle={"executed_events": [], "sociology_result": {}, "idle_assessment": {}},
            summary="Tick 1",
            idempotency_key="tick-1-ledger-branch",
        )
    )
    db.flush()

    child = branch_engine.create_branch(
        db,
        parent=root,
        fork_tick_index=1,
        reason="Candidate A wins the authority announcement before the deadline.",
        idempotency_key="branch-ledger-premise",
        branch_probability=0.35,
    )
    child_ledger = db.scalar(
        select(models.EndpointLedgerVersion).where(models.EndpointLedgerVersion.multiverse_id == child.id)
    )
    child_entry = db.scalar(
        select(models.EndpointLedgerEntry).where(models.EndpointLedgerEntry.ledger_version_id == child_ledger.id)
    )

    assert child_ledger.payload["branch_context"]["branch_premise"] == (
        "Candidate A wins the authority announcement before the deadline."
    )
    assert child_entry.meta["branch_premise"] == "Candidate A wins the authority announcement before the deadline."
    assert child_entry.meta["branch_hypothesis_signature"]


def test_branch_engine_allows_exact_min_branch_runway(db):
    _big_bang, root = _seed_world(db, max_ticks=3, branch_policy={"min_branch_runway_ticks": 2})
    db.add(
        models.TickSnapshot(
            big_bang_id=root.big_bang_id,
            multiverse_id=root.id,
            tick_index=1,
            ui_label="M1:T1",
            status="final",
            provisional_bundle={},
            final_bundle={},
            summary="Tick 1",
            idempotency_key="tick-1",
        )
    )
    db.flush()

    child = branch_engine.create_branch(
        db,
        parent=root,
        fork_tick_index=1,
        reason="two tick runway",
        idempotency_key="valid-runway-branch",
    )

    assert child.parent_multiverse_id == root.id
    assert child.fork_tick_index == 1


def test_run_until_complete_stops_when_god_marks_ready(db, monkeypatch):
    big_bang, root = _seed_world(db, max_ticks=12)
    _patch_successful_tick(monkeypatch)
    monkeypatch.setattr(
        tick_runner,
        "review_provisional_tick",
        lambda *args, **kwargs: (
            {
                "decision": "complete_universe",
                "rationale": "endpoint ledger resolved",
                "confidence": 1,
                "input_summary": {},
                "tool_calls": [
                    {
                        "tool_name": "mark_ready_for_report",
                        "arguments": {"reason": "endpoint ledger resolved"},
                        "idempotency_key": "mark-ready-run-until-complete",
                    }
                ],
            },
            None,
        ),
    )
    run_orchestrator = _load_run_orchestrator_with_report_stub()

    result = run_orchestrator.run_big_bang_until_complete(db, big_bang=big_bang, max_total_ticks=8)
    db.commit()

    assert result["ticks_run"] == 1
    assert big_bang.status == "completed"
    assert root.status == "completed"
    assert root.report_status == "ready"
    assert root.ended_at is not None


def test_unfinished_tick_resumes_from_runtime_checkpoints(db, monkeypatch):
    big_bang, root = _seed_world(db)
    _patch_successful_tick(monkeypatch)
    unfinished = models.TickSnapshot(
        big_bang_id=big_bang.id,
        multiverse_id=root.id,
        tick_index=0,
        ui_label="M1-T0",
        status="provisional",
        provisional_bundle={},
        final_bundle={},
        summary="unfinished",
        idempotency_key="tick-0",
    )
    db.add(unfinished)
    db.commit()

    returned = tick_runner.run_next_tick(db, multiverse=root)

    assert returned.id == unfinished.id
    assert returned.status == "final"
    execution = db.scalar(select(models.TickExecution).where(models.TickExecution.tick_snapshot_id == returned.id))
    assert execution is not None
    assert execution.status == "succeeded"
    checkpoints = db.scalars(select(models.TickCheckpoint).where(models.TickCheckpoint.tick_execution_id == execution.id)).all()
    assert checkpoints
    assert all(item.status == "complete" for item in checkpoints)
    assert len(db.scalars(select(models.TickSnapshot).where(models.TickSnapshot.multiverse_id == root.id)).all()) == 1


def test_direct_retry_returns_running_tick_when_active_execution_exists(db, monkeypatch):
    big_bang, root = _seed_world(db)
    running = models.TickSnapshot(
        big_bang_id=big_bang.id,
        multiverse_id=root.id,
        tick_index=0,
        ui_label="M1-T0",
        status="running",
        provisional_bundle={},
        final_bundle={},
        summary="in flight",
        idempotency_key="tick-0",
    )
    db.add(running)
    db.flush()
    execution = models.TickExecution(
        big_bang_id=big_bang.id,
        multiverse_id=root.id,
        tick_snapshot_id=running.id,
        tick_index=0,
        status="running",
        active_slot="active",
        runtime_meta={},
        started_at=datetime.now(timezone.utc),
    )
    db.add(execution)
    db.commit()
    monkeypatch.setattr(
        tick_runner,
        "_build_runtime_plan",
        lambda *args, **kwargs: pytest.fail("direct retry should not execute a concurrent tick"),
    )

    returned = tick_runner.run_next_tick(db, multiverse=root, idempotency_key="retry")

    assert returned.id == running.id
    assert returned.status == "running"
    assert db.scalars(select(models.ExecutionNode)).all() == []


def test_direct_retry_reclaims_stale_active_execution(db, monkeypatch):
    big_bang, root = _seed_world(db)
    stale_at = datetime.now(timezone.utc) - timedelta(hours=1)
    running = models.TickSnapshot(
        big_bang_id=big_bang.id,
        multiverse_id=root.id,
        tick_index=0,
        ui_label="M1-T0",
        status="running",
        provisional_bundle={},
        final_bundle={},
        summary="stale in flight",
        idempotency_key="tick-0",
    )
    db.add(running)
    db.flush()
    execution = models.TickExecution(
        big_bang_id=big_bang.id,
        multiverse_id=root.id,
        tick_snapshot_id=running.id,
        tick_index=0,
        status="running",
        active_slot="active",
        runtime_meta={},
        started_at=stale_at,
        updated_at=stale_at,
    )
    db.add(execution)
    stale_call = models.LLMCall(
        big_bang_id=big_bang.id,
        provider="openrouter",
        model="stale-model",
        purpose="stale tick call",
        status="running",
        meta={"existing": True},
        created_at=stale_at,
        updated_at=stale_at,
    )
    fresh_call = models.LLMCall(
        big_bang_id=big_bang.id,
        provider="openrouter",
        model="fresh-model",
        purpose="fresh tick call",
        status="running",
        meta={},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.add_all([stale_call, fresh_call])
    db.commit()
    _patch_successful_tick(monkeypatch)

    returned = tick_runner.run_next_tick(db, multiverse=root, idempotency_key="retry")

    assert returned.id == running.id
    assert returned.status == "final"
    db.refresh(execution)
    assert execution.status == "succeeded"
    assert execution.active_slot is None
    db.refresh(stale_call)
    db.refresh(fresh_call)
    assert stale_call.status == "failed"
    assert stale_call.meta["stale_reclaim_reason"] == "stale tick execution reclaimed"
    assert fresh_call.status == "running"
    log = db.scalar(select(models.OperationLog).where(models.OperationLog.event_type == "runtime_execution_stale_reclaimed"))
    assert log
    assert log.body["stale_llm_calls_reclaimed"] == 1


def test_queue_job_heartbeat_extends_lease_at_checkpoint_boundaries(db, monkeypatch):
    big_bang, root = _seed_world(db)
    old_lease = datetime.now(timezone.utc) - timedelta(minutes=5)
    job = models.Job(
        job_type="run_multiverse_tick",
        queue_name="multiverse_ticks",
        status="running",
        big_bang_id=big_bang.id,
        payload={"multiverse_id": str(root.id)},
        result={},
        idempotency_key="heartbeat-job",
        lease_expires_at=old_lease,
        last_heartbeat_at=old_lease,
    )
    db.add(job)
    db.commit()
    _patch_successful_tick(monkeypatch)

    tick = tick_runner.run_next_tick(db, multiverse=root, queue_job=job)

    assert tick.status == "final"
    assert job.last_heartbeat_at is not None
    assert job.lease_expires_at is not None
    lease_expires_at = job.lease_expires_at
    if lease_expires_at.tzinfo is None:
        lease_expires_at = lease_expires_at.replace(tzinfo=timezone.utc)
    assert lease_expires_at > old_lease


def test_cohort_decisions_run_with_bounded_parallelism(db, monkeypatch):
    big_bang, root = _seed_world(db)
    actors = []
    for index in range(3):
        actor = models.Actor(
            big_bang_id=big_bang.id,
            actor_type="cohort",
            name=f"Cohort {index}",
            description=None,
            archetype={},
            created_tick_index=0,
            status="active",
        )
        db.add(actor)
        actors.append(actor)
    db.commit()
    _patch_successful_tick(monkeypatch)
    monkeypatch.setattr(tick_runner.settings, "max_parallel_cohort_decisions", 2)

    lock = threading.Lock()
    active = 0
    max_active = 0

    def fake_worker(*args, actor_id, **kwargs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return {
            "actor_output": {"actor_id": str(actor_id), "llm_call_id": None, "parsed": {}},
            "parsed_actions": [],
            "emotion_self_ratings": [],
            "llm_call_id": None,
            "parsed": {},
        }

    monkeypatch.setattr(tick_runner, "_execute_actor_decision_payload_in_worker", fake_worker)

    tick = tick_runner.run_next_tick(db, multiverse=root)

    assert tick.status == "final"
    assert max_active == 2
    cohort_nodes = db.scalars(
        select(models.ExecutionNode).where(models.ExecutionNode.node_kind == "cohort_decision")
    ).all()
    assert len(cohort_nodes) == len(actors)
    assert all(node.status == "complete" for node in cohort_nodes)


def test_default_cohort_decision_parallelism_is_sixteen():
    from app.core.config import Settings

    assert Settings().max_parallel_cohort_decisions == 16


def test_paused_big_bang_blocks_step_tick_and_run_until_complete(db):
    big_bang, root = _seed_world(db)
    big_bang.status = "paused"
    db.commit()

    with pytest.raises(ValueError, match="paused"):
        tick_runner.run_next_tick(db, multiverse=root)

    run_orchestrator = _load_run_orchestrator_with_report_stub()
    with pytest.raises(ValueError, match="paused"):
        run_orchestrator.run_big_bang_until_complete(db, big_bang=big_bang)

    assert db.scalars(select(models.TickSnapshot).where(models.TickSnapshot.multiverse_id == root.id)).all() == []


def test_run_until_complete_does_not_finalize_with_active_unfinished_timelines(db, monkeypatch):
    big_bang, root = _seed_world(db)
    _patch_successful_tick(monkeypatch)
    unfinished = models.TickSnapshot(
        big_bang_id=big_bang.id,
        multiverse_id=root.id,
        tick_index=0,
        ui_label="M1-T0",
        status="provisional",
        provisional_bundle={},
        final_bundle={},
        summary="unfinished",
        idempotency_key="tick-0",
    )
    db.add(unfinished)
    db.commit()

    run_orchestrator = _load_run_orchestrator_with_report_stub()
    run_orchestrator.generate_final_big_bang_report = lambda *args, **kwargs: pytest.fail("final report should not generate")
    run_orchestrator.generate_multiverse_report = lambda *args, **kwargs: pytest.fail("timeline report should not generate")

    with pytest.raises(ValueError, match="active or unfinished timelines"):
        run_orchestrator.run_big_bang_until_complete(db, big_bang=big_bang, max_total_ticks=1)

    assert big_bang.status != "completed"


def test_failed_tick_execution_preserves_completed_checkpoint_boundary(db, monkeypatch):
    big_bang, root = _seed_world(db)

    def add_partial_social_post(*args, **kwargs):
        db.add(
            models.SocialPost(
                big_bang_id=big_bang.id,
                multiverse_id=root.id,
                tick_index=0,
                actor_id=None,
                channel="test",
                body="partial state",
                meta={},
            )
        )
        db.flush()
        return [{"body": "partial state"}]

    _patch_successful_tick(monkeypatch)
    monkeypatch.setattr(tick_runner, "apply_social_actions", add_partial_social_post)
    monkeypatch.setattr(tick_runner, "review_provisional_tick", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("llm closed")))

    with pytest.raises(RuntimeError):
        tick_runner.run_next_tick(db, multiverse=root)
    db.commit()

    assert db.scalars(select(models.TickSnapshot)).all()
    assert db.scalars(select(models.SocialPost)).all()
    failed = db.scalar(select(models.TickCheckpoint).where(models.TickCheckpoint.checkpoint_key == "god_review"))
    assert failed is not None
    assert failed.status == "failed"
    execution = db.scalar(select(models.TickExecution))
    assert execution is not None
    assert execution.status == "failed"
    assert execution.active_slot is None
    failed_attempt = db.scalar(
        select(models.NodeAttempt)
        .join(models.ExecutionNode)
        .where(models.ExecutionNode.node_key == "god_review")
    )
    assert failed_attempt is not None
    assert failed_attempt.status == "failed"


def test_execute_job_preserves_tick_checkpoint_failure_marker(db, monkeypatch):
    big_bang, root = _seed_world(db)
    _patch_successful_tick(monkeypatch)
    monkeypatch.setattr(tick_runner, "review_provisional_tick", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("llm closed")))
    job = models.Job(
        job_type="run_multiverse_tick",
        queue_name="multiverse_ticks",
        status="queued",
        big_bang_id=big_bang.id,
        payload={"multiverse_id": str(root.id)},
        result={},
        idempotency_key="execute-job-checkpoint-failure",
    )
    db.add(job)
    db.commit()

    returned = job_tasks.execute_job(db, job)
    db.commit()

    assert returned.status == "failed"
    execution = db.scalar(select(models.TickExecution))
    assert execution is not None
    checkpoints = {
        item.checkpoint_key: item.status
        for item in db.scalars(select(models.TickCheckpoint).where(models.TickCheckpoint.tick_execution_id == execution.id))
    }
    assert checkpoints["event_generation"] == "complete"
    assert checkpoints["god_review"] == "failed"


def test_execute_job_preserves_concurrent_cancelled_status(db, monkeypatch):
    job = models.Job(
        job_type="run_multiverse_tick",
        queue_name="multiverse_ticks",
        status="queued",
        payload={"multiverse_id": str(uuid4())},
        result={},
        idempotency_key="execute-job-cancelled-finalization",
    )
    db.add(job)
    db.commit()

    def cancel_then_return_success(inner_db, running_job):
        inner_db.execute(
            update(models.Job)
            .where(models.Job.id == running_job.id)
            .values(status="cancelled", finished_at=datetime.now(timezone.utc))
            .execution_options(synchronize_session=False)
        )
        return {"ok": True}

    monkeypatch.setattr(job_tasks, "_execute_job", cancel_then_return_success)

    returned = job_tasks.execute_job(db, job)
    db.commit()

    assert returned.status == "cancelled"
    assert returned.result["status"] == "cancelled"


def test_tool_calls_are_checkpointed_and_resume_skips_completed_tool(db, monkeypatch):
    big_bang, root = _seed_world(db)
    _patch_successful_tick(monkeypatch)
    job = models.Job(
        job_type="run_multiverse_tick",
        queue_name="multiverse_ticks",
        status="running",
        big_bang_id=big_bang.id,
        payload={"multiverse_id": str(root.id)},
        result={},
        idempotency_key="tool-resume",
    )
    db.add(job)
    db.flush()

    monkeypatch.setattr(
        tick_runner,
        "review_provisional_tick",
        lambda *args, **kwargs: (
            {
                "decision": "continue",
                "rationale": "test",
                "confidence": 1,
                "input_summary": {},
                "tool_calls": [
                    {"tool_name": "continue_timeline", "arguments": {}, "idempotency_key": "tool-1"},
                    {"tool_name": "mark_ready_for_report", "arguments": {}, "idempotency_key": "tool-2"},
                ],
            },
            None,
        ),
    )
    calls: list[str] = []

    def fake_tool_call(db, *, big_bang_id, multiverse, tick_snapshot_id, god_review_id, tool_name, arguments, idempotency_key):
        calls.append(tool_name)
        tool = models.ToolCall(
            big_bang_id=big_bang_id,
            multiverse_id=multiverse.id,
            tick_snapshot_id=tick_snapshot_id,
            god_review_id=god_review_id,
            tool_name=tool_name,
            arguments=arguments,
            status="succeeded",
            result={"status": "ok"},
            idempotency_key=idempotency_key,
        )
        db.add(tool)
        db.flush()
        if len(calls) == 1:
            job.status = "interrupt_requested"
            db.flush()
        return tool

    monkeypatch.setattr(tick_runner, "execute_tool_call", fake_tool_call)

    interrupted = tick_runner.run_next_tick(db, multiverse=root, queue_job=job)
    db.commit()

    assert interrupted.status == "provisional"
    assert job.status == "interrupted"
    assert calls == ["continue_timeline"]
    execution = db.scalar(select(models.TickExecution).where(models.TickExecution.tick_snapshot_id == interrupted.id))
    checkpoints = {
        item.checkpoint_key: item.status
        for item in db.scalars(select(models.TickCheckpoint).where(models.TickCheckpoint.tick_execution_id == execution.id))
    }
    assert checkpoints["tool_call:0:continue_timeline"] == "complete"
    assert checkpoints["tool_call:1:mark_ready_for_report"] == "interrupted"

    calls.clear()

    def fake_tool_call_resume(db, *, big_bang_id, multiverse, tick_snapshot_id, god_review_id, tool_name, arguments, idempotency_key):
        calls.append(tool_name)
        tool = models.ToolCall(
            big_bang_id=big_bang_id,
            multiverse_id=multiverse.id,
            tick_snapshot_id=tick_snapshot_id,
            god_review_id=god_review_id,
            tool_name=tool_name,
            arguments=arguments,
            status="succeeded",
            result={"status": "ok"},
            idempotency_key=f"{idempotency_key}-resume",
        )
        db.add(tool)
        db.flush()
        return tool

    monkeypatch.setattr(tick_runner, "execute_tool_call", fake_tool_call_resume)
    job.status = "running"
    job.interrupted_at = None
    resumed = tick_runner.run_next_tick(db, multiverse=root, queue_job=job)
    db.commit()

    assert resumed.id == interrupted.id
    assert resumed.status == "final"
    assert calls == ["mark_ready_for_report"]
    db.refresh(execution)
    assert execution.status == "succeeded"


def test_duplicate_tool_calls_without_explicit_keys_get_distinct_fallback_keys(db, monkeypatch):
    big_bang, root = _seed_world(db)
    _patch_successful_tick(monkeypatch)
    monkeypatch.setattr(
        tick_runner,
        "review_provisional_tick",
        lambda *args, **kwargs: (
            {
                "decision": "continue",
                "rationale": "test",
                "confidence": 1,
                "input_summary": {},
                "tool_calls": [
                    {"tool_name": "continue_timeline", "arguments": {}},
                    {"tool_name": "continue_timeline", "arguments": {}},
                ],
            },
            None,
        ),
    )
    keys: list[str] = []

    def fake_tool_call(db, *, big_bang_id, multiverse, tick_snapshot_id, god_review_id, tool_name, arguments, idempotency_key):
        keys.append(idempotency_key)
        tool = models.ToolCall(
            big_bang_id=big_bang_id,
            multiverse_id=multiverse.id,
            tick_snapshot_id=tick_snapshot_id,
            god_review_id=god_review_id,
            tool_name=tool_name,
            arguments=arguments,
            status="succeeded",
            result={"status": "ok"},
            idempotency_key=idempotency_key,
        )
        db.add(tool)
        db.flush()
        return tool

    monkeypatch.setattr(tick_runner, "execute_tool_call", fake_tool_call)

    tick = tick_runner.run_next_tick(db, multiverse=root)
    db.commit()

    assert tick.status == "final"
    assert len(keys) == 2
    assert len(keys) == len(set(keys))
    assert "tool_call:0:continue_timeline" in keys[0]
    assert "tool_call:1:continue_timeline" in keys[1]


def test_max_tick_completion_marks_done_without_returning_duplicate_work(db):
    big_bang, root = _seed_world(db, max_ticks=0)
    tick = models.TickSnapshot(
        big_bang_id=big_bang.id,
        multiverse_id=root.id,
        tick_index=0,
        ui_label="M1-T0",
        status="final",
        provisional_bundle={},
        final_bundle={},
        summary="done",
        idempotency_key="tick-0",
    )
    db.add(tick)
    db.commit()

    run_orchestrator = _load_run_orchestrator_with_report_stub()
    assert run_orchestrator.simulate_ticks(db, multiverse=root, count=2) == []
    db.commit()

    assert root.status == "completed"
    assert len(db.scalars(select(models.TickSnapshot).where(models.TickSnapshot.multiverse_id == root.id)).all()) == 1


def test_simulate_ticks_threads_queue_job_and_stops_on_interrupt(db, monkeypatch):
    big_bang, root = _seed_world(db)
    tick = models.TickSnapshot(
        big_bang_id=big_bang.id,
        multiverse_id=root.id,
        tick_index=0,
        ui_label="M1-T0",
        status="final",
        provisional_bundle={},
        final_bundle={},
        summary="done",
        idempotency_key="queued-tick-0",
    )
    job = models.Job(
        job_type="simulate_multiverse_ticks",
        queue_name="multiverse_ticks",
        status="running",
        big_bang_id=big_bang.id,
        payload={"multiverse_id": str(root.id), "count": 2},
        result={},
        idempotency_key="simulate-interrupt",
    )
    db.add_all([job])
    db.flush()
    calls: list[models.Job | None] = []

    def fake_run_next_tick(db, *, multiverse, queue_job=None):
        calls.append(queue_job)
        db.add(tick)
        db.flush()
        queue_job.status = "interrupt_requested"
        db.flush()
        return tick

    run_orchestrator = _load_run_orchestrator_with_report_stub()
    monkeypatch.setattr(run_orchestrator, "run_next_tick", fake_run_next_tick)

    ticks = run_orchestrator.simulate_ticks(db, multiverse=root, count=2, queue_job=job)

    assert ticks == [tick]
    assert calls == [job]


def test_forced_tick_uses_unique_idempotency_key(db, monkeypatch):
    big_bang, root = _seed_world(db)
    db.add(
        models.TickSnapshot(
            big_bang_id=big_bang.id,
            multiverse_id=root.id,
            tick_index=0,
            ui_label="M1-T0",
            status="final",
            provisional_bundle={},
            final_bundle={},
            summary="first",
            idempotency_key="same-key",
        )
    )
    db.commit()
    _patch_successful_tick(monkeypatch)

    tick = tick_runner.run_next_tick(db, multiverse=root, idempotency_key="same-key", force=True)
    db.commit()

    keys = [item.idempotency_key for item in db.scalars(select(models.TickSnapshot)).all()]
    assert tick.tick_index == 1
    assert len(keys) == len(set(keys))
    assert tick.idempotency_key != "same-key"


def test_branch_inherits_state_at_fork_tick_and_rejects_future(db):
    big_bang, root = _seed_world(db)
    artifact = models.Artifact(
        big_bang_id=big_bang.id,
        kind="tick_snapshot",
        path="parent.json",
        content_type="application/json",
        content_hash="test",
        size_bytes=2,
        debug_only=False,
        meta={},
    )
    db.add(artifact)
    db.flush()
    for index, label in [(0, "fork"), (1, "future")]:
        db.add(
            models.TickSnapshot(
                big_bang_id=big_bang.id,
                multiverse_id=root.id,
                tick_index=index,
                ui_label=f"M1-T{index}",
                status="final",
                provisional_bundle={},
                final_bundle={
                    "sociology_result": {
                        "graph_summary": {"label": label},
                        "cohort_state_updates": [{"label": label}],
                        "hero_state_updates": [],
                    },
                    "executed_events": [{"label": label}],
                    "idle_assessment": {"idle_streak": index},
                },
                summary=label,
                artifact_id=artifact.id,
                idempotency_key=f"tick-{index}",
            )
        )
    root.state = {"last_tick_index": 1, "graph_summary": {"label": "future"}}
    db.commit()

    child = branch_engine.create_branch(
        db,
        parent=root,
        fork_tick_index=0,
        reason="historical branch",
        idempotency_key="branch-0",
    )

    assert child.state["last_tick_index"] == 0
    assert child.state["graph_summary"] == {"label": "fork"}
    inherited = db.scalars(select(models.TickSnapshot).where(models.TickSnapshot.multiverse_id == child.id)).all()
    assert len(inherited) == 1
    assert inherited[0].artifact_id is None
    lineage_edges = db.scalars(
        select(models.MultiverseLineageEdge).where(models.MultiverseLineageEdge.child_multiverse_id == child.id)
    ).all()
    lineage_refs = db.scalars(
        select(models.TickLineageRef).where(models.TickLineageRef.child_multiverse_id == child.id)
    ).all()
    lineage_payload = MultiverseLineageOut.model_validate(
        {"multiverse": child, "edges": lineage_edges, "inherited_ticks": lineage_refs}
    ).model_dump(mode="json")
    source_tick = db.scalar(
        select(models.TickSnapshot).where(
            models.TickSnapshot.multiverse_id == root.id,
            models.TickSnapshot.tick_index == 0,
        )
    )
    assert lineage_payload["edges"][0]["child_multiverse_id"] == str(child.id)
    assert lineage_payload["inherited_ticks"][0]["source_tick_snapshot_id"] == str(source_tick.id)
    with pytest.raises(ValueError):
        branch_engine.create_branch(
            db,
            parent=root,
            fork_tick_index=3,
            reason="future branch",
            idempotency_key="branch-3",
        )


def test_god_tools_reject_out_of_scope_mutations(db):
    big_bang, root = _seed_world(db)
    other = models.Multiverse(
        big_bang_id=big_bang.id,
        parent_multiverse_id=None,
        fork_tick_index=None,
        ui_label="M2",
        depth=0,
        status="active",
        branch_reason="Other",
        state={},
    )
    db.add(other)
    db.flush()
    event = models.Event(
        big_bang_id=big_bang.id,
        multiverse_id=other.id,
        event_type="test",
        created_tick=0,
        scheduled_tick=1,
        status="queued",
        title="Out of scope",
        description=None,
        expected_impact={},
        actual_impact={},
        meta={},
    )
    candidate = models.CohortSplitCandidate(
        big_bang_id=big_bang.id,
        multiverse_id=other.id,
        tick_index=0,
        status="candidate",
        payload={},
    )
    plan = models.CohortMergePlan(
        big_bang_id=big_bang.id,
        multiverse_id=other.id,
        tick_index=0,
        status="planned",
        payload={},
    )
    db.add_all([event, candidate, plan])
    db.flush()

    calls = [
        god_tools.execute_tool_call(
            db,
            big_bang_id=big_bang.id,
            multiverse=root,
            tick_snapshot_id=None,
            god_review_id=None,
            tool_name="register_key_event",
            arguments={"event_id": str(event.id)},
            idempotency_key="event-scope",
        ),
        god_tools.execute_tool_call(
            db,
            big_bang_id=big_bang.id,
            multiverse=root,
            tick_snapshot_id=None,
            god_review_id=None,
            tool_name="approve_split",
            arguments={"candidate_id": str(candidate.id)},
            idempotency_key="candidate-scope",
        ),
        god_tools.execute_tool_call(
            db,
            big_bang_id=big_bang.id,
            multiverse=root,
            tick_snapshot_id=None,
            god_review_id=None,
            tool_name="approve_merge_plan",
            arguments={"merge_plan_id": str(plan.id)},
            idempotency_key="plan-scope",
        ),
    ]

    assert [call.status for call in calls] == ["failed", "failed", "failed"]
    assert event.meta == {}
    assert candidate.status == "candidate"
    assert plan.status == "planned"
    assert db.scalars(select(models.CohortSplit)).all() == []
    assert db.scalars(select(models.CohortMerge)).all() == []


def test_god_tool_split_cohort_conserves_population_and_creates_children(db):
    big_bang, root = _seed_world(db)
    actor = models.Actor(
        big_bang_id=big_bang.id,
        actor_type="cohort",
        name="Regional Stability Voters",
        description=None,
        archetype={},
        created_tick_index=0,
        status="active",
    )
    db.add(actor)
    db.flush()
    db.add(
        models.CohortState(
            big_bang_id=big_bang.id,
            multiverse_id=root.id,
            actor_id=actor.id,
            tick_index=0,
            state={
                "represented_population": 300,
                "population_share_of_archetype": 1.0,
                "representation_mode": "population",
                "attention_level": 0.4,
                "expression_level": 0.3,
            },
            queued_event_ids=["evt-parent"],
        )
    )
    db.flush()

    call = god_tools.execute_tool_call(
        db,
        big_bang_id=big_bang.id,
        multiverse=root,
        tick_snapshot_id=None,
        god_review_id=None,
        tool_name="split_cohort",
        arguments={
            "parent_cohort_id": str(actor.id),
            "tick_index": 1,
            "split_axis": "transparency versus control",
            "reason": "Durable factional disagreement.",
            "children": [
                {
                    "name": "Transparency stability voters",
                    "represented_population": 50,
                    "initial_state": {"expression_level": 0.55, "mobilization_readiness": 0.45},
                },
                {
                    "name": "Control stability voters",
                    "represented_population": 25,
                    "initial_state": {"expression_level": 0.25, "mobilization_readiness": 0.2},
                },
                {
                    "name": "Undecided stability voters",
                    "represented_population": 225,
                    "initial_state": {"expression_level": 0.18, "mobilization_readiness": 0.12},
                },
            ],
        },
        idempotency_key="split-stability-voters",
    )

    assert call.status == "succeeded"
    assert call.result["parent_population"] == 300
    assert [item["represented_population"] for item in call.result["children"]] == [50, 25, 225]
    assert actor.status == "split"
    child_ids = [item["actor_id"] for item in call.result["children"]]
    child_rows = db.scalars(
        select(models.CohortState).where(models.CohortState.actor_id.in_(child_ids))
    ).all()
    assert sum(row.state["represented_population"] for row in child_rows) == 300
    assert all(row.queued_event_ids == ["evt-parent"] for row in child_rows)


def test_god_tool_split_cohort_rejects_population_nonconservation(db):
    big_bang, root = _seed_world(db)
    actor = models.Actor(
        big_bang_id=big_bang.id,
        actor_type="cohort",
        name="Low Pressure District Residents",
        description=None,
        archetype={},
        created_tick_index=0,
        status="active",
    )
    db.add(actor)
    db.flush()
    db.add(
        models.CohortState(
            big_bang_id=big_bang.id,
            multiverse_id=root.id,
            actor_id=actor.id,
            tick_index=0,
            state={"represented_population": 100},
            queued_event_ids=[],
        )
    )
    db.flush()

    call = god_tools.execute_tool_call(
        db,
        big_bang_id=big_bang.id,
        multiverse=root,
        tick_snapshot_id=None,
        god_review_id=None,
        tool_name="split_cohort",
        arguments={
            "parent_cohort_id": str(actor.id),
            "children": [
                {"name": "A", "represented_population": 40, "initial_state": {}},
                {"name": "B", "represented_population": 40, "initial_state": {}},
            ],
        },
        idempotency_key="bad-split",
    )

    assert call.status == "failed"
    assert "conserve parent population" in call.error
    assert actor.status == "active"


def test_god_agent_langgraph_loop_repairs_failed_population_tool(db, monkeypatch):
    big_bang, root = _seed_world(db)
    actor = models.Actor(
        big_bang_id=big_bang.id,
        actor_type="cohort",
        name="Regional Stability Voters",
        description=None,
        archetype={},
        created_tick_index=0,
        status="active",
    )
    db.add(actor)
    db.flush()
    db.add(
        models.CohortState(
            big_bang_id=big_bang.id,
            multiverse_id=root.id,
            actor_id=actor.id,
            tick_index=0,
            state={"represented_population": 100, "expression_level": 0.2},
            queued_event_ids=[],
        )
    )
    db.flush()
    calls = []

    def fake_complete_with_audit(*args, **kwargs):
        calls.append(kwargs["messages"])
        if len(calls) == 1:
            parsed = {
                "decision": "split",
                "rationale": "First split attempt underallocates population.",
                "confidence": 0.8,
                "tool_calls": [
                    {
                        "tool_name": "split_cohort",
                        "arguments": {
                            "parent_cohort_id": str(actor.id),
                            "reason": "test",
                            "children": [
                                {"name": "A", "represented_population": 40, "initial_state": {}},
                                {"name": "B", "represented_population": 40, "initial_state": {}},
                            ],
                        },
                        "idempotency_key": "loop-bad-split",
                    }
                ],
            }
        else:
            parsed = {
                "decision": "split",
                "rationale": "Repair conserves population.",
                "confidence": 0.9,
                "tool_calls": [
                    {
                        "tool_name": "split_cohort",
                        "arguments": {
                            "parent_cohort_id": str(actor.id),
                            "reason": "test repair",
                            "children": [
                                {"name": "A", "represented_population": 50, "initial_state": {}},
                                {"name": "B", "represented_population": 50, "initial_state": {}},
                            ],
                        },
                        "idempotency_key": "loop-good-split",
                    }
                ],
            }
        return LLMResponse(content="", parsed=parsed, raw={}), types.SimpleNamespace(id=uuid4(), model="gpt-5.4")

    monkeypatch.setattr(god_agent, "complete_with_audit", fake_complete_with_audit)

    review, _ = god_agent.review_provisional_tick(
        db,
        root,
        {"tick_index": 1, "branch_score": 0.0, "executed_events": []},
    )

    assert len(calls) == 2
    assert len(review["god_agent_iterations"]) == 2
    assert review["god_agent_iterations"][0]["tool_results"][0]["status"] == "failed"
    assert review["god_agent_iterations"][1]["tool_results"][0]["status"] == "succeeded"
    assert actor.status == "split"


def test_god_agent_review_budgets_large_provisional_bundle(db, monkeypatch):
    _big_bang, root = _seed_world(db)
    captured_messages = []

    def fake_complete_with_audit(db, *, big_bang_id, purpose, model, route, messages, metadata):
        captured_messages.append(messages)
        return (
            LLMResponse(content="", parsed={"decision": "continue", "confidence": 0.8, "tool_calls": []}, raw={}),
            types.SimpleNamespace(id=uuid4(), model="gpt-5.4"),
        )

    monkeypatch.setattr(god_agent, "complete_with_audit", fake_complete_with_audit)
    provisional_bundle = {
        "tick_index": 1,
        "branch_score": 0.0,
        "executed_events": [],
        "queued_events": [
            {
                "title": f"Queued event {index}",
                "description": f"GOD_RAW_EVENT_DESCRIPTION_{index} " + ("detail " * 500),
                "scheduled_tick": index,
            }
            for index in range(40)
        ],
        "social_observations": [{"body": f"post {index}"} for index in range(35)],
        "event_summaries": [],
    }

    review, _ = god_agent.review_provisional_tick(db, root, provisional_bundle)

    prompt_text = captured_messages[0][1]["content"]
    assert "Queued event 0" in prompt_text
    assert "GOD_RAW_EVENT_DESCRIPTION_39" not in prompt_text
    assert review["input_summary"]["prompt_budget"]["omitted_total"] >= 39
    assert review["input_summary"]["prompt_budget"]["sections"]["queued_events"]["omitted_count"] == 24


def test_god_agent_review_compacts_raw_actor_outputs(db, monkeypatch):
    _big_bang, root = _seed_world(db)
    captured_messages = []

    def fake_complete_with_audit(db, *, big_bang_id, purpose, model, route, messages, metadata):
        captured_messages.append(messages)
        return (
            LLMResponse(content="", parsed={"decision": "continue", "confidence": 0.8, "tool_calls": []}, raw={}),
            types.SimpleNamespace(id=uuid4(), model="gpt-5.4"),
        )

    monkeypatch.setattr(god_agent, "complete_with_audit", fake_complete_with_audit)
    provisional_bundle = {
        "tick_index": 2,
        "branch_score": 0.2,
        "endpoint_ledger": {"version": 1, "entries": []},
        "final_tick_context": {"is_final_allowed_tick": False},
        "forecast_review_context": {"forecast_question": "Will Candidate A win?"},
        "agent_outputs": [
            {
                "actor_id": "actor-1",
                "llm_call_id": "call-1",
                "parsed": {
                    "social_actions": [
                        {"action_type": "post", "body": "Candidate path remains contested."},
                    ],
                    "proposed_events": [
                        {
                            "title": "Authority publishes endpoint result",
                            "description": "RAW_AGENT_EVENT_DESCRIPTION " + ("detail " * 500),
                        }
                    ],
                    "state_delta": {"stance": "wait_for_authority", "long_note": "STATE_RAW " + ("x" * 3000)},
                },
            }
        ],
    }

    god_agent.review_provisional_tick(db, root, provisional_bundle)

    prompt_text = captured_messages[0][1]["content"]
    assert "Authority publishes endpoint result" in prompt_text
    assert "Candidate path remains contested" in prompt_text
    assert "RAW_AGENT_EVENT_DESCRIPTION" not in prompt_text
    assert "STATE_RAW" not in prompt_text


def test_final_tick_god_review_drops_branch_tool_calls():
    review_payload = {
        "decision": "branch",
        "rationale": "Branch at the decision deadline.",
        "tool_calls": [
            {"tool_name": "create_branch", "arguments": {"reason": "late fork"}},
            {"tool_name": "continue_timeline", "arguments": {"reason": "audit"}},
        ],
    }
    provisional = {
        "final_tick_context": {
            "is_final_allowed_tick": True,
            "max_ticks": 5,
            "current_tick_index": 5,
        }
    }

    pruned = tick_runner._prune_final_tick_branch_tool_calls(review_payload, provisional)

    assert pruned["decision"] == "continue"
    assert [call["tool_name"] for call in pruned["tool_calls"]] == ["continue_timeline"]
    assert "create_branch suppressed at final allowed tick" in pruned["rationale"]


def test_forecast_review_context_preserves_source_packet_and_terminal_signals():
    big_bang = models.BigBang(
        name="Forecast card",
        scenario_input={
            "question": "Will Phone A be generally available to customers by the end of September?",
            "scenario_text": "Phone A is announced with a late-September availability target.",
            "forecast_metadata": {
                "as_of_date": "2025-09-10",
                "forecast_deadline_date": "2025-09-30",
                "deadline_tick": 3,
                "tick_horizon_policy": "deadline_aware",
            },
            "source_packet": [
                {
                    "source_type": "company_announcement",
                    "date": "2025-09-10",
                    "content": "The announced schedule places availability in the second half of September.",
                }
            ],
            "candidate_endpoints": [
                {"id": "yes", "label": "The event occurs by the deadline"},
                {"id": "no", "label": "The event does not occur by the deadline"},
            ],
        },
    )

    context = tick_runner._forecast_review_context(
        big_bang=big_bang,
        prompt_context={"forecast_clock": {"deadline_tick_reached": True}},
        executed_events=[
            {
                "title": "Phone A general availability confirmed",
                "event_type": "commercial_milestone",
                "status": "executed",
                "scheduled_tick": 3,
                "actual_impact": {"summary": "Resolves the forecast endpoint to yes for customer availability."},
            }
        ],
        event_summaries=[
            {
                "summary": "Customer availability was confirmed before the deadline.",
                "parsed": {"what_happened": "Phone A general availability confirmed by the manufacturer."},
            }
        ],
        social_observations=[{"post": "Customer availability remains disputed; some stores still report no stock."}],
    )

    assert context["forecast_clock"]["deadline_tick_reached"] is True
    assert context["forecast_question"].startswith("Will Phone A")
    assert "late-September availability" in context["scenario_text"]
    assert context["source_packet"][0]["source_type"] == "company_announcement"
    assert context["candidate_endpoints"][0]["id"] == "yes"
    assert context["endpoint_relevant_event_signals"][0]["signal_polarity"] == "supports_yes"
    assert context["endpoint_relevant_social_signals"][0]["matched_forecast_terms"]


def test_forecast_review_context_uses_card_terms_for_non_product_events():
    big_bang = models.BigBang(
        name="Macro forecast card",
        scenario_input={
            "question": "Will the Federal Open Market Committee lower the target range for the federal funds rate?",
            "scenario_text": "The endpoint is yes only if the target range is lower immediately after the meeting.",
            "forecast_metadata": {
                "as_of_date": "2025-11-15",
                "forecast_deadline_date": "2025-12-10",
                "deadline_tick": 2,
                "tick_horizon_policy": "deadline_aware",
            },
            "source_packet": [
                {
                    "source_type": "macro_note",
                    "date": "2025-11-15",
                    "text": "Markets discuss a possible quarter-point cut, but policymakers emphasize data dependence.",
                }
            ],
            "candidate_endpoints": [
                {"id": "yes", "label": "The FOMC lowers the target range by the deadline"},
                {"id": "no", "label": "The FOMC does not lower the target range by the deadline"},
            ],
        },
    )

    context = tick_runner._forecast_review_context(
        big_bang=big_bang,
        prompt_context={"forecast_clock": {"deadline_tick_reached": True}},
        executed_events=[
            {
                "title": "FOMC target range decision announced",
                "event_type": "policy_decision",
                "status": "executed",
                "scheduled_tick": 2,
                "actual_impact": {
                    "summary": "The committee lowers the target range for the federal funds rate after the meeting."
                },
            }
        ],
        event_summaries=[],
        social_observations=[],
    )

    assert "Federal Open Market Committee" in context["forecast_question"]
    signal = context["endpoint_relevant_event_signals"][0]
    assert signal["event_type"] == "policy_decision"
    assert {"target", "range"}.issubset(set(signal["matched_forecast_terms"]))


def test_tick_and_run_timing_payloads_include_stage_and_llm_durations(db):
    big_bang, root = _seed_world(db)
    start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    tick = models.TickSnapshot(
        big_bang_id=big_bang.id,
        multiverse_id=root.id,
        tick_index=1,
        ui_label="M1:T1",
        status="final",
        provisional_bundle={},
        final_bundle={},
        summary="timed",
        idempotency_key="timed-tick",
        created_at=start,
        updated_at=start + timedelta(seconds=30),
    )
    db.add(tick)
    db.flush()
    execution = models.TickExecution(
        big_bang_id=big_bang.id,
        multiverse_id=root.id,
        tick_snapshot_id=tick.id,
        tick_index=1,
        status="succeeded",
        active_slot=None,
        started_at=start + timedelta(seconds=1),
        finished_at=start + timedelta(seconds=29),
    )
    db.add(execution)
    db.flush()
    node = models.ExecutionNode(
        tick_execution_id=execution.id,
        node_key="god_review",
        node_kind="god_review",
        status="complete",
        checkpoint_order=1,
        started_at=start + timedelta(seconds=10),
        finished_at=start + timedelta(seconds=18),
    )
    db.add(node)
    db.flush()
    db.add(
        models.NodeAttempt(
            execution_node_id=node.id,
            attempt_number=1,
            status="complete",
            provider="openai-codex",
            model="gpt-5.4",
            started_at=start + timedelta(seconds=10),
            finished_at=start + timedelta(seconds=18),
        )
    )
    db.add(
        models.LLMCall(
            big_bang_id=big_bang.id,
            provider="openai-codex",
            model="gpt-5.4",
            purpose=f"god_review_{root.id}_tick_1_iter_1",
            status="succeeded",
            created_at=start + timedelta(seconds=10),
            updated_at=start + timedelta(seconds=18),
            meta={"attempts": [{"attempt": 1, "status": "succeeded"}]},
        )
    )
    db.flush()

    tick_payload = tick_timing_payload(db, tick)
    run_payload = run_timing_payload(db, big_bang)

    assert tick_payload["duration_seconds"] == 28.0
    assert tick_payload["executions"][0]["stage_timings"][0]["node_kind"] == "god_review"
    assert tick_payload["executions"][0]["stage_timings"][0]["duration_seconds"] == 8.0
    assert tick_payload["llm_calls"][0]["duration_seconds"] == 8.0
    assert tick_payload["llm_summary"]["total_calls"] == 1
    assert run_payload["ticks"][0]["tick_snapshot_id"] == str(tick.id)
