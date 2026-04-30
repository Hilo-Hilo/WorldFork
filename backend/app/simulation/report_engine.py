from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models
from app.llm.audit import LLMCallError, complete_with_audit
from app.llm.prompt_templates import REPORT_AGENT_SYSTEM_PROMPT
from app.simulation.runtime_config import multiverse_runtime_config_version
from app.storage.artifact_store import ArtifactStore, _cleanup_artifact_path
from app.storage.pdf_store import render_markdown_pdf

REPORT_SCHEMA_VERSION = "worldfork.report.v2"


def _cleanup_report_artifacts(*artifacts: models.Artifact | None) -> None:
    for artifact in artifacts:
        if artifact is not None:
            _cleanup_artifact_path(Path(artifact.path))


def generate_multiverse_report(
    db: Session,
    *,
    multiverse: models.Multiverse,
    title: str | None = None,
    summary: str | None = None,
) -> models.ReportVersion:
    db.execute(select(models.Multiverse).where(models.Multiverse.id == multiverse.id).with_for_update()).scalar_one()
    report = _get_or_create_report(
        db,
        big_bang_id=multiverse.big_bang_id,
        multiverse_id=multiverse.id,
        report_type="multiverse",
    )
    previous_version = _latest_report_version(db, report.id)
    version = report.current_version + 1
    latest_tick = _latest_tick(db, multiverse.id)
    title_text = title or f"Multiverse {multiverse.ui_label} Report"
    content = _build_multiverse_report_content(
        db,
        multiverse=multiverse,
        title=title_text,
        summary=summary,
        report_version_number=version,
        latest_tick=latest_tick,
    )
    metadata = _base_generation_metadata(
        report_type="multiverse",
        big_bang_id=multiverse.big_bang_id,
        model=get_settings().report_agent_model,
        source={"multiverse_id": str(multiverse.id), "multiverse_version": multiverse.version},
    )
    ai_summary, llm_call = _run_report_agent(db, big_bang_id=multiverse.big_bang_id, content=content)
    content["ai_summary"] = ai_summary
    if llm_call is not None:
        metadata["llm_call_id"] = str(llm_call.id)
        metadata["report_agent_status"] = "succeeded"
    else:
        metadata.setdefault("report_agent_status", "skipped_or_fallback")
    _refresh_report_counts(db, big_bang_id=multiverse.big_bang_id, content=content)

    report.current_version = version
    report.status = "completed"
    multiverse.report_status = "completed"
    report_version = models.ReportVersion(
        report_id=report.id,
        version=version,
        title=title_text,
        summary=summary,
        source_multiverse_version=multiverse.version,
        source_big_bang_config_version=multiverse_runtime_config_version(db, multiverse),
        source_tick_snapshot_id=latest_tick.id if latest_tick else None,
        source_tick_index=latest_tick.tick_index if latest_tick else None,
        source_multiverse_ids=[str(multiverse.id)],
        content=content,
        generation_metadata=metadata,
        model=get_settings().report_agent_model,
        supersedes_report_version_id=previous_version.id if previous_version else None,
    )
    db.add(report_version)
    db.flush()
    artifact = None
    try:
        artifact = _write_markdown_artifact(
            db,
            report_version=report_version,
            relative_path=(
                f"big_bang_{multiverse.big_bang_id}/multiverses/{multiverse.ui_label}/"
                f"reports/{report_version.id}/report_v{version}.md"
            ),
        )
        report_version.markdown_artifact_id = artifact.id
        db.flush()
    except Exception:
        _cleanup_report_artifacts(artifact)
        raise
    return report_version


