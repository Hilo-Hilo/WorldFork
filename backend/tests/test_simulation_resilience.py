from __future__ import annotations

from decimal import Decimal
from uuid import UUID
import warnings

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import models
from app.llm.audit import LLMCallError
from app.llm.schemas import LLMResponse
from app.api.schemas import BigBangCreate
from app.domains.big_bang import initializer_agent as initializer_agent_module
from app.domains.big_bang.initializer_agent import normalize_initializer_output
from app.simulation import agent_engine, event_engine, initializer
from app.simulation.graph_engine import update_graph_layers
from app.simulation.initializer import persist_initializer_graphs_and_observability
from app.storage.artifact_store import ArtifactStore


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


def _seed_world(db: Session) -> tuple[models.BigBang, models.Multiverse, models.Actor, models.Actor]:
    big_bang = models.BigBang(
        name="Resilience test",
        description=None,
        scenario_input={},
        status="active",
        current_config_version=1,
    )
    db.add(big_bang)
    db.flush()
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
    )
    beta = models.Actor(
        big_bang_id=big_bang.id,
        actor_type="hero",
        name="Beta",
        description=None,
        archetype={},
        created_tick_index=0,
    )
    db.add_all([root, alpha, beta])
    db.flush()
    return big_bang, root, alpha, beta


def _fake_llm_call(db: Session, *, big_bang_id, purpose: str, model: str) -> models.LLMCall:
    call = models.LLMCall(
        big_bang_id=big_bang_id,
        provider="test",
        model=model,
        purpose=purpose,
        status="succeeded",
        meta={},
    )
    db.add(call)
    db.flush()
    return call


def test_agent_decision_payloads_skip_bad_list_items_and_default_casts(db, monkeypatch):
    big_bang, root, _alpha, _beta = _seed_world(db)

    def fake_complete(db, *, big_bang_id, purpose, model, messages, metadata, json_schema=None, route=None):
        call = _fake_llm_call(db, big_bang_id=big_bang_id, purpose=purpose, model=model)
        return (
            LLMResponse(
                content="{}",
                parsed={
                    "social_actions": ["bad", {"body": "valid post"}],
                    "proposed_events": [
                        "bad",
                        {"title": "Valid event", "scheduled_tick": "tick 7"},
                        {"title": "Fallback event", "scheduled_tick": "soon"},
                    ],
                    "emotion_self_ratings": ["bad", {"emotion_key": "relief", "value": "11-ish"}],
                },
            ),
            call,
        )

    monkeypatch.setattr(agent_engine, "complete_with_audit", fake_complete)

    result = agent_engine.run_agent_decisions(
        db,
        big_bang=big_bang,
        multiverse=root,
        tick_index=3,
        prompt_context={},
    )
    assert len(result["parsed_actions"]) == 6
    assert result["emotion_self_ratings"][0]["emotion"] == "relief"
    assert result["emotion_self_ratings"][0]["value"] == 10.0

    observations = agent_engine.apply_social_actions(
        db,
        big_bang_id=big_bang.id,
        multiverse_id=root.id,
        tick_index=3,
        parsed_actions=[*result["parsed_actions"], "not a dict"],
    )
    assert [item["post"] for item in observations] == ["valid post", "valid post"]

    queued = agent_engine.queue_agent_events(
        db,
        big_bang_id=big_bang.id,
        multiverse_id=root.id,
        tick_index=3,
        parsed_actions=[*result["parsed_actions"], {"proposed_event": "not a dict"}],
    )
    assert [item["scheduled_tick"] for item in queued] == [7, 4, 7, 4]


