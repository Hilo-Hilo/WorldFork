from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.domains.endpoint_ledger.service import (
    endpoint_ledger_report_payload,
    latest_endpoint_ledger,
)
from app.domains.report.adjudication import (
    latest_timeline_adjudication,
    latest_timeline_adjudication_map,
    timeline_adjudication_entries,
)

MODE_LIMITS = {
    "summary": {"timelines": 8, "entries": 4, "ticks": 3, "events": 3, "reviews": 3},
    "standard": {"timelines": 16, "entries": 8, "ticks": 6, "events": 6, "reviews": 6},
    "rescue": {"timelines": 6, "entries": 4, "ticks": 3, "events": 3, "reviews": 3},
    "full": {"timelines": 100, "entries": 20, "ticks": 12, "events": 12, "reviews": 12},
}


def build_report_evidence_pack(
    db: Session,
    *,
    big_bang: models.BigBang,
    mode: str = "standard",
) -> dict[str, Any]:
    if mode not in MODE_LIMITS:
        mode = "standard"
    limits = MODE_LIMITS[mode]
    adjudication = latest_timeline_adjudication(db, big_bang_id=big_bang.id)
    adjudication_by_multiverse_id = latest_timeline_adjudication_map(db, big_bang_id=big_bang.id)
    multiverses = db.scalars(
        select(models.Multiverse)
        .where(models.Multiverse.big_bang_id == big_bang.id)
        .order_by(models.Multiverse.path_probability.desc().nullslast(), models.Multiverse.ui_label)
        .limit(limits["timelines"])
    ).all()
    latest_big_bang_ledger = latest_endpoint_ledger(db, big_bang_id=big_bang.id, scope="big_bang")
    token_budget: dict[str, Any] = {
        "mode": mode,
        "char_count": 0,
        "policy": "compact causal evidence; raw bundles and full transcripts excluded",
    }
    pack: dict[str, Any] = {
        "schema_version": "worldfork.report_evidence_pack.v1",
        "mode": mode,
        "big_bang": {
            "id": str(big_bang.id),
            "name": big_bang.name,
            "status": big_bang.status,
            "config_version": big_bang.current_config_version,
        },
        "endpoint_ledger": _compact_ledger_payload(db, latest_big_bang_ledger, limit=limits["entries"]),
        "timeline_adjudication": _compact_adjudication(db, adjudication, limit=limits["timelines"]),
        "timelines": [
            _timeline_pack(
                db,
                multiverse=multiverse,
                adjudication=adjudication_by_multiverse_id.get(str(multiverse.id)),
                limits=limits,
            )
            for multiverse in multiverses
        ],
        "report_inventory": _report_inventory(db, big_bang_id=big_bang.id),
        "token_budget": token_budget,
    }
    token_budget["char_count"] = len(json.dumps(pack, default=str, separators=(",", ":")))
    return pack


def _timeline_pack(
    db: Session,
    *,
    multiverse: models.Multiverse,
    adjudication: models.TimelineAdjudicationEntry | None,
    limits: dict[str, int],
) -> dict[str, Any]:
    ledger = latest_endpoint_ledger(
        db,
        big_bang_id=multiverse.big_bang_id,
        multiverse_id=multiverse.id,
        scope="multiverse",
    )
    return {
        "multiverse": {
            "id": str(multiverse.id),
            "ui_label": multiverse.ui_label,
            "status": multiverse.status,
            "report_status": multiverse.report_status,
            "version": multiverse.version,
            "parent_multiverse_id": str(multiverse.parent_multiverse_id) if multiverse.parent_multiverse_id else None,
            "fork_tick_index": multiverse.fork_tick_index,
            "depth": multiverse.depth,
            "branch_probability": _float_or_default(getattr(multiverse, "branch_probability", None), 1.0),
            "path_probability": _float_or_default(getattr(multiverse, "path_probability", None), 1.0),
        },
        "adjudication": _entry_adjudication(adjudication),
        "endpoint_ledger": _compact_ledger_payload(db, ledger, limit=limits["entries"]),
        "latest_ticks": _latest_ticks(db, multiverse=multiverse, limit=limits["ticks"]),
        "key_events": _key_events(db, multiverse=multiverse, limit=limits["events"]),
        "god_reviews": _god_reviews(db, multiverse=multiverse, limit=limits["reviews"]),
        "actor_state_counts": _actor_state_counts(multiverse),
    }


def _compact_ledger_payload(
    db: Session,
    ledger: models.EndpointLedgerVersion | None,
    *,
    limit: int,
) -> dict[str, Any]:
    payload = endpoint_ledger_report_payload(db, ledger)
    entries = payload.get("entries") or []
    return {
        "status": payload.get("status"),
        "ledger_version_id": payload.get("ledger_version_id"),
        "scope": payload.get("scope"),
        "version": payload.get("version"),
        "summary": payload.get("summary"),
        "histogram": payload.get("histogram", [])[:limit],
        "terminality_assessment": payload.get("terminality_assessment"),
        "contradiction_check": payload.get("contradiction_check"),
        "entries": [
            {
                "endpoint_key": entry.get("endpoint_key"),
                "label": entry.get("label"),
                "status": entry.get("status"),
                "probability": entry.get("probability"),
                "status_basis": entry.get("status_basis"),
                "authority_ref_count": len(entry.get("authority_refs") or []),
                "evidence_ref_count": len(entry.get("evidence_refs") or []),
                "negative_evidence_ref_count": len(entry.get("negative_evidence_refs") or []),
                "blockers": entry.get("blockers"),
            }
            for entry in entries[:limit]
        ],
    }


