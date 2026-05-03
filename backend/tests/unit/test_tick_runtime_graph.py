import warnings

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import models
from app.runtime import NodeKind, TickRuntimeState, build_tick_graph
from app.domains.tick import tick_runner
from app.domains.sociology.graph_engine import build_graph_prompt_summary


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


def _state() -> TickRuntimeState:
    return TickRuntimeState(
        multiverse_id="m1",
        tick_id="t1",
        last_tick_id="t0",
        cohort_ids=["cohort-a", "cohort-b"],
        hero_ids=["hero-a"],
    )


def test_build_tick_graph_fans_out_actor_nodes_and_joins_at_barrier():
    plan = build_tick_graph(_state())

    assert plan.cohort_nodes == ("cohort:cohort-a", "cohort:cohort-b")
    assert plan.hero_nodes == ("hero:hero-a",)
    assert plan.actor_nodes == (
        "cohort:cohort-a",
        "cohort:cohort-b",
        "hero:hero-a",
    )
    assert plan.barrier_nodes == ("barrier:actor_decisions",)
    assert ("cohort:cohort-a", "barrier:actor_decisions") in plan.edges
    assert ("cohort:cohort-b", "barrier:actor_decisions") in plan.edges
    assert ("hero:hero-a", "barrier:actor_decisions") in plan.edges


def test_build_tick_graph_inserts_interrupt_checks_between_checkpoint_phases():
    plan = build_tick_graph(_state())

    assert plan.interrupt_nodes == (
        "interrupt_check:after_actor_barrier",
        "interrupt_check:after_event_generation",
        "interrupt_check:after_sociology_update",
        "interrupt_check:after_graph_update",
        "interrupt_check:after_god_review",
        "interrupt_check:after_tick_summary",
    )
    assert ("barrier:actor_decisions", "interrupt_check:after_actor_barrier") in plan.edges
    assert ("interrupt_check:after_actor_barrier", "event_generation") in plan.edges
    assert ("event_generation", "interrupt_check:after_event_generation") in plan.edges
    assert ("interrupt_check:after_event_generation", "sociology_update") in plan.edges
    assert ("graph_update", "interrupt_check:after_graph_update") in plan.edges
    assert ("interrupt_check:after_tick_summary", "state_commit") in plan.edges


def test_build_tick_graph_preserves_deterministic_checkpoint_order():
    plan = build_tick_graph(_state())

    assert plan.checkpoint_order == (
        "cohort:cohort-a",
        "cohort:cohort-b",
        "hero:hero-a",
        "event_generation",
        "sociology_update",
        "graph_update",
        "god_review",
        "tick_summary",
    )
    assert plan.node_specs["cohort:cohort-a"].kind is NodeKind.COHORT_DECISION
    assert plan.node_specs["hero:hero-a"].kind is NodeKind.HERO_DECISION
    assert plan.node_specs["event_generation"].kind is NodeKind.EVENT_GENERATION
    assert plan.node_specs["state_commit"].checkpoint is False


def test_build_tick_graph_inserts_dynamic_tool_call_checkpoints_after_god_review():
    state = _state()
    state.tool_call_keys = ["tool_call:0:continue_timeline", "tool_call:1:create_branch"]

    plan = build_tick_graph(state)

    assert plan.checkpoint_order == (
        "cohort:cohort-a",
        "cohort:cohort-b",
        "hero:hero-a",
        "event_generation",
        "sociology_update",
        "graph_update",
        "god_review",
        "tool_call:0:continue_timeline",
        "tool_call:1:create_branch",
        "tick_summary",
    )
    assert plan.node_specs["tool_call:0:continue_timeline"].kind is NodeKind.TOOL_CALL
    assert ("interrupt_check:after_god_review", "tool_call:0:continue_timeline") in plan.edges
    assert ("tool_call:0:continue_timeline", "interrupt_check:after_tool_call:0:continue_timeline") in plan.edges


