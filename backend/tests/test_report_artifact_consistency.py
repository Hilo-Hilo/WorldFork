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
from app.domains.jobs.executor import execute_job
from app.llm.audit import LLMCallError
from app.llm.schemas import LLMResponse
from app.domains.report.evidence_pack import build_report_evidence_pack
from app.domains.report import engine as report_engine
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


def test_failed_report_job_rolls_back_completed_report_state(db: Session, monkeypatch):
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

    def fail_report_agent(*args, **kwargs):
        raise LLMCallError("report agent failed")

    monkeypatch.setattr(report_engine, "_run_report_agent", fail_report_agent)

    execute_job(db, job)
    db.commit()
    db.expire_all()

    persisted_report = db.scalar(select(models.Report).where(models.Report.id == report.id))
    persisted_multiverse = db.get(models.Multiverse, multiverse.id)

    assert job.status == "failed"
    assert "report agent failed" in job.error
    assert persisted_report.status == "draft"
    assert persisted_report.current_version == 0
    assert persisted_multiverse.report_status == "not_ready"
    assert db.scalars(select(models.ReportVersion)).all() == []


def test_final_report_inventory_uses_committed_status_and_version(db: Session, monkeypatch):
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

    _install_fake_report_agent(monkeypatch)

    report_version = report_engine.generate_final_big_bang_report(db, big_bang=big_bang)

    body = report_engine.render_report_version_to_markdown(report_version)
    assert report_version.markdown_artifact_id is None
    assert body.startswith("# LLM Report")
    assert "## Structured Evidence Appendix" in body
    assert "- final_big_bang: completed v1" in body
    assert "- multiverse (M1): completed v1" in body
    assert "- final_big_bang: draft v0" not in body
    assert report_version.content["multiverse_comparison"][0]["report_status"] == "completed"
    assert report_version.content["outcome_distribution"]["report_statuses"] == {"completed": 1}


def test_final_report_includes_structured_outcome_conclusions(db: Session, monkeypatch):
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

    _install_fake_report_agent(
        monkeypatch,
        outcome_interpretation="The report interprets the likely endpoint from structured evidence traces.",
    )

    report_version = report_engine.generate_final_big_bang_report(db, big_bang=big_bang)
    conclusions = report_version.content["outcome_conclusions"]
    body = report_engine.render_report_version_to_markdown(report_version)
    assert report_version.markdown_artifact_id is None

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