def generate_final_big_bang_report(
    db: Session,
    *,
    big_bang: models.BigBang,
    title: str | None = None,
    summary: str | None = None,
) -> models.ReportVersion:
    db.execute(select(models.BigBang).where(models.BigBang.id == big_bang.id).with_for_update()).scalar_one()
    report = _get_or_create_report(
        db,
        big_bang_id=big_bang.id,
        multiverse_id=None,
        report_type="final_big_bang",
    )
    previous_version = _latest_report_version(db, report.id)
    version = report.current_version + 1
    multiverses = db.scalars(
        select(models.Multiverse)
        .where(models.Multiverse.big_bang_id == big_bang.id)
        .order_by(models.Multiverse.ui_label)
    ).all()
    title_text = title or f"{big_bang.name} Final Big Bang Report"
    content = _build_final_report_content(
        db,
        big_bang=big_bang,
        multiverses=multiverses,
        title=title_text,
        summary=summary,
        report_version_number=version,
    )
    metadata = _base_generation_metadata(
        report_type="final_big_bang",
        big_bang_id=big_bang.id,
        model=get_settings().report_agent_model,
        source={
            "multiverse_versions": {
                str(item.id): {"label": item.ui_label, "version": item.version}
                for item in multiverses
            }
        },
    )
    ai_summary, llm_call = _run_report_agent(db, big_bang_id=big_bang.id, content=content)
    content["ai_summary"] = ai_summary
    if llm_call is not None:
        metadata["llm_call_id"] = str(llm_call.id)
        metadata["report_agent_status"] = "succeeded"
    else:
        metadata.setdefault("report_agent_status", "skipped_or_fallback")
    _refresh_report_counts(db, big_bang_id=big_bang.id, content=content)

    report.current_version = version
    report.status = "completed"
    reports = db.scalars(select(models.Report).where(models.Report.big_bang_id == big_bang.id)).all()
    label_by_multiverse_id = {str(item.id): item.ui_label for item in multiverses}
    content["report_inventory"] = [
        {
            "report_id": str(item.id),
            "report_type": item.report_type,
            "status": item.status,
            "current_version": item.current_version,
            "multiverse_id": str(item.multiverse_id) if item.multiverse_id else None,
            "multiverse_label": label_by_multiverse_id.get(str(item.multiverse_id)) if item.multiverse_id else None,
        }
        for item in sorted(reports, key=lambda item: (item.report_type, str(item.multiverse_id or "")))
    ]
    report_version = models.ReportVersion(
        report_id=report.id,
        version=version,
        title=title_text,
        summary=summary,
        source_multiverse_version=None,
        source_big_bang_config_version=big_bang.current_config_version,
        source_tick_snapshot_id=None,
        source_tick_index=max(
            [item.get("latest_tick_index") or 0 for item in content.get("multiverse_comparison", [])] or [0]
        ),
        source_multiverse_ids=[str(item.id) for item in multiverses],
        content=content,
        generation_metadata=metadata,
        model=get_settings().report_agent_model,
        supersedes_report_version_id=previous_version.id if previous_version else None,
    )
    db.add(report_version)
    db.flush()
    artifact = None
    try:
        artifact = _write_markdown_artifact(
            db,
            report_version=report_version,
            relative_path=f"big_bang_{big_bang.id}/reports/{report_version.id}/final_big_bang_report_v{version}.md",
        )
        report_version.markdown_artifact_id = artifact.id
        db.flush()
    except Exception:
        _cleanup_report_artifacts(artifact)
        raise
    return report_version


def render_report_version_to_markdown(report_version: models.ReportVersion) -> str:
    content = report_version.content or {}
    if not content:
        return f"# {report_version.title}\n\n{report_version.summary or ''}\n"
    return render_structured_report_markdown(content)


def render_report_version_artifact(
    db: Session,
    *,
    report_version: models.ReportVersion,
    output_format: str,
    force: bool = False,
) -> models.Artifact:
    output_format = output_format.lower()
    if output_format not in {"markdown", "pdf"}:
        raise ValueError("output_format must be markdown or pdf")
    if output_format == "markdown" and report_version.markdown_artifact_id and not force:
        artifact = db.get(models.Artifact, report_version.markdown_artifact_id)
        if artifact is not None:
            return artifact
    if output_format == "pdf" and report_version.pdf_artifact_id and not force:
        artifact = db.get(models.Artifact, report_version.pdf_artifact_id)
        if artifact is not None:
            return artifact

    markdown = render_report_version_to_markdown(report_version)
    report = db.get(models.Report, report_version.report_id)
    if report is None:
        raise ValueError("report not found")
    if output_format == "markdown":
        artifact = ArtifactStore().write_text(
            db,
            big_bang_id=report.big_bang_id,
            relative_path=f"big_bang_{report.big_bang_id}/reports/{report_version.id}/rendered_report_v{report_version.version}.md",
            body=markdown,
            kind="report_markdown",
            content_type="text/markdown",
        )
        report_version.markdown_artifact_id = artifact.id
        db.flush()
        return artifact

    artifact = render_markdown_pdf(
        db,
        big_bang_id=report.big_bang_id,
        relative_path=f"big_bang_{report.big_bang_id}/reports/{report_version.id}/rendered_report_v{report_version.version}.pdf",
        title=report_version.title,
        markdown=markdown,
    )
    report_version.pdf_artifact_id = artifact.id
    db.flush()
    return artifact