def test_actor_prompt_context_includes_global_and_owned_event_queue(db, monkeypatch):
    big_bang, root, alpha, _beta = _seed_world(db)
    db.add_all(
        [
            models.Event(
                big_bang_id=big_bang.id,
                multiverse_id=root.id,
                event_type="announcement",
                created_tick=0,
                scheduled_tick=0,
                status="executed",
                title="Past water pressure loss",
                description="Residents already saw water pressure drop.",
                expected_impact={"trust": "down"},
                actual_impact={"trust": "lower"},
                meta={"source": "initializer_agent"},
            ),
            models.Event(
                big_bang_id=big_bang.id,
                multiverse_id=root.id,
                creator_actor_id=alpha.id,
                event_type="announcement",
                created_tick=1,
                scheduled_tick=3,
                status="queued",
                title="Alpha schedules clinic briefing",
                description="Alpha will brief residents later.",
                expected_impact={},
                actual_impact={},
                meta={"source": "agent_proposal"},
            ),
        ]
    )
    db.flush()
    captured_contexts = []

    def fake_complete(db, *, big_bang_id, purpose, model, messages, metadata, json_schema=None, route=None):
        captured_contexts.append(messages[-1]["content"])
        call = _fake_llm_call(db, big_bang_id=big_bang_id, purpose=purpose, model=model)
        return LLMResponse(content="{}", parsed={"social_actions": [], "proposed_events": []}), call

    monkeypatch.setattr(agent_engine, "complete_with_audit", fake_complete)

    agent_engine.run_actor_decision(
        db,
        big_bang=big_bang,
        multiverse=root,
        actor=alpha,
        tick_index=1,
        prompt_context={},
    )

    assert "Past water pressure loss" in captured_contexts[0]
    assert "Alpha schedules clinic briefing" in captured_contexts[0]
    assert "own_queued_events" in captured_contexts[0]


def test_initializer_seed_events_become_root_event_queue(db, monkeypatch, tmp_path):
    def fake_snapshot(db, big_bang_id):
        snapshot = models.SourceOfTruthSnapshot(
            big_bang_id=big_bang_id,
            version="test",
            content_hash="hash",
            artifact_path="source-of-truth",
        )
        db.add(snapshot)
        db.flush()
        return snapshot

    monkeypatch.setattr(initializer, "snapshot_source_of_truth", fake_snapshot)
    monkeypatch.setattr(initializer, "ArtifactStore", lambda: ArtifactStore(root=tmp_path))
    monkeypatch.setattr(
        initializer,
        "run_initializer_agent",
        lambda *args, **kwargs: {
            "actors": [{"name": "Atlas Council", "actor_type": "institution"}],
            "initial_events": [
                {
                    "event_type": "announcement",
                    "title": "Pressure failure already visible",
                    "description": "Residents saw a water-pressure failure before the first agent tick.",
                    "scheduled_tick": 0,
                    "expected_impact": {"rumor_velocity": "rising"},
                },
                {
                    "event_type": "announcement",
                    "title": "Court hearing scheduled",
                    "description": "A court hearing is scheduled for the next tick.",
                    "scheduled_tick": 2,
                    "expected_impact": {"legal_pressure": "rising"},
                },
            ],
        },
    )

    big_bang = initializer.create_big_bang(
        db,
        BigBangCreate(
            name="Atlas",
            scenario_text="Atlas has a pressure failure and a pending court hearing.",
            use_initializer_agent=True,
        ),
    )

    root = db.query(models.Multiverse).filter_by(big_bang_id=big_bang.id).one()
    event_queue = root.state["event_queue"]

    assert [event["title"] for event in event_queue["seed_events"]] == [
        "Pressure failure already visible",
        "Court hearing scheduled",
    ]
    assert [event["title"] for event in event_queue["past_events"]] == ["Pressure failure already visible"]
    assert [event["title"] for event in event_queue["upcoming_events"]] == ["Court hearing scheduled"]


def test_initializer_normalizes_deepseek_type_aliases():
    normalized = normalize_initializer_output(
        {
            "actors": [
                {"name": "school_board", "type": "institution"},
                {"name": "parents", "type": "group"},
                {"name": "student_organizer", "type": "individual"},
                {"name": "drivers_union", "type": "organization"},
            ],
            "cohort_states": [{"name": "parents", "state": {"attention_level": 0.6}}],
            "hero_archetypes": [{"name": "student_organizer", "definition": {"role": "speaker"}}],
            "graph_edges": [],
        },
        {"premise": "A school board changes bus routes."},
    )

    actor_types = {item["name"]: item["actor_type"] for item in normalized["actors"]}
    assert actor_types == {
        "school_board": "institution",
        "parents": "cohort",
        "student_organizer": "hero",
        "drivers_union": "organization",
    }
    assert normalized["cohort_states"][0]["actor_name"] == "parents"
    assert normalized["hero_archetypes"][0]["actor_type"] == "hero"
    assert normalized["hero_archetypes"][0]["actor_name"] == "student_organizer"


