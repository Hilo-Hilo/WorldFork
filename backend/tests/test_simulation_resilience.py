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
from app.domains.actor import agent_engine
from app.domains.event import event_engine
from app.domains.big_bang import initializer
from app.domains.sociology.graph_engine import update_graph_layers
from app.domains.big_bang.initializer import persist_initializer_graphs_and_observability
from app.storage.artifact_store import ArtifactStore
from backend.app.models.base import Base as RuntimeControlBase
from backend.app.models.settings import GlobalSettingModel


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
    captured_messages = []

    def fake_complete(db, *, big_bang_id, purpose, model, messages, metadata, json_schema=None, route=None):
        captured_messages.append({"messages": messages, "metadata": metadata})
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

    messages = captured_messages[0]["messages"]
    assert messages[1]["content"].startswith("Shared tick context for all actor decisions")
    assert "Actor:" not in messages[1]["content"]
    assert "Past water pressure loss" in messages[2]["content"]
    assert "Alpha schedules clinic briefing" in messages[2]["content"]
    assert "own_queued_events" in messages[2]["content"]
    assert captured_messages[0]["metadata"]["prompt_cache_strategy"] == "openrouter_implicit_sticky"
    assert captured_messages[0]["metadata"]["prompt_cache_stable_prefix_messages"] == 2


def test_event_queue_prompt_context_budgets_long_queues_with_omission_summary(db):
    big_bang, root, alpha, _beta = _seed_world(db)
    for index in range(40):
        db.add(
            models.Event(
                big_bang_id=big_bang.id,
                multiverse_id=root.id,
                creator_actor_id=alpha.id,
                event_type="announcement",
                created_tick=1,
                scheduled_tick=2 + index,
                status="queued",
                title=f"Future event {index}",
                description=f"Long event description {index} " + ("detail " * 300),
                expected_impact={"pressure": "rising", "index": index},
                actual_impact={},
                meta={"source": "agent_proposal"},
            )
        )
    db.flush()

    context = event_engine.build_event_queue_prompt_context(
        db,
        multiverse_id=root.id,
        tick_index=1,
        actor_id=alpha.id,
        future_limit=40,
    )

    assert 1 <= len(context["upcoming_events"]) <= 12
    assert len(context["own_queued_events"]) == 12
    assert len(context["upcoming_events"][0]["description"]) < 700
    assert context["prompt_budget"]["estimated_chars"] <= context["prompt_budget"]["max_chars"]
    assert context["prompt_budget"]["omitted_total"] >= 56
    assert context["prompt_budget"]["sections"]["upcoming_events"]["omitted_count"] >= 28
    assert context["prompt_budget"]["sections"]["upcoming_events"]["budget_trimmed"] is True


def test_actor_llm_call_metadata_records_canonical_source(db, monkeypatch):
    big_bang, root, alpha, _beta = _seed_world(db)
    captured_metadata = []

    def fake_complete(db, *, big_bang_id, purpose, model, messages, metadata, json_schema=None, route=None):
        captured_metadata.append({"metadata": metadata, "route": route})
        call = _fake_llm_call(db, big_bang_id=big_bang_id, purpose=purpose, model=model)
        return LLMResponse(content="{}", parsed={"social_actions": [], "proposed_events": []}), call

    monkeypatch.setattr(agent_engine, "complete_with_audit", fake_complete)

    agent_engine.run_actor_decision(
        db,
        big_bang=big_bang,
        multiverse=root,
        actor=alpha,
        tick_index=2,
        prompt_context={},
    )

    assert captured_metadata[0]["route"] == "cohort_agent"
    metadata = captured_metadata[0]["metadata"]
    assert metadata["agent_source"] == "cohort_agent"
    assert metadata["canonical_job_type"] == "actor_deliberation_call"
    assert metadata["actor_id"] == str(alpha.id)
    assert metadata["actor_type"] == "cohort"


def test_actor_worker_mode_releases_db_transaction_before_llm_call(db, monkeypatch):
    big_bang, root, alpha, _beta = _seed_world(db)
    observed = {}

    def fake_complete(db, *, big_bang_id, purpose, model, messages, metadata, json_schema=None, route=None):
        observed["in_transaction_during_llm"] = db.in_transaction()
        call = _fake_llm_call(db, big_bang_id=big_bang_id, purpose=purpose, model=model)
        return LLMResponse(content="{}", parsed={"social_actions": [], "proposed_events": []}), call

    monkeypatch.setattr(agent_engine, "complete_with_audit", fake_complete)

    agent_engine.run_actor_decision(
        db,
        big_bang=big_bang,
        multiverse=root,
        actor=alpha,
        tick_index=2,
        prompt_context={},
        release_db_connection_before_llm=True,
    )

    assert observed["in_transaction_during_llm"] is False


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


def test_big_bang_defaults_use_persisted_global_settings(db, monkeypatch, tmp_path):
    RuntimeControlBase.metadata.create_all(
        bind=db.get_bind(),
        tables=[RuntimeControlBase.metadata.tables["settings_global"]],
    )
    db.add(
        GlobalSettingModel(
            setting_id="default",
            default_tick_duration_minutes=90,
            default_max_ticks=33,
            default_max_schedule_horizon_ticks=7,
            log_level="INFO",
            display_timezone="UTC",
            theme="system",
            enable_oasis_adapter=False,
            branching_defaults={
                "max_branch_depth": 9,
                "max_active_multiverses": 77,
                "max_branches_per_tick": 6,
                "branch_score_threshold": 0.12,
            },
            payload={},
        )
    )
    db.commit()

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

    big_bang = initializer.create_big_bang(
        db,
        BigBangCreate(
            name="Settings defaults",
            scenario_text="A town prepares a heat response plan.",
            use_initializer_agent=False,
        ),
    )

    config = db.query(models.BigBangConfig).filter_by(big_bang_id=big_bang.id).one()
    assert config.simulation_config["tick_duration"] == "90 minutes"
    assert config.simulation_config["tick_duration_minutes"] == 90
    assert config.simulation_config["max_ticks"] == 33
    assert config.simulation_config["max_schedule_horizon_ticks"] == 7
    assert config.branch_policy["max_branch_depth"] == 9
    assert config.branch_policy["max_active_multiverses"] == 77
    assert config.branch_policy["max_branches_per_tick"] == 6
    assert config.branch_policy["branch_score_threshold"] == 0.12


def test_failed_initializer_agent_does_not_leave_draft_big_bang(db, monkeypatch, tmp_path):
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

    def fail_after_audit_commit(db, *args, **kwargs):
        db.add(
            models.LLMCall(
                big_bang_id=kwargs["big_bang_id"],
                provider="openrouter",
                model="google/gemini-3.1-flash-lite",
                purpose="initializer_agent",
                status="failed",
                meta={"error": "provider unavailable"},
            )
        )
        db.commit()
        raise LLMCallError("provider unavailable")

    monkeypatch.setattr(initializer, "snapshot_source_of_truth", fake_snapshot)
    monkeypatch.setattr(initializer, "ArtifactStore", lambda: ArtifactStore(root=tmp_path))
    monkeypatch.setattr(initializer, "run_initializer_agent", fail_after_audit_commit)

    with pytest.raises(LLMCallError):
        initializer.create_big_bang(
            db,
            BigBangCreate(
                name="Failed init",
                scenario_text="A river town faces a flood warning.",
                use_initializer_agent=True,
            ),
        )

    assert db.query(models.BigBang).count() == 0
    assert db.query(models.SourceOfTruthSnapshot).count() == 0
    assert db.query(models.LLMCall).count() == 0


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
