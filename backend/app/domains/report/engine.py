from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models
from app.llm.audit import LLMCallError, complete_with_audit
from app.llm.routing import AuditedLLMRoute, resolve_audited_llm_route
from app.domains.endpoint_ledger.service import (
    attach_report_version_to_ledger,
    endpoint_ledger_report_payload,
    evaluate_endpoint_ledger,
    latest_endpoint_ledger,
)
from app.domains.multiverse.runtime_config import multiverse_runtime_config_version
from app.domains.report.adjudication import (
    evaluate_timeline_adjudication,
    timeline_adjudication_entries,
)
from app.domains.tick.tick_bundles import TickBundleHydrationContext, hydrate_tick_bundle
from app.storage.pdf_store import render_markdown_pdf_bytes

REPORT_SCHEMA_VERSION = "worldfork.report.v2"
REPORT_AGENT_TEXT_KEYS = (
    "report_markdown",
    "executive_summary",
    "outcome_interpretation",
    "management_notes",
    "risk_notes",
)
REPORT_AGENT_STRUCTURED_KEYS = (
    "endpoint_histogram",
    "terminality_assessment",
    "contradiction_check",
)
REPORT_AGENT_OUTPUT_KEYS = (*REPORT_AGENT_TEXT_KEYS, *REPORT_AGENT_STRUCTURED_KEYS)
REPORT_AGENT_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "report_markdown": {"type": "string"},
        "executive_summary": {"type": "string"},
        "outcome_interpretation": {"type": "string"},
        "management_notes": {"type": "string"},
        "risk_notes": {"type": "string"},
        "endpoint_histogram": {"type": "array"},
        "terminality_assessment": {"type": "object"},
        "contradiction_check": {"type": "object"},
    },
    "required": list(REPORT_AGENT_OUTPUT_KEYS),
    "additionalProperties": False,
}
REPORT_AGENT_STANDARD_TIMELINE_LIMIT = 12
REPORT_AGENT_RESCUE_TIMELINE_LIMIT = 6
REPORT_AGENT_STATE_SAMPLE_LIMIT = 5
REPORT_AGENT_STANDARD_MAX_TOKENS = 2400
REPORT_AGENT_RESCUE_MAX_TOKENS = 1600


@dataclass(frozen=True)
class RenderedReport:
    body: bytes
    content_type: str
    filename: str


def generate_multiverse_report(
    db: Session,
    *,
    multiverse: models.Multiverse,
    title: str | None = None,
    summary: str | None = None,
) -> models.ReportVersion:
    report = _get_or_create_report(
        db,
        big_bang_id=multiverse.big_bang_id,
        multiverse_id=multiverse.id,
        report_type="multiverse",
    )
    planned_version = report.current_version + 1
    latest_tick = _latest_tick(db, multiverse.id)
    title_text = title or f"Multiverse {multiverse.ui_label} Report"
    content = _build_multiverse_report_content(
        db,
        multiverse=multiverse,
        title=title_text,
        summary=summary,
        report_version_number=planned_version,
        latest_tick=latest_tick,
    )
    big_bang = db.get(models.BigBang, multiverse.big_bang_id)
    if big_bang is None:
        raise ValueError("big bang not found")
    endpoint_ledger = evaluate_endpoint_ledger(
        db,
        big_bang=big_bang,
        multiverse=multiverse,
        source_type="report_time",
        created_by="report_agent",
        use_llm=False,
    )
    _attach_endpoint_ledger_content(db, content, endpoint_ledger)
    metadata = _base_generation_metadata(
        report_type="multiverse",
        big_bang_id=multiverse.big_bang_id,
        model=get_settings().report_agent_model,
        source={"multiverse_id": str(multiverse.id), "multiverse_version": multiverse.version},
    )
    _commit_report_inputs_before_llm(db)
    llm_report, llm_call = _run_report_agent(db, big_bang_id=multiverse.big_bang_id, content=content)
    _complete_report_agent_structured_fields(llm_report, content)
    content["llm_report"] = llm_report
    content["ai_summary"] = _summary_fields(llm_report)
    report_agent_model = _attach_report_agent_call_metadata(metadata, llm_call)
    metadata["report_agent_status"] = "succeeded"
    metadata["report_agent_prompt_mode"] = (llm_call.meta or {}).get("prompt_mode")
    metadata["report_agent_attempt"] = (llm_call.meta or {}).get("report_agent_attempt")
    _refresh_report_counts(db, big_bang_id=multiverse.big_bang_id, content=content)

    report, previous_version, version = _allocate_report_version(db, report_id=report.id)
    _set_content_report_version(content, version)
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
        model=report_agent_model,
        supersedes_report_version_id=previous_version.id if previous_version else None,
    )
    db.add(report_version)
    db.flush()
    attach_report_version_to_ledger(db, ledger=endpoint_ledger, report_version_id=report_version.id)
    return report_version


def generate_final_big_bang_report(
    db: Session,
    *,
    big_bang: models.BigBang,
    title: str | None = None,
    summary: str | None = None,
) -> models.ReportVersion:
    report = _get_or_create_report(
        db,
        big_bang_id=big_bang.id,
        multiverse_id=None,
        report_type="final_big_bang",
    )
    planned_version = report.current_version + 1
    multiverses = list(
        db.scalars(
            select(models.Multiverse)
            .where(models.Multiverse.big_bang_id == big_bang.id)
            .order_by(models.Multiverse.ui_label)
        ).all()
    )
    title_text = title or f"{big_bang.name} Final Big Bang Report"
    content = _build_final_report_content(
        db,
        big_bang=big_bang,
        multiverses=multiverses,
        title=title_text,
        summary=summary,
        report_version_number=planned_version,
    )
    _ensure_multiverse_endpoint_ledgers(db, big_bang=big_bang, multiverses=multiverses)
    adjudication = evaluate_timeline_adjudication(
        db,
        big_bang=big_bang,
        source_type="report_time",
        created_by="report_agent",
    )
    _attach_timeline_adjudication_content(db, content, adjudication)
    endpoint_ledger = evaluate_endpoint_ledger(
        db,
        big_bang=big_bang,
        source_type="report_time",
        created_by="report_agent",
        use_llm=False,
    )
    _attach_endpoint_ledger_content(db, content, endpoint_ledger)
    _patch_outcome_conclusions_from_timeline_adjudication(db, content)
    label_by_multiverse_id = {str(item.id): item.ui_label for item in multiverses}
    reports = list(db.scalars(select(models.Report).where(models.Report.big_bang_id == big_bang.id)).all())
    content["report_inventory"] = _report_inventory_items(
        reports,
        label_by_multiverse_id=label_by_multiverse_id,
        planned_final_report_id=report.id,
        planned_final_version=planned_version,
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
    _commit_report_inputs_before_llm(db)
    llm_report, llm_call = _run_report_agent(db, big_bang_id=big_bang.id, content=content)
    _complete_report_agent_structured_fields(llm_report, content)
    content["llm_report"] = llm_report
    content["ai_summary"] = _summary_fields(llm_report)
    report_agent_model = _attach_report_agent_call_metadata(metadata, llm_call)
    metadata["report_agent_status"] = "succeeded"
    metadata["report_agent_prompt_mode"] = (llm_call.meta or {}).get("prompt_mode")
    metadata["report_agent_attempt"] = (llm_call.meta or {}).get("report_agent_attempt")
    _refresh_report_counts(db, big_bang_id=big_bang.id, content=content)

    report, previous_version, version = _allocate_report_version(db, report_id=report.id)
    _set_content_report_version(content, version)
    content["report_inventory"] = _report_inventory_items(
        reports,
        label_by_multiverse_id=label_by_multiverse_id,
        planned_final_report_id=report.id,
        planned_final_version=version,
    )
    report.status = "completed"
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
        model=report_agent_model,
        supersedes_report_version_id=previous_version.id if previous_version else None,
    )
    db.add(report_version)
    db.flush()
    attach_report_version_to_ledger(db, ledger=endpoint_ledger, report_version_id=report_version.id)
    adjudication.source_report_version_id = report_version.id
    return report_version