def _compact_adjudication(
    db: Session,
    adjudication: models.TimelineAdjudicationVersion | None,
    *,
    limit: int,
) -> dict[str, Any]:
    if adjudication is None:
        return {"status": "missing", "entries": []}
    entries = timeline_adjudication_entries(db, adjudication.id)
    return {
        "adjudication_version_id": str(adjudication.id),
        "version": adjudication.version,
        "status": adjudication.status,
        "summary": adjudication.summary,
        "payload": adjudication.payload or {},
        "entries": [
            {
                "ui_label": entry.ui_label,
                "viability_status": entry.viability_status,
                "include_in_final": entry.include_in_final,
                "original_path_probability": entry.original_path_probability,
                "effective_path_probability": entry.effective_path_probability,
                "mass_disposition": entry.mass_disposition,
                "endpoint_key": entry.endpoint_key,
                "endpoint_status": entry.endpoint_status,
                "prune_reason": entry.prune_reason,
            }
            for entry in entries[:limit]
        ],
    }


def _entry_adjudication(entry: models.TimelineAdjudicationEntry | None) -> dict[str, Any]:
    if entry is None:
        return {"status": "missing"}
    return {
        "viability_status": entry.viability_status,
        "include_in_final": entry.include_in_final,
        "prune_reason": entry.prune_reason,
        "original_path_probability": entry.original_path_probability,
        "effective_path_probability": entry.effective_path_probability,
        "mass_disposition": entry.mass_disposition,
    }


def _latest_ticks(db: Session, *, multiverse: models.Multiverse, limit: int) -> list[dict[str, Any]]:
    ticks = db.scalars(
        select(models.TickSnapshot)
        .where(models.TickSnapshot.multiverse_id == multiverse.id)
        .order_by(models.TickSnapshot.tick_index.desc())
        .limit(limit)
    ).all()
    return [
        {
            "tick_id": str(tick.id),
            "tick_index": tick.tick_index,
            "status": tick.status,
            "summary": tick.summary,
        }
        for tick in ticks
    ]


def _key_events(db: Session, *, multiverse: models.Multiverse, limit: int) -> list[dict[str, Any]]:
    events = db.scalars(
        select(models.Event)
        .where(models.Event.multiverse_id == multiverse.id)
        .order_by(models.Event.scheduled_tick.desc(), models.Event.created_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "event_id": str(event.id),
            "title": event.title,
            "event_type": event.event_type,
            "status": event.status,
            "scheduled_tick": event.scheduled_tick,
            "actual_impact": _compact_value(event.actual_impact or {}, max_items=5),
        }
        for event in events
    ]


def _god_reviews(db: Session, *, multiverse: models.Multiverse, limit: int) -> list[dict[str, Any]]:
    reviews = db.scalars(
        select(models.GodAgentReview)
        .where(models.GodAgentReview.multiverse_id == multiverse.id)
        .order_by(models.GodAgentReview.created_at.desc(), models.GodAgentReview.id.desc())
        .limit(limit)
    ).all()
    return [
        {
            "review_id": str(review.id),
            "decision": review.decision,
            "confidence": review.confidence,
            "rationale": review.rationale,
            "tick_snapshot_id": str(review.tick_snapshot_id) if review.tick_snapshot_id else None,
        }
        for review in reviews
    ]


def _actor_state_counts(multiverse: models.Multiverse) -> dict[str, int]:
    state = multiverse.state if isinstance(multiverse.state, dict) else {}
    return {
        "cohort_current_states": len(state.get("cohort_current_states") or []),
        "hero_current_states": len(state.get("hero_current_states") or []),
    }


def _report_inventory(db: Session, *, big_bang_id) -> list[dict[str, Any]]:
    reports = db.scalars(
        select(models.Report).where(models.Report.big_bang_id == big_bang_id).order_by(models.Report.report_type)
    ).all()
    return [
        {
            "report_id": str(report.id),
            "report_type": report.report_type,
            "status": report.status,
            "current_version": report.current_version,
            "multiverse_id": str(report.multiverse_id) if report.multiverse_id else None,
        }
        for report in reports
    ]


def _compact_value(value: Any, *, max_items: int) -> Any:
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                compact["_truncated"] = True
                break
            key_text = str(key)
            if _is_compact_internal_reference_key(key_text):
                continue
            if _is_compact_raw_text_key(key_text):
                compact[key_text] = {"present": bool(item)}
            else:
                compact[key_text] = _compact_value(item, max_items=max_items)
        return compact
    if isinstance(value, list):
        items = [_compact_value(item, max_items=max_items) for item in value[:max_items]]
        if len(value) > max_items:
            items.append({"_truncated_count": len(value) - max_items})
        return items
    if isinstance(value, str) and len(value) > 300:
        return value[:297].rstrip() + "..."
    return value


def _is_compact_raw_text_key(key: str) -> bool:
    normalized = key.lower()
    compact = normalized.replace("_", "").replace("-", "")
    return normalized in {"plain_text_corpus", "raw_text", "scenario_text"} or compact.endswith("corpus") or compact in {
        "scenariotext",
        "rawtext",
        "sourcetext",
        "plaintext",
        "initializerprompt",
        "systemprompt",
        "developerprompt",
        "userprompt",
        "fullprompt",
        "rawprompt",
    }


def _is_compact_internal_reference_key(key: str) -> bool:
    normalized = key.lower()
    compact = normalized.replace("_", "").replace("-", "")
    return (
        normalized
        in {
            "artifact_id",
            "llm_call_id",
            "endpoint_ledger_id",
            "report_version_id",
            "source_snapshot_id",
        }
        or compact.endswith("artifactid")
        or compact in {"llmcallid", "endpointledgerid", "reportversionid", "sourcesnapshotid"}
    )


def _float_or_default(value: Any, default: float) -> float:
    try:
        parsed = float(value if value is not None else default)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:
        return default
    return parsed