def test_initializer_agent_uses_bounded_deepseek_parse_repair(monkeypatch):
    captured: dict = {}

    class FakeCall:
        id = UUID("11111111-1111-1111-1111-111111111111")
        model = "deepseek/deepseek-v4-flash"

    def fake_complete_with_audit(*args, **kwargs):
        del args
        captured.update(kwargs)
        return LLMResponse(content="{}", parsed={}, raw={}), FakeCall()

    monkeypatch.setattr(initializer_agent_module, "complete_with_audit", fake_complete_with_audit)

    result = initializer_agent_module.run_initializer_agent(
        object(),
        big_bang_id=UUID("22222222-2222-2222-2222-222222222222"),
        scenario_input={"premise": "A hospital board vote creates public concern."},
        plain_text_corpus={"simulation_brief": {"mode": "direct"}},
    )

    assert captured["metadata"]["max_attempts"] == 2
    assert captured["metadata"]["request_timeout_seconds"] == 210
    assert captured["metadata"]["retry_timeout_errors"] is False
    assert captured["metadata"]["json_repair_timeout_seconds"] == 60
    assert captured["metadata"]["max_tokens"] == 8192
    assert result["model"] == "deepseek/deepseek-v4-flash"
    assert result["fallback"] is False


def test_initializer_llm_failure_uses_deterministic_fallback(db, monkeypatch, tmp_path):
    def fake_snapshot(db, big_bang_id):
        snapshot = models.SourceOfTruthSnapshot(
            big_bang_id=big_bang_id,
            version="test",
            content_hash="hash",
            artifact_path="source-of-truth",
        )
        db.add(snapshot)
        db.flush()
        return snapshot

    monkeypatch.setattr(initializer, "snapshot_source_of_truth", fake_snapshot)
    monkeypatch.setattr(initializer, "ArtifactStore", lambda: ArtifactStore(root=tmp_path))
    monkeypatch.setattr(
        initializer,
        "run_initializer_agent",
        lambda *args, **kwargs: (_ for _ in ()).throw(LLMCallError("LLM unavailable")),
    )

    big_bang = initializer.create_big_bang(
        db,
        BigBangCreate(
            name="Fallback",
            scenario_text="A small scenario whose live initializer times out.",
            use_initializer_agent=True,
        ),
    )

    root = db.query(models.Multiverse).filter_by(big_bang_id=big_bang.id).one()
    fallback_event = (
        db.query(models.OperationLog)
        .filter_by(big_bang_id=big_bang.id, event_type="initializer_fallback_used")
        .one()
    )

    assert root.state["initializer_output"]["model"] == "deterministic_initializer_fallback"
    assert db.query(models.Actor).filter_by(big_bang_id=big_bang.id).count() > 0
    assert fallback_event.level == "warning"


def test_initializer_chunker_failure_uses_deterministic_fallback(db, monkeypatch, tmp_path):
    def fake_snapshot(db, big_bang_id):
        snapshot = models.SourceOfTruthSnapshot(
            big_bang_id=big_bang_id,
            version="test",
            content_hash="hash",
            artifact_path="source-of-truth",
        )
        db.add(snapshot)
        db.flush()
        return snapshot

    monkeypatch.setattr(initializer, "snapshot_source_of_truth", fake_snapshot)
    monkeypatch.setattr(initializer, "ArtifactStore", lambda: ArtifactStore(root=tmp_path))
    monkeypatch.setattr(
        initializer,
        "build_plain_text_corpus",
        lambda *args, **kwargs: (_ for _ in ()).throw(LLMCallError("chunker unavailable")),
    )
    monkeypatch.setattr(
        initializer,
        "run_initializer_agent",
        lambda *args, **kwargs: pytest.fail("initializer should not run after chunker failure"),
    )

    big_bang = initializer.create_big_bang(
        db,
        BigBangCreate(
            name="Chunker fallback",
            scenario_text="A long scenario whose chunk extractor fails.",
            use_initializer_agent=True,
        ),
    )

    root = db.query(models.Multiverse).filter_by(big_bang_id=big_bang.id).one()
    event_types = [
        row.event_type
        for row in db.query(models.OperationLog).filter_by(big_bang_id=big_bang.id).all()
    ]

    assert root.state["plain_text_corpus"]["simulation_brief"]["mode"] == "fallback"
    assert root.state["initializer_output"]["model"] == "deterministic_initializer_fallback"
    assert "initializer_chunker_fallback_used" in event_types
    assert "initializer_fallback_used" in event_types