def test_final_report_weights_endpoints_by_multiverse_path_probability(db: Session, monkeypatch):
    big_bang = models.BigBang(
        name="Weighted endpoints",
        description=None,
        scenario_input={},
        status="completed",
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
        status="completed",
        branch_reason="Root",
        branch_probability=1.0,
        path_probability=0.7,
        state={},
        report_status="completed",
    )
    db.add(root)
    db.flush()
    child = models.Multiverse(
        big_bang_id=big_bang.id,
        parent_multiverse_id=root.id,
        fork_tick_index=4,
        ui_label="M1.1",
        depth=1,
        status="completed",
        branch_reason="Alternative",
        branch_probability=0.3,
        path_probability=0.3,
        state={},
        report_status="completed",
    )
    db.add(child)
    db.flush()
    db.add(
        models.MultiverseLineageEdge(
            big_bang_id=big_bang.id,
            parent_multiverse_id=root.id,
            child_multiverse_id=child.id,
            fork_tick_index=4,
            reason="Weighted branch",
            branch_probability=0.3,
            parent_path_probability=1.0,
            child_path_probability=0.3,
            probability_basis={"source": "test"},
        )
    )
    for multiverse, endpoint_key, label in (
        (root, "settlement", "Settlement"),
        (child, "collapse", "Collapse"),
    ):
        ledger = models.EndpointLedgerVersion(
            big_bang_id=big_bang.id,
            multiverse_id=multiverse.id,
            scope="multiverse",
            version=1,
            status="completed",
            source_type="test",
            created_by="test",
            summary="Test ledger.",
            payload={},
        )
        db.add(ledger)
        db.flush()
        db.add(
            models.EndpointLedgerEntry(
                ledger_version_id=ledger.id,
                endpoint_key=endpoint_key,
                label=label,
                description=None,
                status="realized",
                authority_refs=[],
                evidence_refs=[{"source": "test"}],
                blockers=[],
                contradiction_notes=None,
                rationale="Test endpoint.",
                last_observed_tick_index=None,
                meta={},
            )
        )
    db.flush()
    _install_fake_report_agent(monkeypatch)

    report_version = report_engine.generate_final_big_bang_report(db, big_bang=big_bang)
    histogram = {
        item["endpoint_key"]: item
        for item in report_version.content["endpoint_histogram"]
    }

    assert report_version.content["outcome_distribution"]["endpoint_path_mass_method"] == "path_mass_by_endpoint_status"
    assert histogram["settlement"]["path_mass"] == 0.7
    assert histogram["collapse"]["path_mass"] == 0.3
    plot_rows = {
        item["endpoint_key"]: item
        for item in report_version.content["endpoint_path_mass_distribution"]
    }
    assert plot_rows["settlement"]["status_path_masses"]["realized"] == 0.7
    assert plot_rows["collapse"]["status_path_masses"]["realized"] == 0.3
    assert report_version.content["outcome_conclusions"]["likely_endpoint"]["endpoint_key"] == "settlement"
    root_path, child_path = report_version.content["path_probability_distribution"]
    assert root_path | {
        "multiverse_id": str(root.id),
        "ui_label": "M1",
        "status": "completed",
        "path_probability": 0.7,
        "normalized_weight": 0.7,
        "ledger_version_id": str(
            db.scalar(
                select(models.EndpointLedgerVersion).where(
                    models.EndpointLedgerVersion.multiverse_id == root.id,
                    models.EndpointLedgerVersion.scope == "multiverse",
                )
            ).id
        ),
    } == root_path
    assert child_path | {
        "multiverse_id": str(child.id),
        "ui_label": "M1.1",
        "status": "completed",
        "path_probability": 0.3,
        "normalized_weight": 0.3,
        "ledger_version_id": str(
            db.scalar(
                select(models.EndpointLedgerVersion).where(
                    models.EndpointLedgerVersion.multiverse_id == child.id,
                    models.EndpointLedgerVersion.scope == "multiverse",
                )
            ).id
        ),
    } == child_path
    assert root_path["viability_status"] == "valid"
    assert child_path["viability_status"] == "valid"


