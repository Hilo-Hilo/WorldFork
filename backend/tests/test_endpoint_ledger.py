from __future__ import annotations

import warnings
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.domains.endpoint_ledger import routes as endpoint_ledgers_api
from app.api.schemas import EndpointLedgerEvaluateRequest
from app.db import models
from app.domains.jobs import executor as job_tasks
from app.llm.schemas import LLMResponse
from app.domains.actor import agent_engine
from app.domains.endpoint_ledger import service as endpoint_ledger
from app.domains.report import engine as report_engine
from app.domains.endpoint_ledger.service import (
    _compact_value,
    endpoint_ledger_entries,
    endpoint_ledger_report_payload,
    evaluate_endpoint_ledger,
    seed_endpoint_ledger,
)


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


def _world(db: Session) -> tuple[models.BigBang, models.Multiverse, models.Actor]:
    big_bang = models.BigBang(
        name="Ledger world",
        description=None,
        scenario_input={
            "initializer_output": {
                "branch_hypotheses": [
                    {
                        "trigger": "authority decides whether the policy survives",
                        "alternate_path": "policy survives after court review",
                        "authority": "court",
                    }
                ],
                "known_uncertainties": ["whether the decision maker acts before the deadline"],
            }
        },
        status="active",
        current_config_version=1,
    )
    db.add(big_bang)
    db.flush()
    multiverse = models.Multiverse(
        big_bang_id=big_bang.id,
        parent_multiverse_id=None,
        fork_tick_index=None,
        ui_label="M1",
        depth=0,
        status="completed",
        branch_reason="Root",
        state={
            "branch_hypotheses": [
                {
                    "trigger": "authority decides whether the policy survives",
                    "alternate_path": "policy survives after court review",
                    "authority": "court",
                }
            ]
        },
        report_status="ready",
    )
    actor = models.Actor(
        big_bang_id=big_bang.id,
        actor_type="cohort",
        name="Student Cohort",
        description=None,
        archetype={},
        created_tick_index=0,
        status="active",
    )
    db.add_all([multiverse, actor])
    db.flush()
    return big_bang, multiverse, actor


@pytest.mark.parametrize(
    "field_name",
    [
        "scenarioText",
        "rawText",
        "sourceText",
        "plainText",
        "initializerPrompt",
        "systemPrompt",
        "developerPrompt",
        "userPrompt",
        "fullPrompt",
        "rawPrompt",
    ],
)
def test_compact_value_gates_mixed_style_raw_text_fields(field_name):
    raw_text = "raw endpoint ledger prompt and scenario text"

    compacted = _compact_value({field_name: raw_text})

    assert compacted == {field_name: {"present": True}}
    assert raw_text not in str(compacted)


@pytest.mark.parametrize(
    "field_name",
    [
        "plainTextCorpus",
        "rawTextCorpus",
        "sourceTextCorpus",
        "scenarioCorpus",
        "initializerCorpus",
        "promptCorpus",
        "textCorpus",
        "documentCorpus",
        "inputCorpus",
        "llmCorpus",
    ],
)
def test_compact_value_gates_mixed_style_corpus_fields(field_name):
    corpus = {"rawText": "raw endpoint ledger corpus text"}

    compacted = _compact_value({field_name: corpus})

    assert compacted == {field_name: {"present": True}}
    assert "raw endpoint ledger corpus text" not in str(compacted)


@pytest.mark.parametrize(
    "field_name",
    [
        "artifactId",
        "llmCallId",
        "markdownArtifactId",
        "pdfArtifactId",
        "auditArtifactId",
        "promptPacketArtifactId",
        "responseArtifactId",
        "parsedArtifactId",
        "sourceSnapshotArtifactId",
        "endpointLedgerId",
    ],
)
def test_compact_value_drops_mixed_style_internal_reference_ids(field_name):
    compacted = _compact_value({field_name: "4f0774fe-b2de-45dc-9918-1b837089a777"})

    assert compacted == {}