def _allocate_report_version(
    db: Session,
    *,
    report_id,
) -> tuple[models.Report, models.ReportVersion | None, int]:
    report = db.execute(
        select(models.Report)
        .where(models.Report.id == report_id)
        .with_for_update()
    ).scalar_one()
    previous_version = _latest_report_version(db, report.id)
    latest_number = previous_version.version if previous_version else 0
    version = max(int(report.current_version or 0), int(latest_number or 0)) + 1
    report.current_version = version
    db.flush()
    return report, previous_version, version


def _set_content_report_version(content: dict[str, Any], version: int) -> None:
    source = content.get("source")
    if isinstance(source, dict):
        source["report_version"] = version


def _ensure_multiverse_endpoint_ledgers(
    db: Session,
    *,
    big_bang: models.BigBang,
    multiverses: list[models.Multiverse],
) -> None:
    for multiverse in multiverses:
        existing = latest_endpoint_ledger(
            db,
            big_bang_id=big_bang.id,
            multiverse_id=multiverse.id,
            scope="multiverse",
        )
        if existing is not None:
            continue
        evaluate_endpoint_ledger(
            db,
            big_bang=big_bang,
            multiverse=multiverse,
            source_type="timeline_adjudication_seed",
            created_by="timeline_adjudicator",
            use_llm=False,
        )


def render_report_version_to_markdown(report_version: models.ReportVersion) -> str:
    content = report_version.content or {}
    if not content:
        return f"# {report_version.title}\n\n{report_version.summary or ''}\n"
    return render_structured_report_markdown(content)


def _attach_endpoint_ledger_content(
    db: Session,
    content: dict[str, Any],
    ledger: models.EndpointLedgerVersion | None,
) -> None:
    if ledger is None:
        source = content.get("source") or {}
        ledger = latest_endpoint_ledger(
            db,
            big_bang_id=source.get("big_bang_id"),
            multiverse_id=source.get("multiverse_id"),
            scope="multiverse" if source.get("multiverse_id") else "big_bang",
        )
    payload = endpoint_ledger_report_payload(db, ledger)
    content["endpoint_ledger"] = payload
    content["endpoint_histogram"] = payload.get("histogram", [])
    content["terminality_assessment"] = payload.get("terminality_assessment", {})
    content["contradiction_check"] = payload.get("contradiction_check", {})
    aggregation_payload = payload.get("payload") if isinstance(payload.get("payload"), dict) else {}
    if aggregation_payload.get("aggregation") in {"path_probability_weighted", "path_mass_by_endpoint_status"}:
        content["path_probability_distribution"] = aggregation_payload.get("path_probability_distribution", [])
        content["endpoint_path_mass_distribution"] = aggregation_payload.get("endpoint_path_mass_distribution", [])
        content["plot_distribution"] = aggregation_payload.get("plot_distribution", {})
        outcome_distribution = content.get("outcome_distribution")
        if isinstance(outcome_distribution, dict):
            outcome_distribution["endpoint_path_mass_method"] = "path_mass_by_endpoint_status"
            outcome_distribution["path_probability_mass"] = aggregation_payload.get("path_probability_mass")
            outcome_distribution["endpoint_path_mass_distribution"] = aggregation_payload.get("endpoint_path_mass_distribution", [])
    _patch_outcome_conclusions_from_endpoint_ledger(content)


def _attach_timeline_adjudication_content(
    db: Session,
    content: dict[str, Any],
    adjudication: models.TimelineAdjudicationVersion | None,
) -> None:
    if adjudication is None:
        return
    entries = timeline_adjudication_entries(db, adjudication.id)
    content["timeline_adjudication"] = {
        "adjudication_version_id": str(adjudication.id),
        "version": adjudication.version,
        "status": adjudication.status,
        "source_type": adjudication.source_type,
        "summary": adjudication.summary,
        "payload": adjudication.payload or {},
        "entries": [
            {
                "multiverse_id": str(entry.multiverse_id),
                "ui_label": entry.ui_label,
                "viability_status": entry.viability_status,
                "include_in_final": entry.include_in_final,
                "prune_reason": entry.prune_reason,
                "original_path_probability": entry.original_path_probability,
                "effective_path_probability": entry.effective_path_probability,
                "mass_disposition": entry.mass_disposition,
                "endpoint_key": entry.endpoint_key,
                "endpoint_status": entry.endpoint_status,
                "evidence_summary": entry.evidence_summary or {},
            }
            for entry in entries
        ],
    }


def _patch_outcome_conclusions_from_endpoint_ledger(content: dict[str, Any]) -> None:
    conclusions = content.get("outcome_conclusions")
    if not isinstance(conclusions, dict):
        return
    histogram = content.get("endpoint_histogram") or []
    if not histogram:
        return
    top = max(histogram, key=lambda item: float(item.get("probability") or 0))
    if top.get("endpoint_key") in {"endpoint_unresolved", "endpoint_insufficient_ticks"} or top.get("status") in {
        "unresolved",
        "process_only",
        "insufficient_ticks",
    }:
        return
    likely = dict(conclusions.get("likely_endpoint") or {})
    likely["endpoint_key"] = top.get("endpoint_key")
    likely["endpoint_label"] = top.get("label")
    likely["endpoint_path_mass"] = top.get("path_mass")
    likely["endpoint_status"] = top.get("status")
    likely["endpoint_selection_basis"] = "endpoint_ledger"
    previous = likely.get("interpretation") or ""
    likely["interpretation"] = (
        f"Endpoint ledger favors {top.get('label')} with path_mass={top.get('path_mass')} "
        f"and status={top.get('status')}. {previous}"
    ).strip()
    conclusions["likely_endpoint"] = likely


