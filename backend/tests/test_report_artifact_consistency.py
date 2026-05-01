from __future__ import annotations

import warnings
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import models
from app.jobs.tasks import execute_job
from app.llm.audit import LLMCallError
from app.llm.schemas import LLMResponse
from app.simulation import report_engine
from app.storage.artifact_store import ArtifactStore, hash_directory


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
            warnings.filterwarnings(
                "ignore",
                message="Can't sort tables for DROP",
                category=SAWarning,
            )
            models.Base.metadata.drop_all(engine)


def test_failed_report_job_rolls_back_completed_report_state(db: Session, monkeypatch, tmp_path):
    big_bang = models.BigBang(
        name="Report consistency",
        description=None,
        scenario_input={},
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
        status="active",
        branch_reason="Root",
        state={},
        report_status="not_ready",
    )
    db.add(multiverse)
    db.flush()
    report = models.Report(
        big_bang_id=big_bang.id,
        multiverse_id=multiverse.id,
        report_type="multiverse",
        status="draft",
        current_version=0,
    )
    job = models.Job(
        job_type="generate_multiverse_report",
        status="queued",
        big_bang_id=big_bang.id,
        payload={"multiverse_id": str(multiverse.id)},
        result={},
        idempotency_key=f"report:{uuid4()}",
    )
    db.add_all([report, job])
    db.commit()

    monkeypatch.setattr(
        report_engine,
        "ArtifactStore",
        lambda: ArtifactStore(root=tmp_path / "artifacts"),
    )
    _install_fake_report_agent(monkeypatch)

    def fail_markdown(*args, **kwargs):
        raise RuntimeError("markdown writer failed")

    monkeypatch.setattr(report_engine, "_write_markdown_artifact", fail_markdown)

    execute_job(db, job)
    db.commit()
    db.expire_all()

    persisted_report = db.scalar(select(models.Report).where(models.Report.id == report.id))
    persisted_multiverse = db.get(models.Multiverse, multiverse.id)

    assert job.status == "failed"
    assert "markdown writer failed" in job.error
    assert persisted_report.status == "draft"
    assert persisted_report.current_version == 0
    assert persisted_multiverse.report_status == "not_ready"
    assert db.scalars(select(models.ReportVersion)).all() == []
    assert not list((tmp_path / "artifacts").rglob("*.md"))


def test_final_report_inventory_uses_committed_status_and_version(db: Session, monkeypatch, tmp_path):
    big_bang = models.BigBang(
        name="Final inventory",
        description=None,
        scenario_input={},
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
        state={},
        report_status="ready",
    )
    db.add(multiverse)
    db.flush()
    db.add(
        models.Report(
            big_bang_id=big_bang.id,
            multiverse_id=multiverse.id,
            report_type="multiverse",
            status="completed",
            current_version=1,
        )
    )
    db.flush()

    artifact_root = tmp_path / "artifacts"
    monkeypatch.setattr(
        report_engine,
        "ArtifactStore",
        lambda: ArtifactStore(root=artifact_root),
    )
    _install_fake_report_agent(monkeypatch)

    report_version = report_engine.generate_final_big_bang_report(db, big_bang=big_bang)
    markdown = db.get(models.Artifact, report_version.markdown_artifact_id)

    body = Path(markdown.path).read_text(encoding="utf-8")
    assert body.startswith("# LLM Report")
    assert "## Structured Evidence Appendix" in body
    assert "- final_big_bang: completed v1" in body
    assert "- multiverse (M1): completed v1" in body
    assert "- final_big_bang: draft v0" not in body
    assert report_version.content["multiverse_comparison"][0]["report_status"] == "completed"
    assert report_version.content["outcome_distribution"]["report_statuses"] == {"completed": 1}