@pytest.mark.parametrize(
    "field_name",
    [
        "apiKey",
        "openrouterApiKey",
        "OPENAI_API_KEY",
        "clientSecret",
        "accessToken",
        "refreshToken",
        "authorization",
        "bearerToken",
        "password",
        "webhookSecret",
    ],
)
def test_compact_value_redacts_mixed_style_secret_fields(field_name):
    compacted = _compact_value({field_name: "super-secret-value"})

    assert compacted == {field_name: "[REDACTED]"}


@pytest.mark.parametrize(
    "field_name",
    [
        "artifactPath",
        "rawRequestPath",
        "rawResponsePath",
        "parsedPath",
        "outputPath",
        "reportPdfPath",
        "workspacePath",
        "configPath",
        "databasePath",
        "auditPath",
    ],
)
def test_compact_value_gates_mixed_style_absolute_path_fields(field_name):
    path = f"/Users/example/worldfork/{field_name}.json"

    compacted = _compact_value({field_name: path})

    assert compacted == {field_name: {"present": True}}
    assert path not in str(compacted)


@pytest.mark.parametrize(
    ("field_name", "path"),
    [
        ("artifactPath", r"C:\Users\example\worldfork\artifact.json"),
        ("rawRequestPath", r"D:\WorldFork\raw_request.json"),
        ("rawResponsePath", r"E:\WorldFork\raw_response.json"),
        ("parsedPath", r"Z:\runs\parsed.json"),
        ("outputPath", r"\\server\share\worldfork\output.json"),
        ("reportPdfPath", r"\\server\share\worldfork\report.pdf"),
        ("workspacePath", r"C:\Users\example\WorldFork\workspace"),
        ("configPath", r"C:\Users\example\.worldfork\config.json"),
        ("databasePath", r"C:\ProgramData\WorldFork\worldfork.db"),
        ("auditPath", r"\\server\audit\worldfork\audit.json"),
    ],
)
def test_compact_value_gates_windows_absolute_path_fields(field_name, path):
    compacted = _compact_value({field_name: path})

    assert compacted == {field_name: {"present": True}}
    assert path not in str(compacted)


def test_endpoint_ledger_seed_and_posthoc_evaluation_versions(db: Session):
    big_bang, multiverse, _actor = _world(db)

    seed = seed_endpoint_ledger(db, big_bang=big_bang, multiverse=multiverse)
    posthoc = evaluate_endpoint_ledger(
        db,
        big_bang=big_bang,
        multiverse=multiverse,
        source_type="posthoc_test",
        use_llm=False,
        candidate_endpoint={"label": "court rejects the policy", "description": "A new posthoc endpoint to track."},
    )

    assert seed.version == 1
    assert posthoc.version == 2
    assert posthoc.parent_ledger_version_id == seed.id
    entries = endpoint_ledger_entries(db, posthoc.id)
    assert entries
    assert {entry.endpoint_key for entry in entries} >= {
        "policy_survives_after_court_review",
        "court_rejects_the_policy",
    }
    assert endpoint_ledger_report_payload(db, posthoc)["histogram"]


def test_endpoint_ledger_seed_uses_initializer_endpoint_ledger_entries(db: Session):
    big_bang, multiverse, _actor = _world(db)
    initializer_output = dict(big_bang.scenario_input["initializer_output"])
    initializer_output["endpoint_ledger"] = [
        {
            "endpoint_key": "institutional_repair",
            "label": "Institutional repair",
            "description": "Formal institutions restore legitimacy through auditable allocation.",
            "status": "active",
            "realization_criteria": [
                "ACCS decisions are publicly auditable.",
                "Clinics and mutual-aid actors accept the allocation process.",
            ],
            "authority_refs": ["Atlas Regional Council", "Emergency Court Panel"],
            "evidence_refs": ["scenario:ACCS", "scenario:trust"],
            "blockers": ["data-smoothing scandal"],
        }
    ]
    big_bang.scenario_input = {"initializer_output": initializer_output}
    db.flush()

    seed = seed_endpoint_ledger(db, big_bang=big_bang, multiverse=multiverse)

    entries = {entry.endpoint_key: entry for entry in endpoint_ledger_entries(db, seed.id)}
    assert "institutional_repair" in entries
    assert entries["institutional_repair"].status_basis == "initializer_endpoint_ledger"
    assert entries["institutional_repair"].realization_criteria == [
        "ACCS decisions are publicly auditable.",
        "Clinics and mutual-aid actors accept the allocation process.",
    ]
    assert entries["institutional_repair"].meta["source"] == "initializer_endpoint_ledger"