def _patch_outcome_conclusions_from_timeline_adjudication(db: Session, content: dict[str, Any]) -> None:
    conclusions = content.get("outcome_conclusions")
    adjudication = content.get("timeline_adjudication")
    if not isinstance(conclusions, dict) or not isinstance(adjudication, dict):
        return
    entries = adjudication.get("entries")
    if not isinstance(entries, list):
        return
    included_entries = [item for item in entries if isinstance(item, dict) and item.get("include_in_final")]
    if not included_entries:
        return
    comparison = content.get("multiverse_comparison")
    comparison_items = comparison if isinstance(comparison, list) else []
    comparison_by_id = {
        str(item.get("multiverse_id")): item
        for item in comparison_items
        if isinstance(item, dict) and item.get("multiverse_id")
    }

    def key(entry: dict[str, Any]) -> tuple[float, float, int, str]:
        row = comparison_by_id.get(str(entry.get("multiverse_id"))) or {}
        return (
            _float_or_default(entry.get("effective_path_probability"), 0.0),
            _float_or_default(row.get("latest_branch_score"), 0.0),
            int(row.get("latest_tick_index") or 0),
            str(entry.get("ui_label") or row.get("ui_label") or ""),
        )

    selected_entry = max(included_entries, key=key)
    selected_id = str(selected_entry.get("multiverse_id") or "")
    row = comparison_by_id.get(selected_id) or {}
    multiverse = db.scalar(select(models.Multiverse).where(models.Multiverse.id == selected_id))
    latest_tick = _latest_tick(db, multiverse.id) if multiverse is not None else None
    latest_review = _latest_god_review(db, multiverse.id) if multiverse is not None else None

    likely = dict(conclusions.get("likely_endpoint") or {})
    endpoint_source = {
        **row,
        "multiverse_id": selected_id,
        "ui_label": selected_entry.get("ui_label") or row.get("ui_label"),
        "status": row.get("status"),
        "latest_tick_index": row.get("latest_tick_index"),
        "latest_branch_score": row.get("latest_branch_score"),
        "path_probability": row.get("path_probability"),
    }
    likely.update(
        {
            "multiverse_id": selected_id,
            "multiverse_label": endpoint_source.get("ui_label"),
            "multiverse_status": endpoint_source.get("status"),
            "latest_tick_index": endpoint_source.get("latest_tick_index"),
            "latest_tick_status": latest_tick.status if latest_tick else None,
            "latest_tick_summary": latest_tick.summary if latest_tick else None,
            "branch_score": endpoint_source.get("latest_branch_score"),
            "god_decision": latest_review.decision if latest_review else None,
            "god_rationale": latest_review.rationale if latest_review else None,
            "endpoint_key": selected_entry.get("endpoint_key") or likely.get("endpoint_key"),
            "endpoint_status": selected_entry.get("endpoint_status") or likely.get("endpoint_status"),
            "endpoint_selection_basis": "timeline_adjudication",
            "effective_path_probability": selected_entry.get("effective_path_probability"),
            "interpretation": _timeline_adjudication_interpretation(
                endpoint_source,
                selected_entry,
                latest_tick,
                latest_review,
            ),
        }
    )
    conclusions["likely_endpoint"] = likely
    mechanisms = conclusions.get("causal_mechanisms")
    if isinstance(mechanisms, list):
        replacement = (
            f"Timeline adjudication selected {endpoint_source.get('ui_label')} from retained timelines "
            f"with effective path probability={selected_entry.get('effective_path_probability')}, "
            f"status={endpoint_source.get('status')}, latest tick={endpoint_source.get('latest_tick_index')}, "
            f"and branch score={endpoint_source.get('latest_branch_score')}."
        )
        conclusions["causal_mechanisms"] = [
            item
            for item in mechanisms
            if not (isinstance(item, str) and item.startswith("Endpoint selection favored "))
        ] + [replacement]


def render_report_version_ephemeral(
    db: Session,
    *,
    report_version: models.ReportVersion,
    output_format: str,
    force: bool = False,
) -> RenderedReport:
    del force
    output_format = output_format.lower()
    if output_format not in {"markdown", "pdf"}:
        raise ValueError("output_format must be markdown or pdf")

    markdown = render_report_version_to_markdown(report_version)
    if db.get(models.Report, report_version.report_id) is None:
        raise ValueError("report not found")
    if output_format == "markdown":
        return RenderedReport(
            body=markdown.encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
            filename=f"report_v{report_version.version}_{report_version.id}.md",
        )

    return RenderedReport(
        body=render_markdown_pdf_bytes(
            title=report_version.title,
            markdown=markdown,
        ),
        content_type="application/pdf",
        filename=f"report_v{report_version.version}_{report_version.id}.pdf",
    )


def render_structured_report_markdown(content: dict[str, Any]) -> str:
    llm_report = content.get("llm_report") or {}
    llm_markdown = llm_report.get("report_markdown") if isinstance(llm_report, dict) else None
    if isinstance(llm_markdown, str) and llm_markdown.strip():
        llm_lines = [llm_markdown.strip(), "", "---", "", "## Structured Evidence Appendix", ""]
        llm_lines.extend(_structured_report_detail_lines(content, heading_level=3))
        return "\n".join(llm_lines).rstrip() + "\n"

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
    lines.extend(_structured_report_detail_lines(content, heading_level=2))
    return "\n".join(lines).rstrip() + "\n"