def test_final_report_includes_deterministic_outcome_conclusions(db: Session, monkeypatch, tmp_path):
    big_bang = models.BigBang(
        name="Outcome clarity",
        description=None,
        scenario_input={},
        status="completed",
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
        state={},
        report_status="completed",
    )
    db.add(multiverse)
    db.flush()
    tick = models.TickSnapshot(
        big_bang_id=big_bang.id,
        multiverse_id=multiverse.id,
        tick_index=3,
        ui_label="M1",
        status="final",
        provisional_bundle={},
        final_bundle={"branch_score": 0.82},
        summary="The evacuation endpoint stabilizes after the dam breach.",
    )
    db.add(tick)
    db.flush()
    event = models.Event(
        big_bang_id=big_bang.id,
        multiverse_id=multiverse.id,
        event_type="infrastructure_failure",
        created_tick=2,
        scheduled_tick=3,
        status="executed",
        title="Dam breach",
        description="Reservoir wall fails and triggers evacuation.",
        expected_impact={"risk": "flooding"},
        actual_impact={"endpoint": "evacuated valley"},
        meta={},
    )
    db.add(event)
    db.flush()
    db.add(
        models.EventSummary(
            event_id=event.id,
            tick_snapshot_id=tick.id,
            version=1,
            summary="Dam breach forced relocation and ended the valley settlement path.",
        )
    )
    db.add(
        models.GodAgentReview(
            big_bang_id=big_bang.id,
            multiverse_id=multiverse.id,
            tick_snapshot_id=tick.id,
            decision="accept_terminal",
            rationale="The population has reached a stable evacuated endpoint.",
            confidence=0.9,
            input_summary={},
            output={},
        )
    )
    db.flush()

    artifact_root = tmp_path / "artifacts"
    monkeypatch.setattr(report_engine, "ArtifactStore", lambda: ArtifactStore(root=artifact_root))
    _install_fake_report_agent(
        monkeypatch,
        outcome_interpretation="The report interprets the likely endpoint from deterministic evidence traces.",
    )

    report_version = report_engine.generate_final_big_bang_report(db, big_bang=big_bang)
    conclusions = report_version.content["outcome_conclusions"]
    markdown = db.get(models.Artifact, report_version.markdown_artifact_id)
    body = Path(markdown.path).read_text(encoding="utf-8")

    assert conclusions["likely_endpoint"]["latest_tick_summary"] == tick.summary
    assert conclusions["likely_endpoint"]["god_decision"] == "accept_terminal"
    assert conclusions["key_event_traces"][0]["summary"] == (
        "Dam breach forced relocation and ended the valley settlement path."
    )
    assert "likely endpoint" in report_version.content["ai_summary"]["outcome_interpretation"]
    assert "## Outcome Conclusions" in body
    assert "### Likely Endpoint" in body
    assert "Dam breach forced relocation" in body
    assert "The population has reached a stable evacuated endpoint." in body