def test_multiverse_ledger_evidence_excludes_sibling_events(db: Session):
    big_bang, multiverse, _actor = _world(db)
    sibling = models.Multiverse(
        big_bang_id=big_bang.id,
        parent_multiverse_id=None,
        fork_tick_index=None,
        ui_label="M2",
        depth=0,
        status="completed",
        branch_reason="Sibling",
        state={},
        report_status="ready",
    )
    db.add(sibling)
    db.flush()
    db.add(
        models.Event(
            big_bang_id=big_bang.id,
            multiverse_id=sibling.id,
            event_type="terminal",
            created_tick=1,
            scheduled_tick=2,
            status="executed",
            title="Sibling endpoint resolves",
            description="Only M2 reaches this endpoint.",
            expected_impact={},
            actual_impact={"endpoint": "sibling endpoint wins"},
        )
    )
    db.flush()

    ledger = evaluate_endpoint_ledger(db, big_bang=big_bang, multiverse=multiverse, use_llm=False)

    assert "sibling_endpoint_wins" not in {entry.endpoint_key for entry in endpoint_ledger_entries(db, ledger.id)}


def test_llm_endpoint_evaluation_preserves_existing_entries(db: Session, monkeypatch):
    big_bang, multiverse, _actor = _world(db)
    seed_endpoint_ledger(db, big_bang=big_bang, multiverse=multiverse)
    monkeypatch.setattr(
        endpoint_ledger,
        "get_settings",
        lambda: SimpleNamespace(
            default_llm_provider="openrouter",
            openrouter_api_key="test",
            god_agent_model="fallback-ledger-model",
        ),
    )

    def fake_ledger_agent(db, *, big_bang_id, purpose, model, messages, metadata, json_schema=None, route=None):
        call = models.LLMCall(
            big_bang_id=big_bang_id,
            provider="test",
            model=model,
            purpose=purpose,
            status="succeeded",
            meta=metadata,
        )
        db.add(call)
        db.flush()
        return (
            LLMResponse(
                content="{}",
                parsed={
                    "summary": "Partial evaluator response.",
                    "entries": [
                        {
                            "endpoint_key": "court_rejects_the_policy",
                            "label": "Court rejects the policy",
                            "status": "active",
                        }
                    ],
                },
            ),
            call,
        )

    monkeypatch.setattr(endpoint_ledger, "complete_with_audit", fake_ledger_agent)

    evaluated = evaluate_endpoint_ledger(db, big_bang=big_bang, multiverse=multiverse, use_llm=True)

    keys = {entry.endpoint_key for entry in endpoint_ledger_entries(db, evaluated.id)}
    assert "policy_survives_after_court_review" in keys
    assert "court_rejects_the_policy" in keys


def test_llm_endpoint_evaluation_accepts_endpoint_ledger_alias(db: Session, monkeypatch):
    big_bang, multiverse, _actor = _world(db)
    seed_endpoint_ledger(db, big_bang=big_bang, multiverse=multiverse)
    monkeypatch.setattr(
        endpoint_ledger,
        "get_settings",
        lambda: SimpleNamespace(
            default_llm_provider="openrouter",
            openrouter_api_key="test",
            god_agent_model="fallback-ledger-model",
        ),
    )

    def fake_ledger_agent(db, *, big_bang_id, purpose, model, messages, metadata, json_schema=None, route=None):
        call = models.LLMCall(
            big_bang_id=big_bang_id,
            provider="test",
            model=model,
            purpose=purpose,
            status="succeeded",
            meta=metadata,
        )
        db.add(call)
        db.flush()
        return (
            LLMResponse(
                content="{}",
                parsed={
                    "summary": "Alias evaluator response.",
                    "endpoint_ledger": [
                        {
                            "endpoint_key": "transit_fare_hike_dispute",
                            "label": "Transit fare hike dispute",
                            "status": "active",
                        }
                    ],
                },
            ),
            call,
        )

    monkeypatch.setattr(endpoint_ledger, "complete_with_audit", fake_ledger_agent)

    evaluated = evaluate_endpoint_ledger(db, big_bang=big_bang, multiverse=multiverse, use_llm=True)

    keys = {entry.endpoint_key for entry in endpoint_ledger_entries(db, evaluated.id)}
    assert "transit_fare_hike_dispute" in keys