def render_structured_report_markdown(content: dict[str, Any]) -> str:
    lines: list[str] = [f"# {content.get('title') or 'WorldFork Report'}", ""]
    if content.get("summary"):
        lines.extend([str(content["summary"]), ""])
    ai_summary = content.get("ai_summary") or {}
    if ai_summary:
        lines.extend(["## AI Outcome Summary", ""])
        for key in ("executive_summary", "outcome_interpretation", "management_notes", "risk_notes"):
            value = ai_summary.get(key)
            if value:
                lines.extend([f"### {key.replace('_', ' ').title()}", "", _markdown_value(value), ""])
    if content.get("outcome_conclusions"):
        lines.extend(["## Outcome Conclusions", ""])
        conclusions = content["outcome_conclusions"]
        likely_endpoint = conclusions.get("likely_endpoint") or {}
        if likely_endpoint:
            lines.extend(["### Likely Endpoint", ""])
            lines.extend(_markdown_block(likely_endpoint))
            lines.append("")
        if conclusions.get("causal_mechanisms"):
            lines.extend(["### Causal Mechanisms", ""])
            lines.extend(_markdown_list(conclusions["causal_mechanisms"]))
            lines.append("")
        if conclusions.get("key_event_traces"):
            lines.extend(["### Key Event Traces", ""])
            lines.extend(_markdown_list(conclusions["key_event_traces"]))
            lines.append("")
        if conclusions.get("god_review_trace"):
            lines.extend(["### God Review Trace", ""])
            lines.extend(_markdown_list(conclusions["god_review_trace"]))
            lines.append("")
    for section in content.get("sections", []):
        heading = section.get("heading")
        if heading:
            lines.extend([f"## {heading}", ""])
        if section.get("body"):
            lines.extend([str(section["body"]), ""])
        if section.get("items"):
            lines.extend(_markdown_list(section["items"]))
            lines.append("")
        if section.get("table"):
            lines.extend(_markdown_table(section["table"]))
            lines.append("")
    if content.get("outcome_distribution"):
        lines.extend(["## Outcome Distribution", ""])
        lines.extend(_markdown_block(content["outcome_distribution"]))
        lines.append("")
    if content.get("multiverse_comparison"):
        lines.extend(["## Multiverse Comparison", ""])
        lines.extend(
            _markdown_table(
                [
                    {
                        "label": item.get("ui_label"),
                        "version": item.get("version"),
                        "status": item.get("status"),
                        "ticks": item.get("tick_count"),
                        "branch_score": item.get("latest_branch_score"),
                        "social_posts": item.get("social_posts"),
                        "graph_edges": item.get("graph_edges"),
                        "sociology_signals": item.get("sociology_signals"),
                    }
                    for item in content["multiverse_comparison"]
                ]
            )
        )
        lines.append("")
    if content.get("report_inventory"):
        lines.extend(["## Report Inventory", ""])
        lines.extend(_markdown_list([_report_inventory_line(item) for item in content["report_inventory"]]))
        lines.append("")
    if content.get("evidence_appendix"):
        lines.extend(["## Evidence Appendix", ""])
        lines.extend(_markdown_block(content["evidence_appendix"]))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _get_or_create_report(
    db: Session,
    *,
    big_bang_id,
    multiverse_id,
    report_type: str,
) -> models.Report:
    query = select(models.Report).where(
        models.Report.big_bang_id == big_bang_id,
        models.Report.report_type == report_type,
    )
    if multiverse_id is None:
        query = query.where(models.Report.multiverse_id.is_(None))
    else:
        query = query.where(models.Report.multiverse_id == multiverse_id)
    report = db.scalar(query.with_for_update())
    if report is None:
        report = models.Report(
            big_bang_id=big_bang_id,
            multiverse_id=multiverse_id,
            report_type=report_type,
            status="draft",
            current_version=0,
        )
        db.add(report)
        db.flush()
    return report


def _latest_report_version(db: Session, report_id) -> models.ReportVersion | None:
    return db.scalar(
        select(models.ReportVersion)
        .where(models.ReportVersion.report_id == report_id)
        .order_by(models.ReportVersion.version.desc())
        .limit(1)
    )


def _latest_tick(db: Session, multiverse_id) -> models.TickSnapshot | None:
    return db.scalar(
        select(models.TickSnapshot)
        .where(models.TickSnapshot.multiverse_id == multiverse_id)
        .order_by(models.TickSnapshot.tick_index.desc())
        .limit(1)
    )


def _big_bang_config_version(db: Session, big_bang_id) -> int | None:
    value = db.scalar(
        select(func.max(models.BigBangConfig.version)).where(models.BigBangConfig.big_bang_id == big_bang_id)
    )
    return int(value) if value is not None else None