def _structured_report_detail_lines(content: dict[str, Any], *, heading_level: int) -> list[str]:
    marker = "#" * heading_level
    lines: list[str] = []
    if content.get("outcome_conclusions"):
        lines.extend([f"{marker} Outcome Conclusions", ""])
        conclusions = content["outcome_conclusions"]
        likely_endpoint = conclusions.get("likely_endpoint") or {}
        if likely_endpoint:
            lines.extend([f"{marker}# Likely Endpoint", ""])
            lines.extend(_markdown_block(likely_endpoint))
            lines.append("")
        if conclusions.get("causal_mechanisms"):
            lines.extend([f"{marker}# Causal Mechanisms", ""])
            lines.extend(_markdown_list(conclusions["causal_mechanisms"]))
            lines.append("")
        if conclusions.get("key_event_traces"):
            lines.extend([f"{marker}# Key Event Traces", ""])
            lines.extend(_markdown_list(conclusions["key_event_traces"]))
            lines.append("")
        if conclusions.get("god_review_trace"):
            lines.extend([f"{marker}# God Review Trace", ""])
            lines.extend(_markdown_list(conclusions["god_review_trace"]))
            lines.append("")
    if content.get("endpoint_histogram"):
        lines.extend([f"{marker} Endpoint Histogram", ""])
        lines.extend(_markdown_table(content["endpoint_histogram"]))
        lines.append("")
    if content.get("terminality_assessment"):
        lines.extend([f"{marker} Terminality Assessment", ""])
        lines.extend(_markdown_block(content["terminality_assessment"]))
        lines.append("")
    if content.get("contradiction_check"):
        lines.extend([f"{marker} Contradiction Check", ""])
        lines.extend(_markdown_block(content["contradiction_check"]))
        lines.append("")
    for section in content.get("sections", []):
        heading = section.get("heading")
        if heading:
            lines.extend([f"{marker} {heading}", ""])
        if section.get("body"):
            lines.extend([str(section["body"]), ""])
        if section.get("items"):
            lines.extend(_markdown_list(section["items"]))
            lines.append("")
        if section.get("table"):
            lines.extend(_markdown_table(section["table"]))
            lines.append("")
    if content.get("outcome_distribution"):
        lines.extend([f"{marker} Outcome Distribution", ""])
        lines.extend(_markdown_block(content["outcome_distribution"]))
        lines.append("")
    if content.get("multiverse_comparison"):
        lines.extend([f"{marker} Multiverse Comparison", ""])
        lines.extend(
            _markdown_table(
                [
                    {
                        "label": item.get("ui_label"),
                        "version": item.get("version"),
                        "status": item.get("status"),
                        "ticks": item.get("tick_count"),
                        "branch_probability": item.get("branch_probability"),
                        "path_probability": item.get("path_probability"),
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
    if content.get("timeline_adjudication"):
        adjudication = content["timeline_adjudication"]
        lines.extend([f"{marker} Timeline Adjudication", ""])
        lines.extend(
            _markdown_block(
                {
                    "version": adjudication.get("version"),
                    "summary": adjudication.get("summary"),
                    **(adjudication.get("payload") or {}),
                }
            )
        )
        lines.append("")
        lines.extend(
            _markdown_table(
                [
                    {
                        "label": item.get("ui_label"),
                        "viability": item.get("viability_status"),
                        "include": item.get("include_in_final"),
                        "original_path": item.get("original_path_probability"),
                        "effective_path": item.get("effective_path_probability"),
                        "endpoint": item.get("endpoint_key"),
                        "reason": item.get("prune_reason"),
                    }
                    for item in adjudication.get("entries", [])
                ]
            )
        )
        lines.append("")
    if content.get("report_inventory"):
        lines.extend([f"{marker} Report Inventory", ""])
        lines.extend(_markdown_list([_report_inventory_line(item) for item in content["report_inventory"]]))
        lines.append("")
    if content.get("evidence_appendix"):
        lines.extend([f"{marker} Evidence Appendix", ""])
        lines.extend(_markdown_block(content["evidence_appendix"]))
        lines.append("")
    return lines


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
    report = db.scalar(query)
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


def _commit_report_inputs_before_llm(db: Session) -> None:
    """Persist report prep rows before the live report-agent call starts."""
    db.commit()


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
    hydration_context = TickBundleHydrationContext()
    metrics = _multiverse_metrics(
        db,
        multiverse,
        latest_tick=latest_tick,
        hydration_context=hydration_context,
    )
    timeline = []
    for tick in ticks:
        final_bundle = hydrate_tick_bundle(
            db,
            tick,
            "final_bundle",
            context=hydration_context,
        )
        timeline.append(
            {
                "tick_id": str(tick.id),
                "tick_index": tick.tick_index,
                "label": tick.ui_label,
                "status": tick.status,
                "summary": tick.summary,
                "branch_score": final_bundle.get("branch_score"),
                "god_decision": (final_bundle.get("god_review") or {}).get("decision"),
            }
        )
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
                "items": _state_change_items(db, latest_tick, hydration_context=hydration_context),
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
    _apply_multiverse_report_snapshots(db, big_bang_id=big_bang.id, comparison=comparison)
    lineage_edges = list(
        db.scalars(
            select(models.MultiverseLineageEdge)
            .where(models.MultiverseLineageEdge.big_bang_id == big_bang.id)
            .order_by(models.MultiverseLineageEdge.created_at)
        ).all()
    )
    god_decisions: Counter[str] = Counter()
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
            "report_statuses": dict(Counter(item.get("report_status") for item in comparison)),
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
                        "branch_probability": edge.branch_probability,
                        "parent_path_probability": edge.parent_path_probability,
                        "child_path_probability": edge.child_path_probability,
                        "probability_basis": edge.probability_basis or {},
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


def _timeline_adjudication_interpretation(
    endpoint_source: dict[str, Any],
    adjudication_entry: dict[str, Any],
    latest_tick: models.TickSnapshot | None,
    latest_review: models.GodAgentReview | None,
) -> str:
    label = endpoint_source.get("ui_label") or "The selected timeline"
    endpoint_key = adjudication_entry.get("endpoint_key") or "unknown"
    endpoint_status = adjudication_entry.get("endpoint_status") or "unknown"
    effective_path = adjudication_entry.get("effective_path_probability")
    tick_index = endpoint_source.get("latest_tick_index")
    base = (
        f"Timeline adjudication retained {label} as the representative timeline for endpoint "
        f"{endpoint_key} with endpoint_status={endpoint_status}, effective path probability={effective_path}, "
        f"timeline status={endpoint_source.get('status')}, and latest tick index={tick_index}."
    )
    if str(endpoint_status).lower() in {"unresolved", "process_only", "active", "pending"}:
        base += " This is not a resolved terminal endpoint claim."
    if latest_review is not None:
        return f"{base} The latest God-agent review decided {latest_review.decision}: {latest_review.rationale}"
    if latest_tick is not None and latest_tick.summary:
        return f"{base} Latest tick summary: {latest_tick.summary}"
    return base


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
        f"latest tick={endpoint_source.get('latest_tick_index')}, branch score={endpoint_source.get('latest_branch_score')}, "
        f"and path probability={endpoint_source.get('path_probability')}."
    )
    return mechanisms


def _apply_multiverse_report_snapshots(
    db: Session,
    *,
    big_bang_id,
    comparison: list[dict[str, Any]],
) -> None:
    report_rows = db.scalars(
        select(models.Report).where(
            models.Report.big_bang_id == big_bang_id,
            models.Report.report_type == "multiverse",
            models.Report.multiverse_id.is_not(None),
        )
    ).all()
    report_by_multiverse_id = {str(report.multiverse_id): report for report in report_rows}
    for item in comparison:
        report = report_by_multiverse_id.get(str(item.get("multiverse_id")))
        if report is None:
            continue
        item["report_status"] = report.status
        item["report_current_version"] = report.current_version


def _report_inventory_items(
    reports: list[models.Report],
    *,
    label_by_multiverse_id: dict[str, str],
    planned_final_report_id=None,
    planned_final_version: int | None = None,
) -> list[dict[str, Any]]:
    inventory = []
    for item in sorted(reports, key=lambda item: (item.report_type, str(item.multiverse_id or ""))):
        status = item.status
        current_version = item.current_version
        if planned_final_report_id is not None and item.id == planned_final_report_id:
            status = "completed"
            current_version = planned_final_version or current_version
        inventory.append(
            {
                "report_id": str(item.id),
                "report_type": item.report_type,
                "status": status,
                "current_version": current_version,
                "multiverse_id": str(item.multiverse_id) if item.multiverse_id else None,
                "multiverse_label": label_by_multiverse_id.get(str(item.multiverse_id))
                if item.multiverse_id
                else None,
            }
        )
    return inventory


def _multiverse_metrics(
    db: Session,
    multiverse: models.Multiverse,
    *,
    latest_tick: models.TickSnapshot | None,
    hydration_context: TickBundleHydrationContext | None = None,
) -> dict[str, Any]:
    latest_final = (
        hydrate_tick_bundle(db, latest_tick, "final_bundle", context=hydration_context)
        if latest_tick
        else {}
    )
    god_decisions = Counter(
        db.scalars(
            select(models.GodAgentReview.decision).where(models.GodAgentReview.multiverse_id == multiverse.id)
        ).all()
    )
    tool_calls = Counter(
        db.scalars(select(models.ToolCall.tool_name).where(models.ToolCall.multiverse_id == multiverse.id)).all()
    )
    multiverse_state = multiverse.state or {}
    cohort_states = multiverse_state.get("cohort_current_states") or []
    hero_states = multiverse_state.get("hero_current_states") or []
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
        "branch_probability": _float_or_default(getattr(multiverse, "branch_probability", None), 1.0),
        "path_probability": _float_or_default(getattr(multiverse, "path_probability", None), 1.0),
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
        "idle_streak": multiverse_state.get("idle_streak"),
        "last_sociology_keys": sorted((multiverse_state.get("last_sociology") or {}).keys())[:20],
        "cohort_state_count": len(cohort_states),
        "hero_state_count": len(hero_states),
        "cohort_state_highlights": _compact_actor_state_list(cohort_states),
        "hero_state_highlights": _compact_actor_state_list(hero_states),
        "recent_event_highlights": _event_highlights(db, multiverse_id=multiverse.id),
    }


def _float_or_default(value: Any, default: float) -> float:
    try:
        parsed = float(value if value is not None else default)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:
        return default
    return parsed


def _event_highlights(db: Session, *, multiverse_id, limit: int = REPORT_AGENT_STATE_SAMPLE_LIMIT) -> list[dict[str, Any]]:
    events = db.scalars(
        select(models.Event)
        .where(models.Event.multiverse_id == multiverse_id)
        .order_by(models.Event.scheduled_tick.desc(), models.Event.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "title": _truncate_text(event.title, 180),
            "event_type": event.event_type,
            "status": event.status,
            "scheduled_tick": event.scheduled_tick,
            "actual_impact": _compact_report_value(event.actual_impact, max_items=4),
        }
        for event in events
    ]


STATE_HIGHLIGHT_KEYS = (
    "cohort_id",
    "hero_id",
    "actor_id",
    "name",
    "label",
    "mood",
    "mobilization_mode",
    "speech_mode",
    "attention",
    "attention_level",
    "expression_level",
    "fatigue",
    "grievance",
    "stance",
    "belief_state",
    "behavior_state",
    "emotions",
    "trust_summary",
    "trust",
    "readiness",
    "current_strategy",
    "fear_of_isolation",
    "mobilization_readiness",
    "perceived_majority",
    "represented_population",
)


def _compact_actor_state_list(states: list[Any], limit: int = REPORT_AGENT_STATE_SAMPLE_LIMIT) -> list[dict[str, Any]]:
    return [_compact_actor_state(item) for item in (states or [])[:limit]]


def _compact_actor_state(state: Any) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {"value": _truncate_text(str(state), 240)}
    compact: dict[str, Any] = {
        key: _compact_report_value(state[key], max_items=4)
        for key in STATE_HIGHLIGHT_KEYS
        if state.get(key) not in (None, "", {}, [])
    }
    raw_nested_state = state.get("state")
    nested_state: dict[str, Any] = raw_nested_state if isinstance(raw_nested_state, dict) else {}
    for key in STATE_HIGHLIGHT_KEYS:
        if len(compact) >= 10:
            break
        if key not in compact and nested_state.get(key) not in (None, "", {}, []):
            compact[key] = _compact_report_value(nested_state[key], max_items=4)
    graph_influence = nested_state.get("graph_influence")
    if isinstance(graph_influence, dict):
        compact["graph_influence"] = _compact_report_value(graph_influence, max_items=4)
    if compact:
        return compact
    for key, value in state.items():
        if len(compact) >= 6:
            break
        if isinstance(value, str | int | float | bool) and value not in ("", None):
            compact[str(key)] = _compact_report_value(value, max_items=4)
    return compact


def _compact_report_value(value: Any, *, max_items: int = 6):
    if isinstance(value, dict):
        compact = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                compact["_truncated"] = True
                break
            compact[str(key)] = _compact_report_value(item, max_items=max_items)
        return compact
    if isinstance(value, list):
        items = [_compact_report_value(item, max_items=max_items) for item in value[:max_items]]
        if len(value) > max_items:
            items.append({"_truncated_count": len(value) - max_items})
        return items
    if isinstance(value, str):
        return _truncate_text(value, 240)
    return value


def _truncate_text(value: str, limit: int = 400) -> str:
    if not isinstance(value, str):
        return str(value)
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


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


def _state_change_items(
    db: Session,
    latest_tick: models.TickSnapshot | None,
    *,
    hydration_context: TickBundleHydrationContext | None = None,
) -> list[dict[str, Any]]:
    if latest_tick is None:
        return []
    final = hydrate_tick_bundle(db, latest_tick, "final_bundle", context=hydration_context)
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


def _report_agent_prompt_content(content: dict[str, Any], *, mode: str) -> dict[str, Any]:
    limit = REPORT_AGENT_RESCUE_TIMELINE_LIMIT if mode == "rescue" else REPORT_AGENT_STANDARD_TIMELINE_LIMIT
    if content.get("report_type") == "final_big_bang":
        comparison = content.get("multiverse_comparison") or []
        selected = _select_report_timelines_for_final_report(content, comparison, limit=limit)
        adjudication = content.get("timeline_adjudication") or {}
        return {
            "report_type": "final_big_bang",
            "title": content.get("title"),
            "summary": content.get("summary"),
            "source": _compact_source(content.get("source") or {}),
            "outcome_conclusions": _compact_outcome_conclusions(content.get("outcome_conclusions") or {}),
            "outcome_distribution": _compact_distribution(content.get("outcome_distribution") or {}),
            "probability_context": _probability_context(content),
            "endpoint_ledger": _compact_endpoint_ledger(content),
            "endpoint_histogram": _compact_report_value(content.get("endpoint_histogram") or [], max_items=limit),
            "endpoint_path_mass_distribution": _compact_report_value(
                content.get("endpoint_path_mass_distribution") or [],
                max_items=limit,
            ),
            "plot_distribution": _compact_report_value(content.get("plot_distribution") or {}, max_items=8),
            "path_probability_distribution": _compact_path_probability_distribution(
                content.get("path_probability_distribution") or [],
                limit=limit,
            ),
            "timeline_adjudication": _compact_timeline_adjudication(adjudication, limit=limit),
            "terminality_assessment": _compact_report_value(content.get("terminality_assessment") or {}, max_items=6),
            "contradiction_check": _compact_report_value(content.get("contradiction_check") or {}, max_items=6),
            "selected_timelines": [_compact_timeline_metric(item) for item in selected],
            "timeline_selection": {
                "selected_count": len(selected),
                "total_count": len(comparison),
                "policy": _timeline_selection_policy(adjudication),
            },
            "divergence_drivers": _compact_divergence_drivers(content, limit=limit),
            "recurring_patterns": _section_items(content, "Recurring Patterns")[:limit],
            "report_inventory": _compact_report_inventory(content.get("report_inventory") or []),
            "evidence_gaps": _evidence_gaps_for_comparison(comparison),
            "quality_controls": _report_quality_controls(),
        }

    timeline_rows = _section_table(content, "Timeline")
    return {
        "report_type": "multiverse",
        "title": content.get("title"),
        "summary": content.get("summary"),
        "source": _compact_source(content.get("source") or {}),
        "outcome_distribution": _compact_timeline_metric(content.get("outcome_distribution") or {}),
            "probability_context": _probability_context(content),
            "endpoint_ledger": _compact_endpoint_ledger(content),
            "endpoint_histogram": _compact_report_value(content.get("endpoint_histogram") or [], max_items=limit),
        "terminality_assessment": _compact_report_value(content.get("terminality_assessment") or {}, max_items=6),
        "contradiction_check": _compact_report_value(content.get("contradiction_check") or {}, max_items=6),
        "timeline": _select_report_ticks(timeline_rows, limit=limit),
        "god_agent_decisions": _compact_report_value(
            _section_items(content, "God Agent Decisions")[:limit],
            max_items=limit,
        ),
        "cohort_and_hero_changes": _compact_report_value(
            _section_items(content, "Cohort and Hero Changes"),
            max_items=limit,
        ),
        "evidence_gaps": _evidence_gaps_for_comparison([content.get("outcome_distribution") or {}]),
        "quality_controls": _report_quality_controls(),
    }


def _compact_endpoint_ledger(content: dict[str, Any]) -> dict[str, Any]:
    raw_ledger = content.get("endpoint_ledger")
    ledger: dict[str, Any] = raw_ledger if isinstance(raw_ledger, dict) else {}
    raw_payload = ledger.get("payload")
    payload: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else {}
    return {
        "ledger_version_id": ledger.get("ledger_version_id"),
        "scope": ledger.get("scope"),
        "version": ledger.get("version"),
        "summary": _truncate_text(str(ledger.get("summary") or ""), 360),
        "entries": _compact_report_value((ledger.get("entries") or [])[:8], max_items=6),
        "aggregation": payload.get("aggregation"),
        "path_probability_mass": payload.get("path_probability_mass"),
        "endpoint_path_mass_distribution": _compact_report_value(
            payload.get("endpoint_path_mass_distribution") or [],
            max_items=8,
        ),
        "plot_distribution": _compact_report_value(payload.get("plot_distribution") or {}, max_items=8),
    }


def _compact_outcome_conclusions(conclusions: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(conclusions, dict):
        return {}
    likely = conclusions.get("likely_endpoint")
    compact_likely = _compact_report_value(likely or {}, max_items=12) if isinstance(likely, dict) else {}
    return {
        "likely_endpoint": compact_likely,
        "causal_mechanisms": _compact_report_value(conclusions.get("causal_mechanisms") or [], max_items=6),
    }


def _compact_source(source: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in source.items()
        if key
        in {
            "big_bang_status",
            "config_version",
            "report_version",
            "ui_label",
            "multiverse_version",
            "status",
            "source_tick_index",
        }
    }


def _compact_distribution(distribution: dict[str, Any]) -> dict[str, Any]:
    keys = {
        "timeline_statuses",
        "report_statuses",
        "god_decisions",
        "endpoint_path_mass_method",
        "endpoint_probability_method",
        "path_probability_mass",
        "endpoint_path_mass_distribution",
        "weighted_endpoint_histogram",
        "total_social_posts",
        "total_graph_edges",
        "total_sociology_signals",
    }
    compact = {key: distribution.get(key) for key in keys if key in distribution}
    if "weighted_endpoint_histogram" in compact:
        compact["weighted_endpoint_histogram"] = _compact_report_value(
            compact["weighted_endpoint_histogram"],
            max_items=8,
        )
    if "endpoint_path_mass_distribution" in compact:
        compact["endpoint_path_mass_distribution"] = _compact_report_value(
            compact["endpoint_path_mass_distribution"],
            max_items=8,
        )
    return compact


def _probability_context(content: dict[str, Any]) -> dict[str, Any]:
    if content.get("report_type") == "final_big_bang":
        raw_distribution = content.get("outcome_distribution")
        distribution: dict[str, Any] = raw_distribution if isinstance(raw_distribution, dict) else {}
        method = distribution.get("endpoint_path_mass_method") or distribution.get("endpoint_probability_method") or "endpoint_ledger"
        return {
            "scope": "final_big_bang",
            "endpoint_path_mass_method": method,
            "path_probability_mass": distribution.get("path_probability_mass"),
            "semantics": (
                "Endpoint ledger entries are yes/no/unresolved states. Final quantitative claims are path-mass "
                "aggregations across retained timelines, not per-endpoint ledger probabilities."
            ),
        }

    raw_metrics = content.get("outcome_distribution")
    metrics: dict[str, Any] = raw_metrics if isinstance(raw_metrics, dict) else {}
    return {
        "scope": "single_multiverse",
        "branch_probability": metrics.get("branch_probability"),
        "path_probability": metrics.get("path_probability"),
        "semantics": (
            "This is a single timeline report. branch_probability is the conditional probability assigned at "
            "this timeline's fork, and path_probability is the cumulative probability mass of this timeline. "
            "Endpoint ledger entries are terminal-state predicates, not probability estimates."
        ),
    }


def _compact_path_probability_distribution(rows: list[Any], *, limit: int) -> list[dict[str, Any]]:
    compact = []
    for row in rows[:limit]:
        if not isinstance(row, dict):
            continue
        item = {
            "ui_label": row.get("ui_label"),
            "status": row.get("status"),
            "path_probability": row.get("path_probability"),
            "original_path_probability": row.get("original_path_probability"),
            "normalized_weight": row.get("normalized_weight"),
            "viability_status": row.get("viability_status"),
            "include_in_final": row.get("include_in_final"),
            "prune_reason": row.get("prune_reason"),
        }
        compact.append({key: value for key, value in item.items() if value not in (None, "")})
    return compact


def _compact_timeline_adjudication(adjudication: dict[str, Any], *, limit: int) -> dict[str, Any]:
    if not isinstance(adjudication, dict) or not adjudication:
        return {}
    return {
        "version": adjudication.get("version"),
        "summary": _truncate_text(str(adjudication.get("summary") or ""), 360),
        "payload": _compact_report_value(adjudication.get("payload") or {}, max_items=8),
        "entries": _compact_report_value((adjudication.get("entries") or [])[:limit], max_items=8),
        "retained_labels": [
            item.get("ui_label")
            for item in adjudication.get("entries", [])
            if isinstance(item, dict) and item.get("include_in_final") and item.get("ui_label")
        ][:limit],
        "pruned_labels": [
            item.get("ui_label")
            for item in adjudication.get("entries", [])
            if isinstance(item, dict) and item.get("include_in_final") is False and item.get("ui_label")
        ][:limit],
    }


def _compact_divergence_drivers(content: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    label_by_id = {
        str(item.get("multiverse_id")): item.get("ui_label")
        for item in content.get("multiverse_comparison") or []
        if item.get("multiverse_id") and item.get("ui_label")
    }
    drivers = []
    for item in _section_items(content, "Divergence Drivers")[:limit]:
        if not isinstance(item, dict):
            drivers.append({"reason": _truncate_text(str(item), 240)})
            continue
        driver = {
            "parent_label": label_by_id.get(str(item.get("parent_multiverse_id"))),
            "child_label": label_by_id.get(str(item.get("child_multiverse_id"))),
            "fork_tick_index": item.get("fork_tick_index"),
            "reason": _truncate_text(str(item.get("reason") or ""), 360),
            "branch_probability": item.get("branch_probability"),
            "parent_path_probability": item.get("parent_path_probability"),
            "child_path_probability": item.get("child_path_probability"),
            "probability_basis": _compact_report_value(item.get("probability_basis") or {}, max_items=4),
        }
        drivers.append({key: value for key, value in driver.items() if value not in (None, "")})
    return drivers


def _compact_report_inventory(inventory: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "report_type": item.get("report_type"),
            "status": item.get("status"),
            "current_version": item.get("current_version"),
            "multiverse_label": item.get("multiverse_label"),
        }
        for item in inventory
    ]


def _report_quality_controls() -> list[str]:
    return [
        "Use exact numeric metrics from the digest; do not recalculate or round totals unless explicitly stated.",
        "Use report_inventory for report completion claims when present.",
        "Tick indexes are zero-based; describe latest_tick_index=2 as tick index 2, not tick 3.",
        "Do not infer executed events from queued events; queued means scheduled but not executed.",
        "Do not discuss LLM-call or artifact audit totals unless they are explicitly present in this digest.",
        "For final reports, explain endpoint_path_mass_distribution as path-mass-weighted outcomes derived from branch probabilities, not equal counts of timelines.",
        "For a single_multiverse probability_context, state branch_probability and path_probability when present, and describe endpoint ledger entries as terminal-state predicates.",
        "Use branch_probability and path_probability when explaining branch divergence; do not treat branch_score as probability.",
        "For final reports with timeline_adjudication, describe which timelines were retained or pruned and use effective_path_probability for final endpoint probability claims.",
        "When timeline_adjudication is present, base outcome interpretation, management notes, and risk notes on include_in_final=true retained timelines.",
        "Do not name a pruned timeline as a final endpoint comparator or decision target unless explicitly labeling it as pruned/non-retained evidence.",
        "If endpoint_status is unresolved, describe the chosen item as a retained representative timeline for an unresolved endpoint, not as a resolved terminal endpoint.",
    ]


def _timeline_selection_policy(adjudication: Any) -> str:
    if isinstance(adjudication, dict) and adjudication.get("entries"):
        return (
            "timeline_adjudication include_in_final=true retained timelines only; pruned timelines are "
            "non-retained evidence and must not be final endpoint comparators"
        )
    return "highest path probability, then branch score, deepest branches, and longest tick history"


def _select_report_timelines_for_final_report(
    content: dict[str, Any],
    comparison: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    adjudication = content.get("timeline_adjudication")
    if isinstance(adjudication, dict):
        retained_ids = {
            str(item.get("multiverse_id"))
            for item in adjudication.get("entries", [])
            if isinstance(item, dict) and item.get("include_in_final") and item.get("multiverse_id")
        }
        if retained_ids:
            retained = [item for item in comparison if str(item.get("multiverse_id")) in retained_ids]
            retained_effective_path = {
                str(item.get("multiverse_id")): _float_or_default(item.get("effective_path_probability"), 0.0)
                for item in adjudication.get("entries", [])
                if isinstance(item, dict) and item.get("include_in_final") and item.get("multiverse_id")
            }
            return sorted(
                retained,
                key=lambda item: (
                    retained_effective_path.get(str(item.get("multiverse_id")), 0.0),
                    item.get("latest_branch_score") or 0,
                    item.get("depth") or 0,
                    item.get("tick_count") or 0,
                    item.get("ui_label") or "",
                ),
                reverse=True,
            )[:limit]
    return _select_report_timelines(comparison, limit=limit)


def _select_report_timelines(comparison: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    return sorted(
        comparison,
        key=lambda item: (
            item.get("latest_branch_score") is not None,
            item.get("path_probability") or 0,
            item.get("latest_branch_score") or 0,
            item.get("depth") or 0,
            item.get("tick_count") or 0,
            item.get("ui_label") or "",
        ),
        reverse=True,
    )[:limit]


def _select_report_ticks(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    if len(rows) <= limit:
        selected = rows
    else:
        first = rows[:2]
        last = rows[-2:]
        important = [
            row
            for row in rows[2:-2]
            if row.get("branch_score") not in (None, 0) or row.get("god_decision") not in (None, "", "continue")
        ]
        selected = [*first, *important[: max(0, limit - len(first) - len(last))], *last]
    return [
        {
            "tick_index": row.get("tick_index"),
            "status": row.get("status"),
            "summary": _truncate_text(str(row.get("summary") or ""), 220),
            "branch_score": row.get("branch_score"),
            "god_decision": row.get("god_decision"),
        }
        for row in selected[:limit]
    ]


def _compact_timeline_metric(item: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "ui_label",
        "version",
        "status",
        "report_status",
        "report_current_version",
        "depth",
        "fork_tick_index",
        "branch_probability",
        "path_probability",
        "latest_tick_index",
        "latest_branch_score",
        "tick_count",
        "events_executed",
        "social_posts",
        "graph_edges",
        "sociology_signals",
        "god_decisions",
        "tool_calls",
        "sociology_signal_types",
        "idle_streak",
        "cohort_state_count",
        "hero_state_count",
        "cohort_state_highlights",
        "hero_state_highlights",
        "recent_event_highlights",
    )
    return {key: _compact_report_value(item.get(key), max_items=4) for key in keys if item.get(key) not in (None, {}, [])}


def _section_items(content: dict[str, Any], heading: str) -> list[Any]:
    for section in content.get("sections") or []:
        if section.get("heading") == heading:
            items = section.get("items")
            return items if isinstance(items, list) else []
    return []


def _section_table(content: dict[str, Any], heading: str) -> list[dict[str, Any]]:
    for section in content.get("sections") or []:
        if section.get("heading") == heading:
            table = section.get("table")
            return table if isinstance(table, list) else []
    return []


def _evidence_gaps_for_comparison(comparison: list[dict[str, Any]]) -> list[str]:
    gaps: list[str] = []
    missing_reports = [
        item.get("ui_label")
        for item in comparison
        if item.get("ui_label") and item.get("report_status") != "completed"
    ]
    if missing_reports:
        gaps.append(f"reports not completed for {', '.join(str(item) for item in missing_reports[:6])}")
    missing_scores = [
        item.get("ui_label") for item in comparison if item.get("ui_label") and item.get("latest_branch_score") is None
    ]
    if missing_scores:
        gaps.append(f"missing latest branch score for {', '.join(str(item) for item in missing_scores[:6])}")
    no_state = [
        item.get("ui_label")
        for item in comparison
        if item.get("ui_label")
        and item.get("cohort_state_count") in (None, 0)
        and item.get("hero_state_count") in (None, 0)
    ]
    if no_state:
        gaps.append(f"no cohort/hero state highlights for {', '.join(str(item) for item in no_state[:6])}")
    unknown_signal_labels = [
        item.get("ui_label")
        for item in comparison
        if item.get("ui_label") and (item.get("sociology_signal_types") or {}).get("unknown")
    ]
    if unknown_signal_labels:
        gaps.append(f"uncategorized sociology signals for {', '.join(str(item) for item in unknown_signal_labels[:6])}")
    return gaps


def _run_report_agent(
    db: Session,
    *,
    big_bang_id,
    content: dict[str, Any],
) -> tuple[dict[str, Any], models.LLMCall]:
    settings = get_settings()
    route = resolve_audited_llm_route(
        db,
        route=AuditedLLMRoute.REPORT_AGENT,
        fallback_provider=settings.default_llm_provider,
        fallback_model=settings.report_agent_model,
    )
    deterministic_candidates = [
        candidate.provider for candidate in route.candidates() if candidate.provider == "deterministic"
    ]
    if deterministic_candidates:
        raise LLMCallError(
            "Report generation requires live LLM providers; deterministic report providers are not allowed."
        )
    source = content.get("source") or {}
    source_id = source.get("multiverse_id") or source.get("big_bang_id") or str(big_bang_id)
    failures: list[str] = []
    for attempt, prompt_mode in enumerate(("standard", "rescue"), start=1):
        prompt_content = _report_agent_prompt_content(content, mode=prompt_mode)
        max_tokens = REPORT_AGENT_STANDARD_MAX_TOKENS if prompt_mode == "standard" else REPORT_AGENT_RESCUE_MAX_TOKENS
        try:
            response, call = complete_with_audit(
                db,
                big_bang_id=big_bang_id,
                purpose=(
                    f"report_agent_{content['report_type']}_{source_id}_"
                    f"{source.get('report_version')}_{prompt_mode}_attempt_{attempt}"
                ),
                model=route.primary.model,
                route=AuditedLLMRoute.REPORT_AGENT,
                messages=_report_agent_messages(prompt_content, mode=prompt_mode),
                json_schema=REPORT_AGENT_JSON_SCHEMA,
                json_response_transform=_coerce_report_agent_output,
                metadata={
                    "max_tokens": max_tokens,
                    "temperature": 0.2,
                    "agent_type": "report_agent",
                    "prompt_mode": prompt_mode,
                    "report_agent_attempt": attempt,
                    "prompt_payload_char_count": len(json.dumps(prompt_content, default=str)),
                },
            )
            parsed = response.parsed if isinstance(response.parsed, dict) else {}
            if parsed.get("fallback") is True:
                raise ValueError("report agent response came from a fallback payload")
            return _coerce_report_agent_output(parsed), call
        except (LLMCallError, ValueError) as exc:
            failures.append(f"{prompt_mode}: {exc}")
            continue

    raise LLMCallError(f"Report agent failed after standard and rescue attempts: {'; '.join(failures)}")


def _report_agent_messages(prompt_content: dict[str, Any], *, mode: str) -> list[dict[str, str]]:
    target_length = "900-1600 words" if mode == "standard" else "550-1000 words"
    return [
        {
            "role": "system",
            "content": (
                "You are the WorldFork report agent. Return exactly one JSON object with keys "
                "report_markdown, executive_summary, outcome_interpretation, management_notes, risk_notes, "
                "endpoint_histogram, terminality_assessment, contradiction_check. "
                "Use only the supplied structured report digest. Do not invent real-world facts. "
                "The report_markdown field must be a complete long-form Markdown report, not a short summary. "
                "Always include a Path-Mass Accounting section. For single-multiverse reports, that section "
                "must state branch_probability and path_probability when present and explicitly say endpoint "
                "ledger entries are terminal-state predicates, not probabilities. For final Big Bang reports, "
                "that section must state endpoint path mass by endpoint/status. "
                f"Target {target_length}. Prefer decision-useful interpretation over raw row restatement. Explain "
                "outcome distribution, branch divergence, cohort/hero state movement, report/version "
                "bindings, and evidence gaps. When the digest includes path-probability weighting, describe "
                "endpoint path mass as branch-path mass rather than equal timeline counts. Treat "
                "probability_context as binding: in single-multiverse reports, separate timeline path "
                "probability from endpoint-ledger status; in final Big Bang reports, use the stated "
                "endpoint path-mass method and the timeline_adjudication pruning ledger. When "
                "timeline_adjudication exists, selected_timelines contains the retained final-analysis "
                "timelines; do not treat pruned timelines as final endpoint comparators, live decision "
                "targets, or management targets unless you explicitly label them as pruned/non-retained "
                "evidence. If likely_endpoint.endpoint_status is unresolved, call it a retained "
                "representative timeline for an unresolved endpoint, not a resolved terminal endpoint. "
                "Prune unneeded raw IDs and low-signal rows; refer to labels, "
                "versions, ticks, branch scores, and path probabilities when they help a reviewer. If evidence "
                "is absent or zero, say so plainly. Treat digest quality_controls as binding instructions, especially exact "
                "numeric totals, report completion claims, zero-based tick indexes, and queued-versus-executed "
                "events. Do not emit Markdown outside the JSON object."
            ),
        },
        {
            "role": "user",
            "content": "Structured report digest:\n"
            + json.dumps(prompt_content, ensure_ascii=True, sort_keys=True, default=str),
        },
    ]


def _coerce_report_agent_output(parsed: dict[str, Any]) -> dict[str, Any]:
    if parsed.get("provider") == "deterministic":
        raise ValueError("report agent response came from deterministic provider")
    report_markdown = _coerce_report_text(parsed.get("report_markdown"), separator="\n\n")
    if not report_markdown.strip():
        raise ValueError("report agent response did not include report_markdown")
    output: dict[str, Any] = {"report_markdown": report_markdown.strip()}
    for key in REPORT_AGENT_TEXT_KEYS:
        if key == "report_markdown":
            continue
        value = parsed.get(key)
        output[key] = _truncate_text(_coerce_report_text(value), 2400)
    endpoint_histogram = parsed.get("endpoint_histogram")
    output["endpoint_histogram"] = _coerce_report_list(endpoint_histogram)
    terminality = parsed.get("terminality_assessment")
    output["terminality_assessment"] = _coerce_report_dict(terminality)
    contradiction = parsed.get("contradiction_check")
    output["contradiction_check"] = _coerce_report_dict(contradiction)
    if not output["executive_summary"]:
        output["executive_summary"] = _truncate_text(report_markdown.strip(), 1200)
    return output


def _coerce_report_text(value: Any, *, separator: str = "\n") -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return separator.join(_coerce_report_text(item, separator=separator).strip() for item in value if item is not None)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    return str(value)


def _coerce_report_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    text = _coerce_report_text(value).strip()
    return [{"summary": text}] if text else []


def _coerce_report_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {"items": value} if value else {}
    text = _coerce_report_text(value).strip()
    return {"summary": text} if text else {}


def _complete_report_agent_structured_fields(llm_report: dict[str, Any], content: dict[str, Any]) -> None:
    if not llm_report.get("endpoint_histogram"):
        llm_report["endpoint_histogram"] = content.get("endpoint_histogram") or []
    if not llm_report.get("terminality_assessment"):
        llm_report["terminality_assessment"] = content.get("terminality_assessment") or {}
    if not llm_report.get("contradiction_check"):
        llm_report["contradiction_check"] = content.get("contradiction_check") or {}


def _summary_fields(llm_report: dict[str, Any]) -> dict[str, Any]:
    summary = {}
    for key in REPORT_AGENT_TEXT_KEYS:
        if key == "report_markdown":
            continue
        value = llm_report.get(key)
        if value:
            summary[key] = value
    return summary


def _base_generation_metadata(*, report_type: str, big_bang_id, model: str, source: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "report_type": report_type,
        "big_bang_id": str(big_bang_id),
        "model": model,
        "source": source,
        "storage": {
            "canonical": "report_versions.content",
            "artifacts": "report renders are ephemeral and are not stored by default",
            "pdf": "compiled from LLM report markdown and structured evidence appendix only on request",
        },
    }


def _attach_report_agent_call_metadata(metadata: dict[str, Any], llm_call: Any) -> str:
    metadata["llm_call_id"] = str(llm_call.id)
    provider = getattr(llm_call, "provider", None)
    if provider:
        metadata["provider"] = provider
    model = str(getattr(llm_call, "model", None) or metadata.get("model") or get_settings().report_agent_model)
    metadata["model"] = model
    return model


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