def test_endpoint_finalization_ignores_legacy_probabilities():
    entries = endpoint_ledger._finalize_entries(
        [
            {"endpoint_key": "a", "label": "A", "status": "active", "probability": 0.9},
            {"endpoint_key": "b", "label": "B", "status": "active", "probability": 0.9},
        ]
    )

    assert {item["probability"] for item in entries} == {None}
    assert all("legacy_probability_ignored" in item["meta"] for item in entries)


def test_endpoint_finalization_marks_final_horizon_as_insufficient_ticks():
    evidence = {
        "big_bang": {"simulation_config": {"max_ticks": 3}},
        "multiverse": {"status": "completed"},
        "ticks": [{"tick_index": 3}],
    }
    entries = endpoint_ledger._finalize_entries(
        [
            {
                "endpoint_key": "law_changed",
                "label": "Law changed",
                "status": "active",
                "evidence_refs": [{"source": "tick"}],
            },
            {
                "endpoint_key": "organizing_continues",
                "label": "Organizing continues",
                "status": "active",
            },
        ],
        evidence=evidence,
    )
    by_key = {entry["endpoint_key"]: entry for entry in entries}

    assert by_key["law_changed"]["status"] == "insufficient_ticks"
    assert by_key["law_changed"]["meta"]["previous_status"] == "active"
    assert by_key["law_changed"]["meta"]["reversible_on_resume"] is True
    assert by_key["organizing_continues"]["status"] == "insufficient_ticks"


def test_endpoint_finalization_marks_frozen_final_horizon_as_insufficient_ticks():
    evidence = {
        "big_bang": {"simulation_config": {"max_ticks": 3}},
        "multiverse": {"status": "frozen"},
        "ticks": [{"tick_index": 3}],
    }
    entries = endpoint_ledger._finalize_entries(
        [
            {
                "endpoint_key": "law_changed",
                "label": "Law changed",
                "status": "active",
            }
        ],
        evidence=evidence,
    )

    assert entries[0]["status"] == "insufficient_ticks"
    assert entries[0]["meta"]["previous_status"] == "process_only"


def test_frozen_multiverse_unresolved_endpoint_weights_as_insufficient_ticks():
    assert (
        endpoint_ledger._representative_status_for_weighting("active", "frozen")
        == "insufficient_ticks"
    )


def test_endpoint_final_horizon_overlay_reverts_after_resume():
    entries = endpoint_ledger._finalize_entries(
        [
            {
                "endpoint_key": "law_changed",
                "label": "Law changed",
                "status": "insufficient_ticks",
                "blockers": ["max_ticks_reached_before_terminal_endpoint"],
                "meta": {
                    "final_horizon_overlay": "insufficient_ticks",
                    "previous_status": "active",
                    "reversible_on_resume": True,
                },
            }
        ],
        evidence={"big_bang": {"simulation_config": {"max_ticks": 3}}, "multiverse": {"status": "active"}, "ticks": [{"tick_index": 4}]},
    )

    assert entries[0]["status"] == "active"
    assert "max_ticks_reached_before_terminal_endpoint" not in entries[0]["blockers"]
    assert "final_horizon_overlay" not in entries[0]["meta"]


def test_report_patch_does_not_replace_terminal_outcome_with_unresolved_endpoint():
    content = {
        "outcome_conclusions": {
            "likely_endpoint": {
                "endpoint_key": "accept_terminal",
                "interpretation": "God accepted the terminal outcome.",
            }
        },
        "endpoint_histogram": [
            {
                "endpoint_key": "endpoint_unresolved",
                "label": "Endpoint unresolved",
                "status": "unresolved",
                "path_mass": 1.0,
            }
        ],
    }

    report_engine._patch_outcome_conclusions_from_endpoint_ledger(content)

    assert content["outcome_conclusions"]["likely_endpoint"]["endpoint_key"] == "accept_terminal"