def test_initializer_graph_and_emotion_casts_tolerate_bad_model_strings(db):
    big_bang, root, alpha, beta = _seed_world(db)

    persist_initializer_graphs_and_observability(
        db,
        big_bang=big_bang,
        root=root,
        actor_by_name={alpha.name.lower(): alpha, beta.name.lower(): beta},
        initializer_output={
            "graph_edges": [
                "bad",
                {
                    "source_actor_name": "Alpha",
                    "target_actor_name": "Beta",
                    "layer": "trust",
                    "weight": "strong",
                },
                {
                    "source_actor_name": "Beta",
                    "target_actor_name": "Alpha",
                    "layer": "influence",
                    "weight": "0.75-ish",
                },
            ],
            "emotion_observations": [
                "bad",
                {"actor_name": "Alpha", "emotion": "calm", "value": "about 7.5"},
                {"actor_name": "Beta", "emotion": "fear", "value": "extreme"},
            ],
            "sociology_baseline": ["bad", {"model": "attention_decay", "signal": {}}],
            "sociology_prompt_influences": ["bad", {"actor_name": "Alpha", "influence": {}}],
        },
    )
    db.flush()

    weights = sorted(float(edge.weight) for edge in db.query(models.GraphEdge).all())
    assert weights == [0.5, 0.75]
    values = sorted(float(row.value) for row in db.query(models.EmotionObservation).all())
    assert values == [0.0, 7.5]


def test_event_summary_ids_are_flushed_before_return(db, monkeypatch, tmp_path):
    big_bang, root, _alpha, _beta = _seed_world(db)
    event = models.Event(
        big_bang_id=big_bang.id,
        multiverse_id=root.id,
        event_type="announcement",
        created_tick=0,
        scheduled_tick=1,
        status="executed",
        title="Event",
        description=None,
        expected_impact={},
        actual_impact={},
        meta={},
    )
    db.add(event)
    db.flush()

    def fake_complete(db, *, big_bang_id, purpose, model, messages, metadata, json_schema=None, route=None):
        call = _fake_llm_call(db, big_bang_id=big_bang_id, purpose=purpose, model=model)
        return LLMResponse(content="summary", parsed={"what_happened": "summary"}), call

    monkeypatch.setattr(event_engine, "complete_with_audit", fake_complete)
    monkeypatch.setattr(event_engine, "ArtifactStore", lambda: ArtifactStore(root=tmp_path))

    summaries = event_engine.summarize_executed_events(db, [event])

    assert summaries[0]["summary_id"] != "None"
    assert UUID(summaries[0]["summary_id"])


def test_evolved_graph_edge_ids_are_flushed_before_return(db):
    big_bang, root, alpha, beta = _seed_world(db)
    db.add(
        models.GraphEdge(
            big_bang_id=big_bang.id,
            multiverse_id=root.id,
            tick_index=0,
            source_actor_id=alpha.id,
            target_actor_id=beta.id,
            layer="trust",
            weight=Decimal("0.4"),
            payload={},
        )
    )
    db.flush()

    snapshots = update_graph_layers(
        db,
        big_bang_id=big_bang.id,
        multiverse_id=root.id,
        tick_index=1,
        social_observations=[{"actor_id": str(alpha.id), "post": "share support"}, "bad"],
        executed_events=[{"title": "transparent aid"}],
    )

    edge_ids = [edge["edge_id"] for snapshot in snapshots for edge in snapshot["edges"]]
    assert edge_ids
    assert "None" not in edge_ids
    assert all(UUID(edge_id) for edge_id in edge_ids)