def test_report_version_stores_structured_content_and_renders_pdf_on_demand(db: Session, monkeypatch, tmp_path):
    big_bang = models.BigBang(
        name="Structured reports",
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
    multiverse = models.Multiverse(
        big_bang_id=big_bang.id,
        parent_multiverse_id=None,
        fork_tick_index=None,
        ui_label="M1",
        version=2,
        depth=0,
        status="completed",
        branch_reason="Root",
        state={"runtime_config_version": 1},
        report_status="ready",
    )
    db.add(multiverse)
    db.flush()
    artifact_root = tmp_path / "artifacts"
    monkeypatch.setattr(report_engine, "ArtifactStore", lambda: ArtifactStore(root=artifact_root))
    _install_fake_report_agent(monkeypatch)

    def fake_pdf(db, *, big_bang_id, relative_path, title, markdown):
        return ArtifactStore(root=artifact_root).write_bytes(
            db,
            big_bang_id=big_bang_id,
            relative_path=relative_path,
            body=b"%PDF-1.4\n",
            kind="report_pdf",
            content_type="application/pdf",
        )

    monkeypatch.setattr(report_engine, "render_markdown_pdf", fake_pdf)

    report_version = report_engine.generate_multiverse_report(db, multiverse=multiverse)

    assert report_version.source_multiverse_version == 2
    assert report_version.source_big_bang_config_version == 1
    assert report_version.model
    assert report_version.content["source"]["multiverse_version"] == 2
    assert report_version.content["llm_report"]["report_markdown"].startswith("# LLM Report")
    assert report_version.generation_metadata["storage"]["canonical"] == "report_versions.content"
    assert report_version.pdf_artifact_id is None

    pdf_artifact = report_engine.render_report_version_artifact(
        db,
        report_version=report_version,
        output_format="pdf",
    )

    assert pdf_artifact.content_type == "application/pdf"
    assert report_version.pdf_artifact_id == pdf_artifact.id


def test_report_agent_retries_with_smaller_rescue_digest(db: Session, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(
        report_engine,
        "get_settings",
        lambda: SimpleNamespace(
            default_llm_provider="openrouter",
            openrouter_api_key="test-key",
            report_agent_model="test-report-model",
        ),
    )
    calls = []

    def fake_complete_with_audit(db, *, big_bang_id, purpose, model, messages, metadata, json_schema=None, route=None):
        calls.append(
            {
                "purpose": purpose,
                "messages": messages,
                "metadata": metadata,
                "json_schema": json_schema,
            }
        )
        if len(calls) == 1:
            raise LLMCallError("transient report failure")
        return (
            LLMResponse(
                content="{}",
                parsed={
                    "report_markdown": "# Rescued Report\n\nThis is a generated long-form report from the LLM agent.",
                    "executive_summary": "rescued",
                    "outcome_interpretation": "used compact digest",
                    "management_notes": "review top timelines",
                    "risk_notes": "none",
                },
                raw={},
            ),
            SimpleNamespace(id=uuid4(), meta=metadata),
        )

    monkeypatch.setattr(report_engine, "complete_with_audit", fake_complete_with_audit)
    content = {
        "report_type": "final_big_bang",
        "title": "Retry report",
        "summary": "Retry summary",
        "source": {"big_bang_id": str(uuid4()), "report_version": 1},
        "outcome_distribution": {
            "timeline_statuses": {"completed": 20},
            "total_artifacts": 200,
            "total_llm_calls": 30,
        },
        "multiverse_comparison": [
            {
                "multiverse_id": str(uuid4()),
                "ui_label": f"M{index}",
                "status": "completed",
                "report_status": "completed",
                "latest_branch_score": index / 100,
                "tick_count": index,
                "cohort_state_highlights": [{"cohort_id": f"c{index}", "mood": "watchful"}],
            }
            for index in range(20)
        ],
        "sections": [{"heading": "Divergence Drivers", "items": [{"reason": "forked"}]}],
    }

    llm_report, llm_call = report_engine._run_report_agent(db, big_bang_id=uuid4(), content=content)

    assert llm_report["executive_summary"] == "rescued"
    assert llm_report["report_markdown"].startswith("# Rescued Report")
    assert llm_call.meta["prompt_mode"] == "rescue"
    assert [call["metadata"]["prompt_mode"] for call in calls] == ["standard", "rescue"]
    standard_payload = _report_agent_prompt_payload(calls[0])
    rescue_payload = _report_agent_prompt_payload(calls[1])
    assert len(standard_payload["selected_timelines"]) == report_engine.REPORT_AGENT_STANDARD_TIMELINE_LIMIT
    assert len(rescue_payload["selected_timelines"]) == report_engine.REPORT_AGENT_RESCUE_TIMELINE_LIMIT
    assert "total_artifacts" not in rescue_payload["outcome_distribution"]
    assert "total_llm_calls" not in rescue_payload["outcome_distribution"]
    assert "multiverse_id" not in calls[1]["messages"][1]["content"]
    assert calls[1]["json_schema"] == report_engine.REPORT_AGENT_JSON_SCHEMA


def test_report_agent_failure_raises_instead_of_storing_deterministic_report(db: Session, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(
        report_engine,
        "get_settings",
        lambda: SimpleNamespace(
            default_llm_provider="openrouter",
            openrouter_api_key="test-key",
            report_agent_model="test-report-model",
        ),
    )
    calls = []

    def fake_complete_with_audit(db, *, big_bang_id, purpose, model, messages, metadata, json_schema=None, route=None):
        calls.append(metadata["prompt_mode"])
        raise LLMCallError("report model unavailable")

    monkeypatch.setattr(report_engine, "complete_with_audit", fake_complete_with_audit)
    content = {
        "report_type": "final_big_bang",
        "summary": "Structured final report.",
        "outcome_distribution": {
            "timeline_statuses": {"completed": 1, "terminated": 1},
            "god_decisions": {"branch": 2, "continue": 1},
            "total_social_posts": 9,
            "total_sociology_signals": 5,
            "total_graph_edges": 4,
        },
        "multiverse_comparison": [
            {
                "ui_label": "M1",
                "status": "completed",
                "report_status": "completed",
                "latest_branch_score": 0.2,
                "cohort_state_count": 1,
            },
            {
                "ui_label": "M2",
                "status": "terminated",
                "report_status": "ready",
                "latest_branch_score": 0.88,
                "hero_state_count": 1,
            },
        ],
        "sections": [{"heading": "Divergence Drivers", "items": [{"reason": "threshold crossed"}]}],
    }

    with pytest.raises(LLMCallError, match="failed after standard and rescue attempts"):
        report_engine._run_report_agent(db, big_bang_id=uuid4(), content=content)

    assert calls == ["standard", "rescue"]


def test_report_agent_rejects_non_llm_report_payload(db: Session, monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(
        report_engine,
        "get_settings",
        lambda: SimpleNamespace(
            default_llm_provider="openrouter",
            openrouter_api_key="test-key",
            report_agent_model="test-report-model",
        ),
    )

    def fake_complete_with_audit(db, *, big_bang_id, purpose, model, messages, metadata, json_schema=None, route=None):
        return (
            LLMResponse(
                content="{}",
                parsed={"executive_summary": "fallback without report markdown"},
                raw={},
            ),
            SimpleNamespace(id=uuid4(), meta=metadata),
        )

    monkeypatch.setattr(report_engine, "complete_with_audit", fake_complete_with_audit)

    with pytest.raises(LLMCallError, match="report_markdown"):
        report_engine._run_report_agent(
            db,
            big_bang_id=uuid4(),
            content={
                "report_type": "multiverse",
                "source": {"multiverse_id": str(uuid4()), "report_version": 1},
                "outcome_distribution": {},
                "sections": [],
            },
        )


def test_multiverse_metrics_include_compact_state_and_event_highlights(db: Session):
    big_bang = models.BigBang(name="Metrics", scenario_input={}, status="active")
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
            "cohort_current_states": [
                {
                    "state": {
                        "trust": 0.61,
                        "attention_level": 0.395,
                        "expression_level": 0.314,
                        "mobilization_readiness": 0.1538,
                    },
                    "irrelevant_long_field": "x" * 1000,
                }
            ],
            "hero_current_states": [
                {
                    "actor_id": "hero-a",
                    "state": {"readiness": 0.8, "current_strategy": "reduce_conflict"},
                }
            ],
        },
        report_status="ready",
    )
    db.add(multiverse)
    db.flush()
    db.add(
        models.Event(
            big_bang_id=big_bang.id,
            multiverse_id=multiverse.id,
            event_type="announcement",
            created_tick=0,
            scheduled_tick=1,
            status="executed",
            title="Long public hearing",
            description=None,
            expected_impact={},
            actual_impact={"trust": "lower"},
            meta={},
        )
    )
    db.flush()

    metrics = report_engine._multiverse_metrics(db, multiverse, latest_tick=None)

    assert metrics["cohort_state_highlights"] == [
        {
            "attention_level": 0.395,
            "expression_level": 0.314,
            "trust": 0.61,
            "mobilization_readiness": 0.1538,
        }
    ]
    assert metrics["hero_state_highlights"] == [
        {"actor_id": "hero-a", "readiness": 0.8, "current_strategy": "reduce_conflict"}
    ]
    assert metrics["recent_event_highlights"][0]["title"] == "Long public hearing"


def test_artifact_file_is_removed_when_db_flush_fails(tmp_path: Path):
    store = ArtifactStore(root=tmp_path / "artifacts")
    db = _FailingFlushSession()

    with pytest.raises(RuntimeError, match="flush failed"):
        store.write_text(
            db,
            big_bang_id=None,
            relative_path="reports/report.md",
            body="partial",
            kind="report_markdown",
        )

    assert not (tmp_path / "artifacts/reports/report.md").exists()


def test_artifact_cleanup_does_not_remove_reused_existing_file(tmp_path: Path):
    store = ArtifactStore(root=tmp_path / "artifacts")
    existing = store.write_text(
        _SuccessfulFlushSession(),
        big_bang_id=None,
        relative_path="reports/report.md",
        body="already committed",
        kind="report_markdown",
    )

    with pytest.raises(RuntimeError, match="flush failed"):
        store.write_text(
            _FailingFlushSession(),
            big_bang_id=None,
            relative_path="reports/report.md",
            body="already committed",
            kind="report_markdown",
        )

    assert Path(existing.path).read_text() == "already committed"


def test_hash_directory_frames_paths_and_contents(tmp_path: Path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "ab").write_text("c", encoding="utf-8")
    (second / "a").write_text("bc", encoding="utf-8")

    assert hash_directory(first) != hash_directory(second)


class _FailingFlushSession:
    def add(self, obj):
        return None

    def flush(self):
        raise RuntimeError("flush failed")


class _SuccessfulFlushSession:
    def add(self, obj):
        return None

    def flush(self):
        return None


def _report_agent_prompt_payload(call):
    return json_loads_from_digest_message(call["messages"][1]["content"])


def json_loads_from_digest_message(message: str):
    import json

    return json.loads(message.split("\n", 1)[1])


def _install_fake_report_agent(monkeypatch, *, outcome_interpretation: str = "Interprets structured metrics."):
    def fake_report_agent(db, *, big_bang_id, content):
        return (
            {
                "report_markdown": (
                    "# LLM Report\n\n"
                    "## Executive Summary\n\n"
                    "This is a generated long-form report body supplied by the test LLM agent.\n\n"
                    "## Outcome Interpretation\n\n"
                    "The report interprets the structured WorldFork metrics without using a deterministic fallback."
                ),
                "executive_summary": "Generated by test LLM agent.",
                "outcome_interpretation": outcome_interpretation,
                "management_notes": "Review evidence appendix.",
                "risk_notes": "Test double only.",
            },
            SimpleNamespace(id=uuid4(), meta={"prompt_mode": "test", "report_agent_attempt": 1}),
        )

    monkeypatch.setattr(report_engine, "_run_report_agent", fake_report_agent)