def test_report_generation_includes_endpoint_ledger_payload(db: Session, monkeypatch):
    big_bang, multiverse, _actor = _world(db)
    tick = models.TickSnapshot(
        big_bang_id=big_bang.id,
        multiverse_id=multiverse.id,
        tick_index=1,
        ui_label="M1 T1",
        status="final",
        provisional_bundle={},
        final_bundle={"branch_score": 0.42},
        summary="The court review process remains unresolved.",
    )
    db.add(tick)
    db.flush()

    monkeypatch.setattr(
        report_engine,
        "get_settings",
        lambda: SimpleNamespace(
            default_llm_provider="openrouter",
            openrouter_api_key="test",
            report_agent_model="test-report-model",
        ),
    )

    def fake_report_agent(
        db,
        *,
        big_bang_id,
        purpose,
        model,
        messages,
        metadata,
        json_schema=None,
        json_response_transform=None,
        route=None,
    ):
        call = models.LLMCall(
            big_bang_id=big_bang_id,
            provider="test",
            model=model,
            purpose=purpose,
            status="succeeded",
            meta=metadata,
        )
        db.add(call)
        db.flush()
        return (
            LLMResponse(
                content="{}",
                parsed={
                    "report_markdown": "# LLM Report\n\nEndpoint-ledger backed report.",
                    "executive_summary": "Endpoint-aware report.",
                    "outcome_interpretation": "Uses the ledger.",
                    "management_notes": "None.",
                    "risk_notes": "Unresolved.",
                    "endpoint_histogram": [],
                    "terminality_assessment": {},
                    "contradiction_check": {},
                },
            ),
            call,
        )

    monkeypatch.setattr(report_engine, "complete_with_audit", fake_report_agent)

    version = report_engine.generate_multiverse_report(db, multiverse=multiverse)
    body = report_engine.render_report_version_to_markdown(version)

    assert version.content["endpoint_ledger"]["entries"]
    assert version.content["endpoint_histogram"]
    assert version.content["llm_report"]["endpoint_histogram"]
    assert version.markdown_artifact_id is None
    assert "## Structured Evidence Appendix" in body


def test_event_sanity_gate_retries_invalid_cohort_direct_authority_event(db: Session, monkeypatch):
    big_bang, multiverse, actor = _world(db)
    invalid_action = {
        "actor_id": actor.id,
        "proposed_event": {
            "title": "Students pass legislation",
            "event_type": "legislation",
            "description": "Students pass a bill changing state law.",
        },
    }

    def fake_retry(db, *, big_bang, multiverse, actor, tick_index, prompt_context):
        assert prompt_context["event_validation_feedback"]["blocked_events"][0]["rule_id"] == "legislation_direct_action"
        return {
            "actor_output": {"actor_id": str(actor.id), "llm_call_id": None, "parsed": {}},
            "parsed_actions": [
                {
                    "actor_id": actor.id,
                    "proposed_event": {
                        "title": "Students petition lawmakers",
                        "event_type": "organizing",
                        "description": "Students petition lawmakers to consider a bill.",
                        "scheduled_tick": 2,
                    },
                }
            ],
            "emotion_self_ratings": [],
        }

    monkeypatch.setattr(agent_engine, "run_actor_decision", fake_retry)

    repaired = agent_engine.validate_and_repair_event_actions(
        db,
        big_bang=big_bang,
        multiverse=multiverse,
        tick_index=1,
        prompt_context={},
        agent_result={
            "actor_outputs": [{"actor_id": str(actor.id), "parsed": {"old": True}}],
            "parsed_actions": [invalid_action],
            "emotion_self_ratings": [{"actor_id": actor.id, "emotion": "anger", "value": 8}],
        },
        max_retries=3,
    )

    assert repaired["event_validation"]["status"] == "passed"
    assert repaired["event_validation"]["attempts"][0]["invalid_count"] == 1
    assert repaired["parsed_actions"][0]["proposed_event"]["title"] == "Students petition lawmakers"
    assert repaired["actor_outputs"] == [
        {"actor_id": str(actor.id), "llm_call_id": None, "parsed": {}, "event_validation_retry_round": 1}
    ]
    assert repaired["emotion_self_ratings"] == []
    queued = agent_engine.queue_agent_events(
        db,
        big_bang_id=big_bang.id,
        multiverse_id=multiverse.id,
        tick_index=1,
        parsed_actions=repaired["parsed_actions"],
    )
    assert queued[0]["title"] == "Students petition lawmakers"