def test_final_report_prunes_process_only_timelines_from_effective_path_mass(db: Session, monkeypatch):
    big_bang = models.BigBang(
        name="Adjudicated endpoints",
        description=None,
        scenario_input={},
        status="completed",
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
        status="completed",
        branch_reason="Root",
        branch_probability=1.0,
        path_probability=0.6,
        state={},
        report_status="completed",
    )
    child = models.Multiverse(
        big_bang_id=big_bang.id,
        parent_multiverse_id=root.id,
        fork_tick_index=2,
        ui_label="M1.1",
        depth=1,
        status="completed",
        branch_reason="Audit-only branch",
        branch_probability=0.4,
        path_probability=0.4,
        state={},
        report_status="completed",
    )
    db.add_all([root, child])
    db.flush()
    root_tick = models.TickSnapshot(
        big_bang_id=big_bang.id,
        multiverse_id=root.id,
        tick_index=3,
        ui_label="M1",
        status="final",
        provisional_bundle={},
        final_bundle={"branch_score": 0.1},
        summary="Settlement reached.",
    )
    child_tick = models.TickSnapshot(
        big_bang_id=big_bang.id,
        multiverse_id=child.id,
        tick_index=3,
        ui_label="M1.1",
        status="final",
        provisional_bundle={},
        final_bundle={"branch_score": 0.9},
        summary="Audit process remains unresolved.",
    )
    db.add_all([root_tick, child_tick])
    db.add(
        models.GodAgentReview(
            big_bang_id=big_bang.id,
            multiverse_id=root.id,
            tick_snapshot_id=root_tick.id,
            decision="accept_terminal",
            rationale="The settlement endpoint is retained.",
            confidence=0.8,
            input_summary={},
            output={},
        )
    )
    db.add(
        models.GodAgentReview(
            big_bang_id=big_bang.id,
            multiverse_id=child.id,
            tick_snapshot_id=child_tick.id,
            decision="continue_timeline",
            rationale="The audit branch is process-only.",
            confidence=0.8,
            input_summary={},
            output={},
        )
    )
    db.flush()
    _add_endpoint_ledger(
        db,
        big_bang=big_bang,
        multiverse=root,
        endpoint_key="settlement",
        label="Settlement",
        status="realized",
        probability=None,
        evidence_refs=[{"source": "event", "event_id": "terminal"}],
        status_basis="terminal_event",
    )
    _add_endpoint_ledger(
        db,
        big_bang=big_bang,
        multiverse=child,
        endpoint_key="audit_continues",
        label="Audit continues",
        status="process_only",
        probability=None,
        evidence_refs=[{"source": "log", "kind": "process"}],
        status_basis="process_only",
    )
    _install_fake_report_agent(monkeypatch)

    report_version = report_engine.generate_final_big_bang_report(db, big_bang=big_bang)
    histogram = {item["endpoint_key"]: item for item in report_version.content["endpoint_histogram"]}
    paths = {
        item["ui_label"]: item
        for item in report_version.content["endpoint_ledger"]["payload"]["path_probability_distribution"]
    }

    assert histogram["settlement"]["path_mass"] == 1.0
    assert histogram["audit_continues"]["path_mass"] == 0.0
    assert paths["M1"]["path_probability"] == 0.6
    assert paths["M1"]["normalized_weight"] == 1.0
    assert paths["M1.1"]["path_probability"] == 0.0
    assert paths["M1.1"]["original_path_probability"] == 0.4
    assert paths["M1.1"]["viability_status"] == "process_only"
    assert paths["M1.1"]["include_in_final"] is False
    adjudication = report_version.content["timeline_adjudication"]
    assert adjudication["payload"]["included_path_probability_mass"] == 0.6
    assert adjudication["payload"]["excluded_path_probability_mass"] == 0.4
    assert report_version.content["outcome_conclusions"]["likely_endpoint"]["multiverse_label"] == "M1"
    assert (
        report_version.content["outcome_conclusions"]["likely_endpoint"]["endpoint_selection_basis"]
        == "timeline_adjudication"
    )
    assert "retained M1 as the representative timeline" in (
        report_version.content["outcome_conclusions"]["likely_endpoint"]["interpretation"]
    )
    assert "is the likely endpoint because it ended status=completed" not in (
        report_version.content["outcome_conclusions"]["likely_endpoint"]["interpretation"]
    )
    assert any(
        item.startswith("Timeline adjudication selected M1 ")
        for item in report_version.content["outcome_conclusions"]["causal_mechanisms"]
    )
    assert not any(
        item.startswith("Endpoint selection favored M1.1 ")
        for item in report_version.content["outcome_conclusions"]["causal_mechanisms"]
    )
    prompt_content = report_engine._report_agent_prompt_content(report_version.content, mode="standard")
    assert [item["ui_label"] for item in prompt_content["selected_timelines"]] == ["M1"]
    assert prompt_content["timeline_selection"]["policy"].startswith("timeline_adjudication include_in_final=true")
    assert prompt_content["timeline_adjudication"]["retained_labels"] == ["M1"]
    assert prompt_content["timeline_adjudication"]["pruned_labels"] == ["M1.1"]


def test_report_evidence_pack_is_compact_and_includes_adjudication(db: Session):
    big_bang = models.BigBang(
        name="Evidence pack",
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
        path_probability=1.0,
        state={"cohort_current_states": [{}], "hero_current_states": [{}]},
        report_status="completed",
    )
    db.add(multiverse)
    db.flush()
    db.add(
        models.TickSnapshot(
            big_bang_id=big_bang.id,
            multiverse_id=multiverse.id,
            tick_index=1,
            ui_label="M1",
            status="final",
            provisional_bundle={"large": "x" * 1000},
            final_bundle={"branch_score": 0.5, "large": "y" * 1000},
            summary="Terminal evidence exists.",
        )
    )
    db.flush()
    _add_endpoint_ledger(
        db,
        big_bang=big_bang,
        multiverse=multiverse,
        endpoint_key="settlement",
        label="Settlement",
        status="active",
        probability=1.0,
        evidence_refs=[{"source": "event"}],
        status_basis="terminal_event",
    )
    report_engine.evaluate_timeline_adjudication(
        db,
        big_bang=big_bang,
        source_type="test",
        created_by="test",
    )

    pack = build_report_evidence_pack(db, big_bang=big_bang, mode="summary")
    serialized = str(pack)

    assert pack["schema_version"] == "worldfork.report_evidence_pack.v1"
    assert pack["timeline_adjudication"]["entries"][0]["viability_status"] == "valid"
    assert pack["timelines"][0]["endpoint_ledger"]["entries"][0]["endpoint_key"] == "settlement"
    assert pack["timelines"][0]["latest_ticks"][0]["summary"] == "Terminal evidence exists."
    assert pack["timelines"][0]["actor_state_counts"] == {"cohort_current_states": 1, "hero_current_states": 1}
    assert "provisional_bundle" not in serialized
    assert "final_bundle" not in serialized
    assert "xxxxxxxxxx" not in serialized