def _build_multiverse_report_content(
    db: Session,
    *,
    multiverse: models.Multiverse,
    title: str,
    summary: str | None,
    report_version_number: int,
    latest_tick: models.TickSnapshot | None,
) -> dict[str, Any]:
    ticks = db.scalars(
        select(models.TickSnapshot)
        .where(models.TickSnapshot.multiverse_id == multiverse.id)
        .order_by(models.TickSnapshot.tick_index)
    ).all()
    god_reviews = db.scalars(
        select(models.GodAgentReview)
        .where(models.GodAgentReview.multiverse_id == multiverse.id)
        .order_by(models.GodAgentReview.created_at)
    ).all()
    metrics = _multiverse_metrics(db, multiverse, latest_tick=latest_tick)
    timeline = [
        {
            "tick_id": str(tick.id),
            "tick_index": tick.tick_index,
            "label": tick.ui_label,
            "status": tick.status,
            "summary": tick.summary,
            "branch_score": (tick.final_bundle or {}).get("branch_score"),
            "god_decision": ((tick.final_bundle or {}).get("god_review") or {}).get("decision"),
        }
        for tick in ticks
    ]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_type": "multiverse",
        "title": title,
        "summary": summary or f"Structured report for {multiverse.ui_label} v{multiverse.version}.",
        "source": {
            "big_bang_id": str(multiverse.big_bang_id),
            "multiverse_id": str(multiverse.id),
            "ui_label": multiverse.ui_label,
            "multiverse_version": multiverse.version,
            "status": multiverse.status,
            "report_version": report_version_number,
            "source_tick_snapshot_id": str(latest_tick.id) if latest_tick else None,
            "source_tick_index": latest_tick.tick_index if latest_tick else None,
            "config_version": multiverse_runtime_config_version(db, multiverse),
        },
        "outcome_distribution": metrics,
        "sections": [
            {
                "heading": "Condensed Outcome",
                "body": _condensed_multiverse_outcome(multiverse, metrics),
            },
            {
                "heading": "Timeline",
                "table": timeline,
            },
            {
                "heading": "God Agent Decisions",
                "items": [
                    {
                        "tick_snapshot_id": str(review.tick_snapshot_id) if review.tick_snapshot_id else None,
                        "decision": review.decision,
                        "confidence": review.confidence,
                        "rationale": review.rationale,
                    }
                    for review in god_reviews
                ],
            },
            {
                "heading": "Cohort and Hero Changes",
                "items": _state_change_items(latest_tick),
            },
        ],
        "evidence_appendix": {
            "multiverse_id": str(multiverse.id),
            "latest_tick_id": str(latest_tick.id) if latest_tick else None,
            "artifact_counts": _artifact_counts(db, multiverse.big_bang_id),
        },
    }


def _build_final_report_content(
    db: Session,
    *,
    big_bang: models.BigBang,
    multiverses: list[models.Multiverse],
    title: str,
    summary: str | None,
    report_version_number: int,
) -> dict[str, Any]:
    comparison = [
        _multiverse_metrics(db, multiverse, latest_tick=_latest_tick(db, multiverse.id))
        for multiverse in multiverses
    ]
    lineage_edges = db.scalars(
        select(models.MultiverseLineageEdge)
        .where(models.MultiverseLineageEdge.big_bang_id == big_bang.id)
        .order_by(models.MultiverseLineageEdge.created_at)
    ).all()
    god_decisions = Counter()
    for item in comparison:
        god_decisions.update(item.get("god_decisions", {}))
    outcome_conclusions = _final_outcome_conclusions(
        db,
        big_bang=big_bang,
        multiverses=multiverses,
        comparison=comparison,
        lineage_edges=lineage_edges,
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_type": "final_big_bang",
        "title": title,
        "summary": summary or f"Structured final report across {len(multiverses)} multiverse timelines.",
        "source": {
            "big_bang_id": str(big_bang.id),
            "big_bang_status": big_bang.status,
            "config_version": big_bang.current_config_version,
            "report_version": report_version_number,
        },
        "multiverse_comparison": comparison,
        "outcome_distribution": {
            "timeline_statuses": dict(Counter(item.status for item in multiverses)),
            "report_statuses": dict(Counter(item.report_status for item in multiverses)),
            "god_decisions": dict(god_decisions),
            "total_social_posts": sum(item.get("social_posts", 0) for item in comparison),
            "total_graph_edges": sum(item.get("graph_edges", 0) for item in comparison),
            "total_sociology_signals": sum(item.get("sociology_signals", 0) for item in comparison),
            "total_llm_calls": _count(db, models.LLMCall, big_bang_id=big_bang.id),
            "total_artifacts": _count(db, models.Artifact, big_bang_id=big_bang.id),
        },
        "outcome_conclusions": outcome_conclusions,
        "sections": [
            {
                "heading": "Condensed Outcome",
                "body": _condensed_final_outcome(comparison),
            },
            {
                "heading": "Divergence Drivers",
                "items": [
                    {
                        "parent_multiverse_id": str(edge.parent_multiverse_id),
                        "child_multiverse_id": str(edge.child_multiverse_id),
                        "fork_tick_index": edge.fork_tick_index,
                        "reason": edge.reason,
                    }
                    for edge in lineage_edges
                ],
            },
            {
                "heading": "Recurring Patterns",
                "items": _recurring_patterns(comparison),
            },
        ],
        "evidence_appendix": {
            "big_bang_id": str(big_bang.id),
            "source_multiverse_ids": [str(item.id) for item in multiverses],
            "artifact_counts": _artifact_counts(db, big_bang.id),
        },
    }