def test_event_sanity_gate_rejects_payload_self_authorized_direct_action(db: Session):
    big_bang, multiverse, actor = _world(db)
    action = {
        "actor_id": actor.id,
        "proposed_event": {
            "title": "Students demand lawmakers pass legislation",
            "event_type": "legislation",
            "description": "Students demand lawmakers pass a bill changing state law.",
            "preconditions": {"legal_authority": True},
            "authority_refs": ["student_claimed_authority"],
        },
    }

    with pytest.raises(agent_engine.EventValidationError, match="God event sanity gate rejected"):
        agent_engine.validate_and_repair_event_actions(
            db,
            big_bang=big_bang,
            multiverse=multiverse,
            tick_index=1,
            prompt_context={},
            agent_result={"actor_outputs": [], "parsed_actions": [action], "emotion_self_ratings": []},
            max_retries=0,
        )


def test_event_sanity_gate_allows_indirect_pressure_to_authority(db: Session):
    big_bang, multiverse, actor = _world(db)
    action = {
        "actor_id": actor.id,
        "proposed_event": {
            "title": "Students petition lawmakers",
            "event_type": "organizing",
            "description": "Students petition lawmakers to consider a bill.",
            "scheduled_tick": 2,
        },
    }

    repaired = agent_engine.validate_and_repair_event_actions(
        db,
        big_bang=big_bang,
        multiverse=multiverse,
        tick_index=1,
        prompt_context={},
        agent_result={"actor_outputs": [], "parsed_actions": [action], "emotion_self_ratings": []},
        max_retries=0,
    )

    assert repaired["event_validation"]["status"] == "passed"


def test_endpoint_ledger_api_creates_fresh_default_evaluation_jobs(db: Session, monkeypatch):
    big_bang, _multiverse, _actor = _world(db)
    keys = []

    def fake_create_job_record(payload, db):
        keys.append(payload.idempotency_key)
        return SimpleNamespace(id=uuid4(), status="queued")

    monkeypatch.setattr(endpoint_ledgers_api, "create_job_record", fake_create_job_record)

    first = endpoint_ledgers_api.evaluate_big_bang_ledger(
        big_bang.id,
        EndpointLedgerEvaluateRequest(),
        db,
    )
    second = endpoint_ledgers_api.evaluate_big_bang_ledger(
        big_bang.id,
        EndpointLedgerEvaluateRequest(),
        db,
    )

    assert first["job_id"] != second["job_id"]
    assert len(keys) == 2
    assert keys[0] != keys[1]


def test_endpoint_ledger_job_rejects_cross_big_bang_multiverse(db: Session):
    big_bang, multiverse, _actor = _world(db)
    other = models.BigBang(
        name="Other",
        description=None,
        scenario_input={},
        status="active",
        current_config_version=1,
    )
    db.add(other)
    db.flush()
    job = models.Job(
        job_type="evaluate_endpoint_ledger",
        queue_name="reports",
        status="queued",
        big_bang_id=other.id,
        payload={
            "big_bang_id": str(other.id),
            "scope": "multiverse",
            "multiverse_id": str(multiverse.id),
        },
        idempotency_key="cross-bb",
    )

    with pytest.raises(ValueError, match="multiverse does not belong"):
        job_tasks._execute_job(db, job)

    assert db.scalar(select(models.BigBang).where(models.BigBang.id == big_bang.id)) is big_bang