def test_report_version_stores_structured_content_and_renders_pdf_ephemerally_on_demand(db: Session, monkeypatch):
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
    _install_fake_report_agent(monkeypatch)

    def fake_pdf(*, title, markdown):
        assert title == report_version.title
        assert "# LLM Report" in markdown
        return b"%PDF-1.4\n"

    monkeypatch.setattr(report_engine, "render_markdown_pdf_bytes", fake_pdf)

    report_version = report_engine.generate_multiverse_report(db, multiverse=multiverse)

    assert report_version.source_multiverse_version == 2
    assert report_version.source_big_bang_config_version == 1
    assert report_version.model
    assert report_version.content["source"]["multiverse_version"] == 2
    assert report_version.content["llm_report"]["report_markdown"].startswith("# LLM Report")
    assert report_version.generation_metadata["storage"]["canonical"] == "report_versions.content"
    assert "ephemeral" in report_version.generation_metadata["storage"]["artifacts"]
    assert report_version.markdown_artifact_id is None
    assert report_version.pdf_artifact_id is None

    pdf_render = report_engine.render_report_version_ephemeral(
        db,
        report_version=report_version,
        output_format="pdf",
    )

    assert pdf_render.content_type == "application/pdf"
    assert pdf_render.body == b"%PDF-1.4\n"
    assert pdf_render.filename.endswith(".pdf")
    assert report_version.pdf_artifact_id is None
    report_artifacts = db.scalars(
        select(models.Artifact).where(models.Artifact.kind.in_(("report_markdown", "report_pdf")))
    ).all()
    assert report_artifacts == []


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

    def fake_complete_with_audit(
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
        calls.append(
            {
                "purpose": purpose,
                "messages": messages,
                "metadata": metadata,
                "json_schema": json_schema,
                "json_response_transform": json_response_transform,
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
            "endpoint_path_mass_method": "path_mass_by_endpoint_status",
            "path_probability_mass": 1.0,
            "endpoint_path_mass_distribution": [
                {"endpoint_key": "settlement", "label": "Settlement", "path_mass": 0.7, "status": "realized"}
            ],
            "total_artifacts": 200,
            "total_llm_calls": 30,
        },
        "endpoint_ledger": {
            "payload": {
                "aggregation": "path_mass_by_endpoint_status",
                "path_probability_mass": 1.0,
                "endpoint_path_mass_distribution": [
                    {"endpoint_key": "settlement", "label": "Settlement", "path_mass": 0.7, "status": "realized"}
                ],
            }
        },
        "path_probability_distribution": [
            {"multiverse_id": "root", "ui_label": "M1", "path_probability": 0.7},
            {"multiverse_id": "branch", "ui_label": "M1.1", "path_probability": 0.3},
        ],
        "multiverse_comparison": [
            {
                "multiverse_id": str(uuid4()),
                "ui_label": f"M{index}",
                "status": "completed",
                "report_status": "completed",
                "branch_probability": index / 100,
                "path_probability": index / 100,
                "latest_branch_score": index / 100,
                "tick_count": index,
                "cohort_state_highlights": [{"cohort_id": f"c{index}", "mood": "watchful"}],
            }
            for index in range(20)
        ],
        "sections": [
            {
                "heading": "Divergence Drivers",
                "items": [
                    {
                        "reason": "forked",
                        "branch_probability": 0.3,
                        "parent_path_probability": 1.0,
                        "child_path_probability": 0.3,
                        "probability_basis": {"source": "god_agent"},
                    }
                ],
            }
        ],
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
    assert standard_payload["selected_timelines"][0]["path_probability"] == 0.19
    assert standard_payload["outcome_distribution"]["endpoint_path_mass_method"] == "path_mass_by_endpoint_status"
    assert standard_payload["probability_context"]["scope"] == "final_big_bang"
    assert standard_payload["probability_context"]["endpoint_path_mass_method"] == "path_mass_by_endpoint_status"
    assert standard_payload["outcome_distribution"]["endpoint_path_mass_distribution"][0]["path_mass"] == 0.7
    assert standard_payload["endpoint_ledger"]["aggregation"] == "path_mass_by_endpoint_status"
    assert standard_payload["path_probability_distribution"][0]["path_probability"] == 0.7
    assert standard_payload["divergence_drivers"][0]["branch_probability"] == 0.3
    assert any("path-mass-weighted" in item for item in standard_payload["quality_controls"])
    assert "total_artifacts" not in rescue_payload["outcome_distribution"]
    assert "total_llm_calls" not in rescue_payload["outcome_distribution"]
    assert "multiverse_id" not in calls[1]["messages"][1]["content"]
    assert "Path-Mass Accounting" in calls[1]["messages"][0]["content"]
    assert calls[1]["json_schema"] == report_engine.REPORT_AGENT_JSON_SCHEMA
    assert calls[1]["json_response_transform"] == report_engine._coerce_report_agent_output


def test_single_multiverse_report_prompt_separates_path_and_endpoint_probability():
    content = {
        "report_type": "multiverse",
        "title": "Single timeline",
        "summary": "One timeline report.",
        "source": {"multiverse_id": str(uuid4()), "ui_label": "M1", "report_version": 1},
        "outcome_distribution": {
            "ui_label": "M1",
            "status": "completed",
            "branch_probability": 0.4,
            "path_probability": 0.18,
            "latest_branch_score": 0.77,
            "cohort_state_highlights": [{"cohort_id": "public", "trust": 0.3}],
        },
        "endpoint_ledger": {
            "entries": [
                {
                    "endpoint_key": "settlement",
                    "label": "Settlement",
                    "status": "active",
                }
            ]
        },
        "endpoint_histogram": [
            {
                "endpoint_key": "settlement",
                "label": "Settlement",
                "status": "active",
            }
        ],
        "sections": [],
    }

    payload = report_engine._report_agent_prompt_content(content, mode="standard")

    assert payload["outcome_distribution"]["branch_probability"] == 0.4
    assert payload["outcome_distribution"]["path_probability"] == 0.18
    assert payload["probability_context"]["scope"] == "single_multiverse"
    assert payload["probability_context"]["branch_probability"] == 0.4
    assert payload["probability_context"]["path_probability"] == 0.18
    assert "terminal-state predicates" in payload["probability_context"]["semantics"]
    assert any("single_multiverse" in item for item in payload["quality_controls"])


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

    def fake_complete_with_audit(
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


def test_report_agent_rejects_deterministic_fallback(db: Session, monkeypatch):
    monkeypatch.setattr(
        report_engine,
        "get_settings",
        lambda: SimpleNamespace(
            default_llm_provider="openrouter",
            openrouter_api_key="test-key",
            report_agent_model="test-report-model",
        ),
    )
    primary = SimpleNamespace(provider="openrouter", model="test-report-model")
    fallback = SimpleNamespace(provider="deterministic", model="deterministic")
    monkeypatch.setattr(
        report_engine,
        "resolve_audited_llm_route",
        lambda *args, **kwargs: SimpleNamespace(
            primary=primary,
            fallback=fallback,
            candidates=lambda: [primary, fallback],
        ),
    )

    with pytest.raises(LLMCallError, match="deterministic report providers are not allowed"):
        report_engine._run_report_agent(
            db,
            big_bang_id=uuid4(),
            content={
                "report_type": "final_big_bang",
                "source": {"big_bang_id": str(uuid4()), "report_version": 1},
                "summary": "Structured final report.",
            },
        )


def test_allocate_report_version_uses_latest_persisted_version(db: Session):
    big_bang = models.BigBang(
        name="Version race",
        description=None,
        scenario_input={},
        status="completed",
        current_config_version=1,
    )
    db.add(big_bang)
    db.flush()
    report = models.Report(
        big_bang_id=big_bang.id,
        multiverse_id=None,
        report_type="final_big_bang",
        status="completed",
        current_version=2,
    )
    db.add(report)
    db.flush()
    db.add(
        models.ReportVersion(
            report_id=report.id,
            version=5,
            title="Existing",
            summary=None,
            content={},
            generation_metadata={},
        )
    )
    db.flush()

    locked_report, previous, version = report_engine._allocate_report_version(db, report_id=report.id)

    assert previous.version == 5
    assert version == 6
    assert locked_report.current_version == 6


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

    def fake_complete_with_audit(
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
        return (
            LLMResponse(
                content="{}",
                parsed={"executive_summary": "invalid payload without report markdown"},
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


def test_report_agent_rejects_fallback_payload_even_with_markdown(db: Session, monkeypatch):
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

    def fake_complete_with_audit(
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
        return (
            LLMResponse(
                content="{}",
                parsed={
                    "fallback": True,
                    "provider": "openrouter",
                    "report_markdown": "# Fallback\n\nThis should not be accepted.",
                    "executive_summary": "Fallback payload",
                },
                raw={},
            ),
            SimpleNamespace(id=uuid4(), meta=metadata),
        )

    monkeypatch.setattr(report_engine, "complete_with_audit", fake_complete_with_audit)

    with pytest.raises(LLMCallError, match="fallback payload"):
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


def test_report_agent_rejects_deterministic_payload_even_with_markdown():
    with pytest.raises(ValueError, match="deterministic provider"):
        report_engine._coerce_report_agent_output(
            {
                "provider": "deterministic",
                "report_markdown": "# Deterministic\n\nThis should not be accepted.",
            }
        )


def test_report_agent_coercion_accepts_minor_shape_drift():
    output = report_engine._coerce_report_agent_output(
        {
            "report_markdown": ["# Report", "Path-Mass Accounting"],
            "executive_summary": ["first", "second"],
            "outcome_interpretation": {"note": "structured"},
            "management_notes": ["watch unresolved endpoints"],
            "risk_notes": None,
            "endpoint_histogram": "endpoint states summarized in prose",
            "terminality_assessment": ["terminal", "insufficient_ticks"],
            "contradiction_check": "none found",
        }
    )

    assert output["report_markdown"] == "# Report\n\nPath-Mass Accounting"
    assert output["executive_summary"] == "first\nsecond"
    assert output["outcome_interpretation"] == '{"note": "structured"}'
    assert output["management_notes"] == "watch unresolved endpoints"
    assert output["risk_notes"] == ""
    assert output["endpoint_histogram"] == [{"summary": "endpoint states summarized in prose"}]
    assert output["terminality_assessment"] == {"items": ["terminal", "insufficient_ticks"]}
    assert output["contradiction_check"] == {"summary": "none found"}


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


def _add_endpoint_ledger(
    db: Session,
    *,
    big_bang: models.BigBang,
    multiverse: models.Multiverse,
    endpoint_key: str,
    label: str,
    status: str,
    probability: float | None,
    evidence_refs: list[dict],
    status_basis: str,
) -> models.EndpointLedgerVersion:
    ledger = models.EndpointLedgerVersion(
        big_bang_id=big_bang.id,
        multiverse_id=multiverse.id,
        scope="multiverse",
        version=1,
        status="completed",
        source_type="test",
        created_by="test",
        summary="Test ledger.",
        payload={},
    )
    db.add(ledger)
    db.flush()
    db.add(
        models.EndpointLedgerEntry(
            ledger_version_id=ledger.id,
            endpoint_key=endpoint_key,
            label=label,
            description=None,
            status=status,
            probability=probability,
            realization_criteria=[f"{label} is observed."],
            authority_refs=[],
            evidence_refs=evidence_refs,
            negative_evidence_refs=[],
            blockers=[],
            status_basis=status_basis,
            contradiction_notes=None,
            rationale="Test endpoint.",
            last_observed_tick_index=None,
            meta={},
        )
    )
    db.flush()
    return ledger


def _install_fake_report_agent(monkeypatch, *, outcome_interpretation: str = "Interprets structured metrics."):
    def fake_report_agent(db, *, big_bang_id, content):
        return (
            {
                "report_markdown": (
                    "# LLM Report\n\n"
                    "## Executive Summary\n\n"
                    "This is a generated long-form report body supplied by the test LLM agent.\n\n"
                    "## Outcome Interpretation\n\n"
                    "The report interprets the structured WorldFork metrics without using a non-LLM fallback."
                ),
                "executive_summary": "Generated by test LLM agent.",
                "outcome_interpretation": outcome_interpretation,
                "management_notes": "Review evidence appendix.",
                "risk_notes": "Test double only.",
            },
            SimpleNamespace(id=uuid4(), meta={"prompt_mode": "test", "report_agent_attempt": 1}),
        )

    monkeypatch.setattr(report_engine, "_run_report_agent", fake_report_agent)