def _final_outcome_conclusions(
    db: Session,
    *,
    big_bang: models.BigBang,
    multiverses: list[models.Multiverse],
    comparison: list[dict[str, Any]],
    lineage_edges: list[models.MultiverseLineageEdge],
) -> dict[str, Any]:
    if not multiverses:
        return {
            "likely_endpoint": {
                "status": big_bang.status,
                "interpretation": "No multiverse timelines exist yet, so no simulated endpoint can be derived.",
            },
            "causal_mechanisms": ["No event, review, or lineage data is available yet."],
            "key_event_traces": [],
            "god_review_trace": [],
        }

    endpoint_source = _select_endpoint_source(comparison)
    endpoint_multiverse = next(
        (item for item in multiverses if str(item.id) == endpoint_source.get("multiverse_id")),
        None,
    )
    latest_tick = _latest_tick(db, endpoint_multiverse.id) if endpoint_multiverse is not None else None
    latest_review = _latest_god_review(db, endpoint_multiverse.id) if endpoint_multiverse is not None else None
    event_traces = _final_event_traces(db, big_bang_id=big_bang.id)
    review_trace = _final_god_review_trace(db, big_bang_id=big_bang.id)
    endpoint = {
        "multiverse_label": endpoint_source.get("ui_label"),
        "multiverse_status": endpoint_source.get("status"),
        "latest_tick_index": endpoint_source.get("latest_tick_index"),
        "latest_tick_status": latest_tick.status if latest_tick else None,
        "latest_tick_summary": latest_tick.summary if latest_tick else None,
        "branch_score": endpoint_source.get("latest_branch_score"),
        "god_decision": latest_review.decision if latest_review else None,
        "god_rationale": latest_review.rationale if latest_review else None,
        "interpretation": _endpoint_interpretation(endpoint_source, latest_tick, latest_review),
    }
    return {
        "likely_endpoint": endpoint,
        "causal_mechanisms": _final_causal_mechanisms(
            endpoint_source,
            event_traces=event_traces,
            review_trace=review_trace,
            lineage_edges=lineage_edges,
        ),
        "key_event_traces": event_traces,
        "god_review_trace": review_trace,
    }


def _select_endpoint_source(comparison: list[dict[str, Any]]) -> dict[str, Any]:
    terminal_statuses = {"completed", "complete", "terminal", "ended", "final"}

    def key(item: dict[str, Any]) -> tuple[int, float, int, str]:
        status_rank = 1 if str(item.get("status") or "").lower() in terminal_statuses else 0
        score = item.get("latest_branch_score")
        branch_score = float(score) if isinstance(score, (int, float)) else 0.0
        tick_index = int(item.get("latest_tick_index") or 0)
        return (status_rank, branch_score, tick_index, str(item.get("ui_label") or ""))

    return max(comparison, key=key) if comparison else {}


def _endpoint_interpretation(
    endpoint_source: dict[str, Any],
    latest_tick: models.TickSnapshot | None,
    latest_review: models.GodAgentReview | None,
) -> str:
    label = endpoint_source.get("ui_label") or "The selected timeline"
    status = endpoint_source.get("status") or "unknown"
    tick_index = endpoint_source.get("latest_tick_index")
    if latest_review is not None:
        return (
            f"{label} is the likely endpoint because it ended status={status} at tick {tick_index} "
            f"and the latest God-agent review decided {latest_review.decision}: {latest_review.rationale}"
        )
    if latest_tick is not None and latest_tick.summary:
        return f"{label} is the likely endpoint because it ended status={status} at tick {tick_index}: {latest_tick.summary}"
    return f"{label} is the likely endpoint by terminal status, branch score, and latest tick position."


def _final_event_traces(db: Session, *, big_bang_id) -> list[dict[str, Any]]:
    rows = db.execute(
        select(models.Event, models.EventSummary, models.TickSnapshot)
        .join(models.EventSummary, models.EventSummary.event_id == models.Event.id)
        .outerjoin(models.TickSnapshot, models.TickSnapshot.id == models.EventSummary.tick_snapshot_id)
        .where(models.Event.big_bang_id == big_bang_id)
        .order_by(models.Event.scheduled_tick.desc(), models.EventSummary.version.desc(), models.Event.created_at.desc())
        .limit(8)
    ).all()
    traces = []
    seen_events = set()
    for event, summary, tick in rows:
        if event.id in seen_events:
            continue
        seen_events.add(event.id)
        traces.append(
            {
                "multiverse_id": str(event.multiverse_id),
                "tick_index": tick.tick_index if tick else event.scheduled_tick,
                "event_title": event.title,
                "event_status": event.status,
                "event_type": event.event_type,
                "summary": summary.summary,
                "expected_impact": event.expected_impact or {},
                "actual_impact": event.actual_impact or {},
            }
        )
    if traces:
        return traces
    fallback_events = db.scalars(
        select(models.Event)
        .where(models.Event.big_bang_id == big_bang_id)
        .order_by(models.Event.scheduled_tick.desc(), models.Event.created_at.desc())
        .limit(5)
    ).all()
    return [
        {
            "multiverse_id": str(event.multiverse_id),
            "tick_index": event.scheduled_tick,
            "event_title": event.title,
            "event_status": event.status,
            "event_type": event.event_type,
            "summary": event.description,
            "expected_impact": event.expected_impact or {},
            "actual_impact": event.actual_impact or {},
        }
        for event in fallback_events
    ]