def test_committed_sociology_graph_summary_reflects_current_tick_graph(db, monkeypatch):
    big_bang = models.BigBang(
        name="Runtime graph summary",
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
            simulation_config={"max_ticks": 2},
            model_config={},
            branch_policy={},
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
    alpha = models.Actor(
        big_bang_id=big_bang.id,
        actor_type="cohort",
        name="Alpha",
        description=None,
        archetype={},
        created_tick_index=0,
        status="active",
    )
    beta = models.Actor(
        big_bang_id=big_bang.id,
        actor_type="cohort",
        name="Beta",
        description=None,
        archetype={},
        created_tick_index=0,
        status="active",
    )
    db.add_all([root, alpha, beta])
    db.flush()

    def fake_actor_decision(db, *, big_bang, multiverse, actor, tick_index, prompt_context):
        return {
            "actor_output": {"actor_id": str(actor.id), "llm_call_id": None, "parsed": {}},
            "parsed_actions": [
                {
                    "actor_id": actor.id,
                    "action_type": "post",
                    "channel": "oasis",
                    "body": "anger blame conflict protest over unequal enforcement",
                }
            ],
            "emotion_self_ratings": [],
            "llm_call_id": None,
            "parsed": {},
        }

    monkeypatch.setattr(tick_runner, "run_actor_decision", fake_actor_decision)
    monkeypatch.setattr(tick_runner, "load_due_events", lambda *args, **kwargs: [])
    monkeypatch.setattr(tick_runner, "execute_due_events", lambda *args, **kwargs: [])
    monkeypatch.setattr(tick_runner, "summarize_executed_events", lambda *args, **kwargs: [])
    monkeypatch.setattr(tick_runner, "update_emotion_observability_graphs", lambda *args, **kwargs: {})
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

    tick = tick_runner.run_next_tick(db, multiverse=root)
    db.flush()

    latest_graph_summary = build_graph_prompt_summary(db, multiverse_id=root.id)
    committed_graph_summary = tick.final_bundle["sociology_result"]["graph_summary"]

    assert tick.status == "final"
    assert committed_graph_summary == latest_graph_summary
    assert committed_graph_summary["pressure"]["conflict_max"] > 0
    assert committed_graph_summary["layers"]["conflict"]["edge_count"] > 0


def test_runtime_actor_prompt_context_includes_due_seed_events(db, monkeypatch):
    big_bang = models.BigBang(
        name="Runtime seed events",
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
            simulation_config={"max_ticks": 2},
            model_config={},
            branch_policy={},
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
    alpha = models.Actor(
        big_bang_id=big_bang.id,
        actor_type="cohort",
        name="Alpha",
        description=None,
        archetype={},
        created_tick_index=0,
        status="active",
    )
    db.add_all([root, alpha])
    db.flush()
    db.add_all(
        [
            models.TickSnapshot(
                big_bang_id=big_bang.id,
                multiverse_id=root.id,
                tick_index=0,
                ui_label="M1 T0",
                status="final",
                final_bundle={"state": {}},
                idempotency_key=f"{root.id}:tick:0",
            ),
            models.Event(
                big_bang_id=big_bang.id,
                multiverse_id=root.id,
                event_type="announcement",
                created_tick=0,
                scheduled_tick=0,
                status="queued",
                title="Initializer seeded outage",
                description="The outage is known before the first actor decision.",
                expected_impact={},
                actual_impact={},
                meta={"source": "initializer_agent"},
            ),
        ]
    )
    db.flush()
    captured_event_queues = []

    def fake_actor_decision(db, *, big_bang, multiverse, actor, tick_index, prompt_context):
        captured_event_queues.append(prompt_context.get("event_queue"))
        return {
            "actor_output": {"actor_id": str(actor.id), "llm_call_id": None, "parsed": {}},
            "parsed_actions": [],
            "emotion_self_ratings": [],
            "llm_call_id": None,
            "parsed": {},
        }

    monkeypatch.setattr(tick_runner, "run_actor_decision", fake_actor_decision)
    monkeypatch.setattr(tick_runner, "summarize_executed_events", lambda *args, **kwargs: [])
    monkeypatch.setattr(tick_runner, "update_emotion_observability_graphs", lambda *args, **kwargs: {})
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

    tick_runner.run_next_tick(db, multiverse=root)

    assert captured_event_queues
    assert captured_event_queues[0]["due_events"][0]["title"] == "Initializer seeded outage"