def _latest_god_review(db: Session, multiverse_id) -> models.GodAgentReview | None:
    return db.scalar(
        select(models.GodAgentReview)
        .where(models.GodAgentReview.multiverse_id == multiverse_id)
        .order_by(models.GodAgentReview.created_at.desc(), models.GodAgentReview.id.desc())
        .limit(1)
    )


def _final_god_review_trace(db: Session, *, big_bang_id) -> list[dict[str, Any]]:
    rows = db.execute(
        select(models.GodAgentReview, models.TickSnapshot)
        .outerjoin(models.TickSnapshot, models.TickSnapshot.id == models.GodAgentReview.tick_snapshot_id)
        .where(models.GodAgentReview.big_bang_id == big_bang_id)
        .order_by(models.GodAgentReview.created_at.desc(), models.GodAgentReview.id.desc())
        .limit(8)
    ).all()
    return [
        {
            "multiverse_id": str(review.multiverse_id),
            "tick_index": tick.tick_index if tick else None,
            "decision": review.decision,
            "confidence": review.confidence,
            "rationale": review.rationale,
        }
        for review, tick in rows
    ]


def _final_causal_mechanisms(
    endpoint_source: dict[str, Any],
    *,
    event_traces: list[dict[str, Any]],
    review_trace: list[dict[str, Any]],
    lineage_edges: list[models.MultiverseLineageEdge],
) -> list[str]:
    mechanisms: list[str] = []
    if event_traces:
        event = event_traces[0]
        mechanisms.append(
            f"Latest summarized event pressure: {event.get('event_title')} at tick {event.get('tick_index')} "
            f"ended {event.get('event_status')} and was summarized as: {event.get('summary')}"
        )
    if review_trace:
        review = review_trace[0]
        mechanisms.append(
            f"God-agent gate: decision={review.get('decision')} at tick {review.get('tick_index')} "
            f"because {review.get('rationale')}"
        )
    if lineage_edges:
        edge = lineage_edges[-1]
        mechanisms.append(
            f"Lineage divergence: fork at tick {edge.fork_tick_index} carried reason: {edge.reason or 'unspecified'}"
        )
    mechanisms.append(
        f"Endpoint selection favored {endpoint_source.get('ui_label')} by status={endpoint_source.get('status')}, "
        f"latest tick={endpoint_source.get('latest_tick_index')}, and branch score={endpoint_source.get('latest_branch_score')}."
    )
    return mechanisms


def _multiverse_metrics(
    db: Session,
    multiverse: models.Multiverse,
    *,
    latest_tick: models.TickSnapshot | None,
) -> dict[str, Any]:
    latest_final = latest_tick.final_bundle if latest_tick and latest_tick.final_bundle else {}
    god_decisions = Counter(
        db.scalars(
            select(models.GodAgentReview.decision).where(models.GodAgentReview.multiverse_id == multiverse.id)
        ).all()
    )
    tool_calls = Counter(
        db.scalars(select(models.ToolCall.tool_name).where(models.ToolCall.multiverse_id == multiverse.id)).all()
    )
    signal_types = Counter(
        (signal.signal or {}).get("signal_type") or (signal.signal or {}).get("type") or "unknown"
        for signal in db.scalars(
            select(models.SociologySignal).where(models.SociologySignal.multiverse_id == multiverse.id)
        ).all()
    )
    return {
        "multiverse_id": str(multiverse.id),
        "ui_label": multiverse.ui_label,
        "version": multiverse.version,
        "status": multiverse.status,
        "report_status": multiverse.report_status,
        "depth": multiverse.depth,
        "parent_multiverse_id": str(multiverse.parent_multiverse_id) if multiverse.parent_multiverse_id else None,
        "fork_tick_index": multiverse.fork_tick_index,
        "config_version": multiverse_runtime_config_version(db, multiverse),
        "latest_tick_id": str(latest_tick.id) if latest_tick else None,
        "latest_tick_index": latest_tick.tick_index if latest_tick else None,
        "latest_branch_score": latest_final.get("branch_score"),
        "tick_count": _count(db, models.TickSnapshot, multiverse_id=multiverse.id),
        "social_posts": _count(db, models.SocialPost, multiverse_id=multiverse.id),
        "graph_edges": _count(db, models.GraphEdge, multiverse_id=multiverse.id),
        "sociology_signals": _count(db, models.SociologySignal, multiverse_id=multiverse.id),
        "events_executed": _count(db, models.Event, multiverse_id=multiverse.id, status="executed"),
        "god_decisions": dict(god_decisions),
        "tool_calls": dict(tool_calls),
        "sociology_signal_types": dict(signal_types),
        "idle_streak": (multiverse.state or {}).get("idle_streak"),
        "last_sociology_keys": sorted(((multiverse.state or {}).get("last_sociology") or {}).keys())[:20],
    }


def _count(db: Session, model, **filters) -> int:
    query = select(func.count()).select_from(model)
    for key, value in filters.items():
        query = query.where(getattr(model, key) == value)
    return int(db.scalar(query) or 0)


def _artifact_counts(db: Session, big_bang_id) -> dict[str, int]:
    rows = db.execute(
        select(models.Artifact.kind, func.count(models.Artifact.id))
        .where(models.Artifact.big_bang_id == big_bang_id)
        .group_by(models.Artifact.kind)
    ).all()
    return {str(kind): int(count) for kind, count in rows}


def _refresh_report_counts(db: Session, *, big_bang_id, content: dict[str, Any]) -> None:
    distribution = content.setdefault("outcome_distribution", {})
    if isinstance(distribution, dict):
        distribution["total_llm_calls"] = _count(db, models.LLMCall, big_bang_id=big_bang_id)
        distribution["total_artifacts"] = _count(db, models.Artifact, big_bang_id=big_bang_id)
    evidence = content.setdefault("evidence_appendix", {})
    if isinstance(evidence, dict):
        evidence["artifact_counts"] = _artifact_counts(db, big_bang_id)


def _report_inventory_line(item: dict[str, Any]) -> str:
    label = f" ({item['multiverse_label']})" if item.get("multiverse_label") else ""
    return f"{item['report_type']}{label}: {item['status']} v{item['current_version']}"


def _state_change_items(latest_tick: models.TickSnapshot | None) -> list[dict[str, Any]]:
    if latest_tick is None:
        return []
    final = latest_tick.final_bundle or {}
    sociology = final.get("sociology_result") or {}
    return [
        {
            "kind": "cohort_state_updates",
            "count": len(sociology.get("cohort_state_updates") or []),
            "sample": (sociology.get("cohort_state_updates") or [])[:5],
        },
        {
            "kind": "hero_state_updates",
            "count": len(sociology.get("hero_state_updates") or []),
            "sample": (sociology.get("hero_state_updates") or [])[:5],
        },
    ]


def _condensed_multiverse_outcome(multiverse: models.Multiverse, metrics: dict[str, Any]) -> str:
    return (
        f"{multiverse.ui_label} v{multiverse.version} ended with status={multiverse.status}, "
        f"{metrics.get('tick_count', 0)} tick snapshots, {metrics.get('social_posts', 0)} social posts, "
        f"{metrics.get('graph_edges', 0)} graph edges, and latest branch score "
        f"{metrics.get('latest_branch_score')}."
    )


def _condensed_final_outcome(comparison: list[dict[str, Any]]) -> str:
    if not comparison:
        return "No multiverses were available for comparison."
    highest = max(comparison, key=lambda item: item.get("latest_branch_score") or 0)
    if len(comparison) == 1:
        return (
            "The Big Bang currently has one multiverse timeline. "
            f"{highest.get('ui_label')} ended status={highest.get('status')} at tick "
            f"{highest.get('latest_tick_index')} with latest branch score "
            f"{highest.get('latest_branch_score')}; use Outcome Conclusions for the endpoint trace."
        )
    return (
        f"The Big Bang currently compares {len(comparison)} multiverse timelines. "
        f"{highest.get('ui_label')} has the highest latest branch score "
        f"({highest.get('latest_branch_score')}) among compared timelines; use Outcome Conclusions "
        "to distinguish terminal endpoints from process states."
    )


def _recurring_patterns(comparison: list[dict[str, Any]]) -> list[str]:
    total_posts = sum(item.get("social_posts", 0) for item in comparison)
    total_signals = sum(item.get("sociology_signals", 0) for item in comparison)
    total_edges = sum(item.get("graph_edges", 0) for item in comparison)
    return [
        f"Public feedback loop volume: {total_posts} social posts across compared timelines.",
        f"Sociology pressure volume: {total_signals} persisted signals.",
        f"Graph movement volume: {total_edges} graph edges.",
    ]


def _run_report_agent(
    db: Session,
    *,
    big_bang_id,
    content: dict[str, Any],
) -> tuple[dict[str, Any], models.LLMCall | None]:
    settings = get_settings()
    if "PYTEST_CURRENT_TEST" in os.environ:
        return _deterministic_ai_summary(content), None
    if settings.default_llm_provider == "openrouter" and not settings.openrouter_api_key:
        return _deterministic_ai_summary(content), None
    source = content.get("source") or {}
    source_id = source.get("multiverse_id") or source.get("big_bang_id") or str(big_bang_id)
    try:
        response, call = complete_with_audit(
            db,
            big_bang_id=big_bang_id,
            purpose=f"report_agent_{content['report_type']}_{source_id}_{source.get('report_version')}",
            model=settings.report_agent_model,
            messages=[
                {
                    "role": "system",
                    "content": REPORT_AGENT_SYSTEM_PROMPT,
                },
                {"role": "user", "content": f"Structured report metrics: {content}"},
            ],
            metadata={"max_tokens": 900, "temperature": 0.2, "agent_type": "report_agent"},
        )
        parsed = response.parsed if isinstance(response.parsed, dict) else {}
        return {
            "executive_summary": parsed.get("executive_summary") or "Report agent generated a summary.",
            "outcome_interpretation": parsed.get("outcome_interpretation") or "",
            "management_notes": parsed.get("management_notes") or "",
            "risk_notes": parsed.get("risk_notes") or "",
        }, call
    except LLMCallError as exc:
        summary = _deterministic_ai_summary(content)
        summary["risk_notes"] = f"Report agent fallback used after LLM error: {exc}"
        return summary, None


def _deterministic_ai_summary(content: dict[str, Any]) -> dict[str, Any]:
    if content.get("report_type") == "final_big_bang":
        distribution = content.get("outcome_distribution") or {}
        outcome_conclusions = content.get("outcome_conclusions") or {}
        endpoint = outcome_conclusions.get("likely_endpoint") or {}
        endpoint_text = endpoint.get("interpretation") or "No likely endpoint trace was available."
        return {
            "executive_summary": content.get("summary") or "Final Big Bang report generated.",
            "outcome_interpretation": (
                f"Compared timelines: {len(content.get('multiverse_comparison') or [])}. "
                f"Total social posts: {distribution.get('total_social_posts', 0)}. "
                f"Total sociology signals: {distribution.get('total_sociology_signals', 0)}. "
                f"{endpoint_text}"
            ),
            "management_notes": "Review divergence drivers and per-multiverse reports before continuing any terminal timeline.",
            "risk_notes": "Deterministic fallback summary used when the report agent is unavailable.",
        }
    source = content.get("source") or {}
    distribution = content.get("outcome_distribution") or {}
    return {
        "executive_summary": content.get("summary") or "Multiverse report generated.",
        "outcome_interpretation": (
            f"{source.get('ui_label')} v{source.get('multiverse_version')} produced "
            f"{distribution.get('social_posts', 0)} social posts and "
            f"{distribution.get('sociology_signals', 0)} sociology signals."
        ),
        "management_notes": "This report is bound to the source multiverse version and latest tick listed in the source metadata.",
        "risk_notes": "Deterministic fallback summary used when the report agent is unavailable.",
    }


def _base_generation_metadata(*, report_type: str, big_bang_id, model: str, source: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_type": report_type,
        "big_bang_id": str(big_bang_id),
        "model": model,
        "source": source,
        "storage": {
            "canonical": "report_versions.content",
            "artifacts": "cached render outputs",
            "pdf": "compiled from structured report content on request",
        },
    }


def _write_markdown_artifact(
    db: Session,
    *,
    report_version: models.ReportVersion,
    relative_path: str,
) -> models.Artifact:
    report = db.get(models.Report, report_version.report_id)
    if report is None:
        raise ValueError("report not found")
    return ArtifactStore().write_text(
        db,
        big_bang_id=report.big_bang_id,
        relative_path=relative_path,
        body=render_report_version_to_markdown(report_version),
        kind="report_markdown",
        content_type="text/markdown",
    )


def _markdown_list(items: list[Any]) -> list[str]:
    return [f"- {_markdown_value(item)}" for item in items]


def _markdown_block(value: Any, *, indent: int = 0) -> list[str]:
    prefix = " " * indent
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            if isinstance(item, dict):
                lines.append(f"{prefix}- {key}:")
                lines.extend(_markdown_block(item, indent=indent + 2))
            elif isinstance(item, list):
                lines.append(f"{prefix}- {key}:")
                for entry in item:
                    lines.append(f"{prefix}  - {_markdown_value(entry)}")
            else:
                lines.append(f"{prefix}- {key}: {_markdown_value(item)}")
        return lines
    if isinstance(value, list):
        return [f"{prefix}- {_markdown_value(item)}" for item in value]
    return [f"{prefix}{_markdown_value(value)}"]


def _markdown_table(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["(no rows)"]
    columns = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_markdown_cell(row.get(column)) for column in columns) + " |")
    return lines


def _markdown_cell(value: Any) -> str:
    text = _markdown_value(value)
    return text.replace("\n", " ").replace("|", "\\|")


def _markdown_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return str(value)
