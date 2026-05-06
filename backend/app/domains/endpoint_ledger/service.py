from __future__ import annotations

import json
import re
from collections import Counter
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models
from app.domains.multiverse.runtime_config import simulation_config_for_multiverse
from app.domains.tick.tick_bundles import TickBundleHydrationContext, hydrate_tick_bundle
from app.llm.audit import LLMCallError, complete_with_audit
from app.llm.routing import AuditedLLMRoute, ResolvedLLMRoute, resolve_audited_llm_route


ENDPOINT_STATUSES = {
    "active",
    "weakened",
    "eliminated",
    "realized",
    "unresolved",
    "process_only",
    "insufficient_ticks",
}
TERMINAL_ENTRY_STATUSES = {"realized"}
ENDPOINT_LEDGER_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "endpoint_key": {"type": "string"},
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                    "status": {"type": "string"},
                    "realization_criteria": {"type": "array"},
                    "authority_refs": {"type": "array"},
                    "evidence_refs": {"type": "array"},
                    "negative_evidence_refs": {"type": "array"},
                    "blockers": {"type": "array"},
                    "status_basis": {"type": "string"},
                    "contradiction_notes": {"type": "string"},
                    "rationale": {"type": "string"},
                    "last_observed_tick_index": {"type": "integer"},
                },
                "required": ["endpoint_key", "label", "status"],
                "additionalProperties": True,
            },
        },
    },
    "required": ["summary", "entries"],
    "additionalProperties": False,
}


def latest_endpoint_ledger(
    db: Session,
    *,
    big_bang_id,
    multiverse_id=None,
    scope: str | None = None,
) -> models.EndpointLedgerVersion | None:
    query = select(models.EndpointLedgerVersion).where(models.EndpointLedgerVersion.big_bang_id == big_bang_id)
    if scope is not None:
        query = query.where(models.EndpointLedgerVersion.scope == scope)
    if multiverse_id is None:
        query = query.where(models.EndpointLedgerVersion.multiverse_id.is_(None))
    else:
        query = query.where(models.EndpointLedgerVersion.multiverse_id == multiverse_id)
    return db.scalar(
        query.order_by(
            models.EndpointLedgerVersion.version.desc(),
            models.EndpointLedgerVersion.created_at.desc(),
            models.EndpointLedgerVersion.id.desc(),
        ).limit(1)
    )


def endpoint_ledger_entries(db: Session, ledger_version_id) -> list[models.EndpointLedgerEntry]:
    return list(
        db.scalars(
            select(models.EndpointLedgerEntry)
            .where(models.EndpointLedgerEntry.ledger_version_id == ledger_version_id)
            .order_by(models.EndpointLedgerEntry.probability.desc().nullslast(), models.EndpointLedgerEntry.endpoint_key)
        ).all()
    )


def endpoint_ledger_detail(db: Session, ledger: models.EndpointLedgerVersion) -> dict[str, Any]:
    return {
        "ledger": ledger,
        "entries": endpoint_ledger_entries(db, ledger.id),
    }


def endpoint_ledger_report_payload(
    db: Session,
    ledger: models.EndpointLedgerVersion | None,
) -> dict[str, Any]:
    if ledger is None:
        return {"status": "missing", "entries": []}
    entries = [
        {
            "endpoint_key": entry.endpoint_key,
            "label": entry.label,
            "description": entry.description,
            "status": entry.status,
            "realized": _realized_value(entry.status),
            "realization_criteria": entry.realization_criteria or [],
            "authority_refs": entry.authority_refs or [],
            "evidence_refs": entry.evidence_refs or [],
            "negative_evidence_refs": entry.negative_evidence_refs or [],
            "blockers": entry.blockers or [],
            "status_basis": entry.status_basis,
            "contradiction_notes": entry.contradiction_notes,
            "rationale": entry.rationale,
            "last_observed_tick_index": entry.last_observed_tick_index,
            "meta": entry.meta or {},
        }
        for entry in endpoint_ledger_entries(db, ledger.id)
    ]
    return {
        "ledger_version_id": str(ledger.id),
        "scope": ledger.scope,
        "version": ledger.version,
        "status": ledger.status,
        "source_type": ledger.source_type,
        "summary": ledger.summary,
        "payload": ledger.payload or {},
        "entries": entries,
        "histogram": _endpoint_histogram(entries),
        "terminality_assessment": _terminality_assessment(entries),
        "contradiction_check": _contradiction_check(entries),
    }


def latest_endpoint_ledger_prompt_payload(db: Session, *, big_bang_id, multiverse_id=None) -> dict[str, Any]:
    ledger = latest_endpoint_ledger(db, big_bang_id=big_bang_id, multiverse_id=multiverse_id, scope="multiverse")
    if ledger is None:
        ledger = latest_endpoint_ledger(db, big_bang_id=big_bang_id, scope="big_bang")
    payload = endpoint_ledger_report_payload(db, ledger)
    return {
        "status": payload.get("status"),
        "scope": payload.get("scope"),
        "version": payload.get("version"),
        "summary": payload.get("summary"),
        "entries": [
            {
                "endpoint_key": item.get("endpoint_key"),
                "label": item.get("label"),
                "status": item.get("status"),
                "realized": item.get("realized"),
                "blockers": item.get("blockers"),
                "last_observed_tick_index": item.get("last_observed_tick_index"),
            }
            for item in payload.get("entries", [])[:8]
        ],
    }


def seed_endpoint_ledger(
    db: Session,
    *,
    big_bang: models.BigBang,
    multiverse: models.Multiverse | None,
) -> models.EndpointLedgerVersion:
    scope = "multiverse" if multiverse is not None else "big_bang"
    existing = latest_endpoint_ledger(
        db,
        big_bang_id=big_bang.id,
        multiverse_id=multiverse.id if multiverse else None,
        scope=scope,
    )
    if existing is not None:
        return existing
    evidence = _collect_evidence(db, big_bang=big_bang, multiverse=multiverse)
    entries = _entries_from_evidence(evidence)
    return _create_ledger_version(
        db,
        big_bang_id=big_bang.id,
        multiverse_id=multiverse.id if multiverse else None,
        scope=scope,
        source_type="initializer_seed",
        created_by="initializer",
        summary="Seeded endpoint ledger from initialization evidence.",
        entries=entries,
        payload={"evidence": _compact_evidence(evidence)},
    )


def apply_god_endpoint_updates(
    db: Session,
    *,
    big_bang_id,
    multiverse_id,
    tick_snapshot_id,
    review_payload: dict[str, Any],
) -> models.EndpointLedgerVersion | None:
    raw_updates = review_payload.get("endpoint_ledger_updates")
    if not isinstance(raw_updates, list) or not raw_updates:
        return None
    latest = latest_endpoint_ledger(
        db,
        big_bang_id=big_bang_id,
        multiverse_id=multiverse_id,
        scope="multiverse",
    )
    base_entries = _entry_payloads(db, latest) if latest is not None else []
    big_bang = db.get(models.BigBang, big_bang_id)
    multiverse = db.get(models.Multiverse, multiverse_id)
    evidence = _collect_evidence(db, big_bang=big_bang, multiverse=multiverse) if big_bang and multiverse else None
    merged = _merge_entry_updates(base_entries, raw_updates, evidence=evidence)
    if _canonical_entries(base_entries) == _canonical_entries(merged):
        return latest
    ledger = _create_ledger_version(
        db,
        big_bang_id=big_bang_id,
        multiverse_id=multiverse_id,
        scope="multiverse",
        source_type="god_tick_review",
        source_tick_snapshot_id=tick_snapshot_id,
        parent_ledger_version_id=latest.id if latest else None,
        created_by="god_agent",
        summary=review_payload.get("endpoint_ledger_summary") or "God review updated endpoint ledger.",
        entries=merged,
        payload={"review_decision": review_payload.get("decision")},
    )
    return ledger


def evaluate_endpoint_ledger(
    db: Session,
    *,
    big_bang: models.BigBang,
    multiverse: models.Multiverse | None = None,
    source_type: str = "posthoc_evaluation",
    source_report_version_id=None,
    created_by: str = "endpoint_evaluator",
    use_llm: bool = True,
    candidate_endpoint: dict[str, Any] | None = None,
) -> models.EndpointLedgerVersion:
    scope = "multiverse" if multiverse is not None else "big_bang"
    latest = latest_endpoint_ledger(
        db,
        big_bang_id=big_bang.id,
        multiverse_id=multiverse.id if multiverse else None,
        scope=scope,
    )
    if multiverse is None:
        weighted = _weighted_entries_from_multiverse_ledgers(db, big_bang=big_bang)
        if weighted is not None:
            return _create_ledger_version(
                db,
                big_bang_id=big_bang.id,
                scope=scope,
                source_type=source_type,
                source_report_version_id=source_report_version_id,
                parent_ledger_version_id=latest.id if latest else None,
                created_by=created_by,
                summary="Weighted endpoint ledger aggregated from multiverse path probabilities.",
                entries=weighted["entries"],
                payload=weighted["payload"],
            )
    evidence = _collect_evidence(db, big_bang=big_bang, multiverse=multiverse)
    if candidate_endpoint:
        evidence["candidate_endpoint"] = candidate_endpoint
    entries = _entries_from_evidence(evidence, base_entries=_entry_payloads(db, latest))
    summary = "Endpoint ledger evaluated from current timeline evidence."
    llm_call = None
    llm_payload = None
    if use_llm:
        llm_payload, llm_call = _try_llm_endpoint_evaluation(
            db,
            big_bang_id=big_bang.id,
            scope=scope,
            evidence=evidence,
            base_entries=entries,
        )
        if llm_payload is not None:
            entries = _entries_from_llm_payload(llm_payload, fallback_entries=entries, evidence=evidence)
            summary = str(llm_payload.get("summary") or summary)
    return _create_ledger_version(
        db,
        big_bang_id=big_bang.id,
        multiverse_id=multiverse.id if multiverse else None,
        scope=scope,
        source_type=source_type,
        source_report_version_id=source_report_version_id,
        parent_ledger_version_id=latest.id if latest else None,
        created_by=created_by,
        summary=summary,
        model=llm_call.model if llm_call is not None else None,
        llm_call_id=llm_call.id if llm_call is not None else None,
        entries=entries,
        payload={"evidence": _compact_evidence(evidence), "llm_payload": llm_payload or {}},
    )


def attach_report_version_to_ledger(
    db: Session,
    *,
    ledger: models.EndpointLedgerVersion | None,
    report_version_id,
) -> None:
    if ledger is None:
        return
    ledger.source_report_version_id = report_version_id
    db.flush()


def _create_ledger_version(
    db: Session,
    *,
    big_bang_id,
    multiverse_id=None,
    scope: str,
    source_type: str,
    created_by: str,
    summary: str | None,
    entries: list[dict[str, Any]],
    source_tick_snapshot_id=None,
    source_report_version_id=None,
    parent_ledger_version_id=None,
    model: str | None = None,
    llm_call_id=None,
    payload: dict[str, Any] | None = None,
) -> models.EndpointLedgerVersion:
    if multiverse_id is None:
        db.execute(select(models.BigBang.id).where(models.BigBang.id == big_bang_id).with_for_update()).first()
    else:
        db.execute(select(models.Multiverse.id).where(models.Multiverse.id == multiverse_id).with_for_update()).first()
    max_version = db.scalar(
        select(func.max(models.EndpointLedgerVersion.version)).where(
            models.EndpointLedgerVersion.big_bang_id == big_bang_id,
            models.EndpointLedgerVersion.multiverse_id.is_(None)
            if multiverse_id is None
            else models.EndpointLedgerVersion.multiverse_id == multiverse_id,
            models.EndpointLedgerVersion.scope == scope,
        )
    )
    ledger = models.EndpointLedgerVersion(
        big_bang_id=big_bang_id,
        multiverse_id=multiverse_id,
        scope=scope,
        version=int(max_version or 0) + 1,
        status="completed",
        source_type=source_type,
        source_tick_snapshot_id=source_tick_snapshot_id,
        source_report_version_id=source_report_version_id,
        parent_ledger_version_id=parent_ledger_version_id,
        created_by=created_by,
        summary=summary,
        model=model,
        llm_call_id=llm_call_id,
        payload=payload or {},
    )
    db.add(ledger)
    db.flush()
    for entry_payload in _normalize_entries(entries):
        db.add(models.EndpointLedgerEntry(ledger_version_id=ledger.id, **entry_payload))
    db.flush()
    return ledger


def _entry_payloads(db: Session, ledger: models.EndpointLedgerVersion | None) -> list[dict[str, Any]]:
    if ledger is None:
        return []
    return [
        {
            "endpoint_key": entry.endpoint_key,
            "label": entry.label,
            "description": entry.description,
            "status": entry.status,
            "probability": entry.probability,
            "realization_criteria": entry.realization_criteria or [],
            "authority_refs": entry.authority_refs or [],
            "evidence_refs": entry.evidence_refs or [],
            "negative_evidence_refs": entry.negative_evidence_refs or [],
            "blockers": entry.blockers or [],
            "status_basis": entry.status_basis,
            "contradiction_notes": entry.contradiction_notes,
            "rationale": entry.rationale,
            "last_observed_tick_index": entry.last_observed_tick_index,
            "meta": entry.meta or {},
        }
        for entry in endpoint_ledger_entries(db, ledger.id)
    ]


def _collect_evidence(
    db: Session,
    *,
    big_bang: models.BigBang,
    multiverse: models.Multiverse | None,
) -> dict[str, Any]:
    multiverses = [multiverse] if multiverse is not None else db.scalars(
        select(models.Multiverse).where(models.Multiverse.big_bang_id == big_bang.id).order_by(models.Multiverse.ui_label)
    ).all()
    ticks: list[dict[str, Any]] = []
    hydration_context = TickBundleHydrationContext()
    for item in multiverses:
        rows = db.scalars(
            select(models.TickSnapshot)
            .where(models.TickSnapshot.multiverse_id == item.id)
            .order_by(models.TickSnapshot.tick_index.desc())
            .limit(8)
        ).all()
        for tick in rows:
            final = hydrate_tick_bundle(db, tick, "final_bundle", context=hydration_context)
            ticks.append(
                {
                    "tick_id": str(tick.id),
                    "multiverse_id": str(item.id),
                    "multiverse_label": item.ui_label,
                    "tick_index": tick.tick_index,
                    "status": tick.status,
                    "summary": tick.summary,
                    "branch_score": final.get("branch_score"),
                    "god_decision": (final.get("god_review") or {}).get("decision"),
                    "executed_events": final.get("executed_events", [])[:5],
                }
            )
    event_query = (
        select(models.Event)
        .where(models.Event.big_bang_id == big_bang.id)
        .order_by(models.Event.scheduled_tick.desc(), models.Event.created_at.desc())
        .limit(30)
    )
    if multiverse is not None:
        event_query = event_query.where(models.Event.multiverse_id == multiverse.id)
    events = [
        {
            "event_id": str(event.id),
            "multiverse_id": str(event.multiverse_id),
            "title": event.title,
            "event_type": event.event_type,
            "status": event.status,
            "scheduled_tick": event.scheduled_tick,
            "expected_impact": event.expected_impact or {},
            "actual_impact": event.actual_impact or {},
        }
        for event in db.scalars(event_query).all()
    ]
    review_query = (
        select(models.GodAgentReview)
        .where(models.GodAgentReview.big_bang_id == big_bang.id)
        .order_by(models.GodAgentReview.created_at.desc())
        .limit(20)
    )
    if multiverse is not None:
        review_query = review_query.where(models.GodAgentReview.multiverse_id == multiverse.id)
    reviews = [
        {
            "review_id": str(review.id),
            "multiverse_id": str(review.multiverse_id),
            "tick_snapshot_id": str(review.tick_snapshot_id) if review.tick_snapshot_id else None,
            "decision": review.decision,
            "confidence": review.confidence,
            "rationale": review.rationale,
        }
        for review in db.scalars(review_query).all()
    ]
    scenario = big_bang.scenario_input or {}
    config = db.scalar(
        select(models.BigBangConfig)
        .where(models.BigBangConfig.big_bang_id == big_bang.id)
        .order_by(models.BigBangConfig.version.desc())
        .limit(1)
    )
    if multiverse is not None:
        simulation_config = simulation_config_for_multiverse(db, multiverse)
    else:
        simulation_config = config.simulation_config if config is not None and isinstance(config.simulation_config, dict) else {}
    raw_initializer = scenario.get("initializer_output")
    initializer: dict[str, Any] = raw_initializer if isinstance(raw_initializer, dict) else {}
    candidate_endpoints = scenario.get("candidate_endpoints")
    if not isinstance(candidate_endpoints, list):
        candidate_endpoints = []
    forecast_metadata = scenario.get("forecast_metadata")
    if not isinstance(forecast_metadata, dict):
        forecast_metadata = {}
    multiverse_state = multiverse.state if multiverse is not None and isinstance(multiverse.state, dict) else {}
    return {
        "big_bang": {
            "id": str(big_bang.id),
            "name": big_bang.name,
            "status": big_bang.status,
            "scenario_input": _compact_value(scenario, max_items=8),
            "simulation_config": _compact_value(simulation_config, max_items=8),
        },
        "scenario_candidate_endpoints": candidate_endpoints,
        "forecast_metadata": forecast_metadata,
        "forecast_source_guidance": _forecast_source_guidance(scenario, initializer),
        "scope": "multiverse" if multiverse is not None else "big_bang",
        "multiverse": {
            "id": str(multiverse.id),
            "ui_label": multiverse.ui_label,
            "status": multiverse.status,
            "state": _compact_value(multiverse_state, max_items=10),
        }
        if multiverse is not None
        else None,
        "initializer": {
            "important_questions": initializer.get("important_questions") or multiverse_state.get("important_questions") or [],
            "endpoint_ledger": initializer.get("endpoint_ledger") or multiverse_state.get("endpoint_ledger") or [],
            "branch_hypotheses": initializer.get("branch_hypotheses") or multiverse_state.get("branch_hypotheses") or [],
            "risk_flags": initializer.get("risk_flags") or [],
            "known_uncertainty": _extract_known_uncertainties(scenario, initializer, multiverse_state),
        },
        "ticks": ticks,
        "events": events,
        "god_reviews": reviews,
        "timeline_statuses": dict(Counter(item.status for item in multiverses)),
    }


def _entries_from_evidence(
    evidence: dict[str, Any],
    *,
    base_entries: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    entries = {entry["endpoint_key"]: dict(entry) for entry in base_entries or [] if entry.get("endpoint_key")}
    forecast_metadata = evidence.get("forecast_metadata") if isinstance(evidence.get("forecast_metadata"), dict) else {}
    scenario_candidates = [
        item
        for item in evidence.get("scenario_candidate_endpoints") or []
        if isinstance(item, dict) and (item.get("id") or item.get("endpoint_key") or item.get("label"))
    ]
    for item in scenario_candidates:
        candidate_id = str(item.get("id") or item.get("endpoint_key") or item.get("label")).strip()
        endpoint_key = _endpoint_key(candidate_id)
        label = item.get("label") or item.get("description") or candidate_id
        existing = entries.get(endpoint_key, {})
        meta = existing.get("meta") if isinstance(existing.get("meta"), dict) else {}
        entries[endpoint_key] = {
            **existing,
            "endpoint_key": endpoint_key,
            "label": _label_from_text(label),
            "description": item.get("description") or str(label),
            "status": str(existing.get("status") or item.get("status") or "active").lower(),
            "realization_criteria": _list_value(item.get("realization_criteria"))
            or [
                f"Resolve candidate endpoint {candidate_id} using the forecast question, deadline, and official settlement evidence.",
            ],
            "authority_refs": _list_value(item.get("authority_refs")) or ["forecast_card"],
            "evidence_refs": [
                {"source": "scenario_candidate_endpoint", "candidate_endpoint_id": candidate_id},
                *_list_value(item.get("evidence_refs")),
            ],
            "negative_evidence_refs": _list_value(item.get("negative_evidence_refs")),
            "blockers": _list_value(existing.get("blockers") or item.get("blockers")),
            "status_basis": existing.get("status_basis") or "scenario_candidate_endpoint",
            "contradiction_notes": existing.get("contradiction_notes")
            or "Auxiliary mechanism endpoints must not override this primary yes/no candidate.",
            "rationale": existing.get("rationale") or "Preserved from the benchmark card candidate endpoints.",
            "last_observed_tick_index": _optional_int(existing.get("last_observed_tick_index")),
            "meta": {
                **meta,
                "source": "scenario_candidate_endpoint",
                "endpoint_role": "primary_candidate",
                "candidate_endpoint_id": candidate_id.lower(),
                "candidate_endpoint_role": candidate_id.lower(),
                "forecast_deadline_date": forecast_metadata.get("forecast_deadline_date"),
                "as_of_date": forecast_metadata.get("as_of_date"),
            },
        }
    has_primary_candidates = bool(scenario_candidates)
    for item in evidence.get("initializer", {}).get("endpoint_ledger") or []:
        if not isinstance(item, dict):
            continue
        key = item.get("endpoint_key") or item.get("key") or item.get("label")
        if not key:
            continue
        endpoint_key = _endpoint_key(key)
        payload = dict(item)
        payload["endpoint_key"] = endpoint_key
        payload.setdefault("label", _label_from_text(key))
        payload.setdefault("status", "active")
        payload.setdefault("realization_criteria", [f"Observable evidence confirms {payload.get('label')}."])
        payload.setdefault("evidence_refs", [{"source": "initializer", "kind": "endpoint_ledger"}])
        payload.setdefault("status_basis", "initializer_endpoint_ledger")
        payload.setdefault("contradiction_notes", "Track later evidence that supports, weakens, eliminates, or realizes this endpoint.")
        payload.setdefault("rationale", "Preserved from initializer endpoint ledger.")
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
        payload["meta"] = {"source": "initializer_endpoint_ledger", **meta}
        if has_primary_candidates and payload["endpoint_key"] not in entries:
            payload["status"] = "process_only"
            payload["meta"] = {**payload["meta"], "endpoint_role": "auxiliary_mechanism"}
        entries.setdefault(endpoint_key, payload)
    for item in evidence.get("initializer", {}).get("branch_hypotheses") or []:
        if not isinstance(item, dict):
            continue
        text = item.get("alternate_path") or item.get("plausible_alternate_path") or item.get("trigger") or item.get("label")
        if not text:
            continue
        key = _endpoint_key(text)
        entries.setdefault(
            key,
            {
                "endpoint_key": key,
                "label": _label_from_text(text),
                "description": item.get("trigger") or item.get("observable_divergence_signal") or str(text),
                "status": "active",
                "realization_criteria": _list_value(item.get("realization_criteria"))
                or [f"Observable evidence confirms {text}."],
                "authority_refs": _refs_from_value(item, keys=("authority", "decision_authority", "actor")),
                "evidence_refs": [{"source": "initializer", "kind": "branch_hypothesis"}],
                "negative_evidence_refs": [],
                "blockers": [],
                "status_basis": "initializer_branch_hypothesis",
                "contradiction_notes": "No contradiction pass has eliminated this endpoint.",
                "rationale": "Preserved from initialization branch hypothesis.",
                "last_observed_tick_index": None,
                "meta": {
                    "source": "initializer_branch_hypothesis",
                    "endpoint_role": "auxiliary_mechanism" if has_primary_candidates else "candidate_mechanism",
                },
            },
        )
    for item in evidence.get("initializer", {}).get("known_uncertainty") or []:
        text = item if isinstance(item, str) else json.dumps(item, sort_keys=True, default=str)
        key = _endpoint_key(text)
        entries.setdefault(
            key,
            {
                "endpoint_key": key,
                "label": _label_from_text(text),
                "description": text,
                "status": "active",
                "realization_criteria": [f"Later evidence resolves uncertainty: {text}"],
                "authority_refs": [],
                "evidence_refs": [{"source": "initializer", "kind": "known_uncertainty"}],
                "negative_evidence_refs": [],
                "blockers": ["uncertainty unresolved"],
                "status_basis": "known_uncertainty",
                "contradiction_notes": "Track whether later evidence resolves this uncertainty.",
                "rationale": "Scenario identified this as an endpoint-relevant uncertainty.",
                "last_observed_tick_index": None,
                "meta": {
                    "source": "known_uncertainty",
                    "endpoint_role": "auxiliary_mechanism" if has_primary_candidates else "candidate_mechanism",
                },
            },
        )
    for event in evidence.get("events") or []:
        actual = event.get("actual_impact") if isinstance(event.get("actual_impact"), dict) else {}
        endpoint = actual.get("endpoint") or actual.get("outcome") or actual.get("result")
        if endpoint:
            key = _endpoint_key(endpoint)
            existing = entries.get(key, {})
            existing_meta = existing.get("meta") if isinstance(existing.get("meta"), dict) else {}
            entries[key] = {
                **existing,
                "endpoint_key": key,
                "label": existing.get("label") or _label_from_text(endpoint),
                "description": str(endpoint),
                "status": "realized" if event.get("status") == "executed" else "active",
                "probability": 0.75 if event.get("status") == "executed" else None,
                "realization_criteria": [f"Executed event '{event.get('title')}' produced endpoint evidence."],
                "authority_refs": [],
                "evidence_refs": [{"source": "event", "event_id": event.get("event_id"), "title": event.get("title")}],
                "negative_evidence_refs": [],
                "blockers": [],
                "status_basis": "event_actual_impact",
                "contradiction_notes": "Verify no later event reverses this endpoint evidence.",
                "rationale": f"Event actual_impact named endpoint after status={event.get('status')}.",
                "last_observed_tick_index": event.get("scheduled_tick"),
                "meta": {**existing_meta, "source": "event_actual_impact"},
            }
    candidate = evidence.get("candidate_endpoint")
    if isinstance(candidate, dict):
        raw_candidate_id = candidate.get("id") or candidate.get("endpoint_key")
        label = candidate.get("label") or raw_candidate_id or candidate.get("description") or "Candidate endpoint"
        key = _endpoint_key(raw_candidate_id or label)
        candidate_id = str(raw_candidate_id or key).strip().lower()
        existing = entries.get(key, {})
        existing_meta = existing.get("meta") if isinstance(existing.get("meta"), dict) else {}
        primary_candidate_meta = (
            {
                "endpoint_role": "primary_candidate",
                "candidate_endpoint_id": candidate_id,
                "candidate_endpoint_role": candidate_id,
            }
            if candidate_id in {"yes", "no"}
            else {}
        )
        entries[key] = {
            **existing,
            "endpoint_key": key,
            "label": existing.get("label") or _label_from_text(label),
            "description": candidate.get("description") or candidate.get("rationale"),
            "status": str(candidate.get("status") or "active").lower(),
            "probability": candidate.get("probability"),
            "realization_criteria": _list_value(candidate.get("realization_criteria")),
            "authority_refs": _list_value(candidate.get("authority_refs")),
            "evidence_refs": [
                {"source": "posthoc_candidate", "label": candidate.get("label") or key, "candidate_endpoint_id": candidate_id},
                *_list_value(candidate.get("evidence_refs")),
            ],
            "negative_evidence_refs": _list_value(candidate.get("negative_evidence_refs")),
            "blockers": _list_value(candidate.get("blockers")),
            "status_basis": candidate.get("status_basis") or "posthoc_candidate",
            "contradiction_notes": candidate.get("contradiction_notes")
            or "Posthoc candidate requires comparison against existing timeline evidence.",
            "rationale": candidate.get("rationale") or "Added through post-simulation endpoint evaluation.",
            "last_observed_tick_index": _optional_int(candidate.get("last_observed_tick_index")),
            "meta": {**existing_meta, "source": "posthoc_candidate", **primary_candidate_meta},
        }
    if not entries:
        latest_tick_index = max((int(tick.get("tick_index") or 0) for tick in evidence.get("ticks") or []), default=None)
        entries["endpoint_unresolved"] = {
            "endpoint_key": "endpoint_unresolved",
            "label": "Endpoint unresolved",
            "description": "No explicit terminal endpoint has been realized in the available evidence.",
            "status": "unresolved",
            "realization_criteria": ["A later authority decision or executed terminal event names a terminal endpoint."],
            "authority_refs": [],
            "evidence_refs": [{"source": "timeline", "kind": "absence_of_terminal_endpoint"}],
            "negative_evidence_refs": [],
            "blockers": ["No realized endpoint evidence"],
            "status_basis": "absence_of_terminal_endpoint",
            "contradiction_notes": "A later authority decision or executed terminal event could resolve this.",
            "rationale": "Fallback ledger entry because the evidence contains process movement but no explicit endpoint.",
            "last_observed_tick_index": latest_tick_index,
            "meta": {"source": "fallback"},
        }
    return _finalize_entries(list(entries.values()), evidence=evidence)


def _weighted_entries_from_multiverse_ledgers(
    db: Session,
    *,
    big_bang: models.BigBang,
) -> dict[str, Any] | None:
    multiverses = db.scalars(
        select(models.Multiverse)
        .where(models.Multiverse.big_bang_id == big_bang.id)
        .order_by(models.Multiverse.ui_label)
    ).all()
    if not multiverses:
        return None

    ledger_rows: list[tuple[models.Multiverse, models.EndpointLedgerVersion, list[models.EndpointLedgerEntry]]] = []
    for multiverse in multiverses:
        ledger = latest_endpoint_ledger(
            db,
            big_bang_id=big_bang.id,
            multiverse_id=multiverse.id,
            scope="multiverse",
        )
        if ledger is None:
            ledger = evaluate_endpoint_ledger(
                db,
                big_bang=big_bang,
                multiverse=multiverse,
                source_type="weighted_big_bang_seed",
                created_by="endpoint_weight_aggregator",
                use_llm=False,
            )
        entries = endpoint_ledger_entries(db, ledger.id)
        if entries:
            ledger_rows.append((multiverse, ledger, entries))
    if not ledger_rows:
        return None

    from app.domains.report.adjudication import latest_timeline_adjudication_map

    adjudication_by_multiverse_id = latest_timeline_adjudication_map(db, big_bang_id=big_bang.id)
    raw_weights = [
        _effective_multiverse_path_probability(multiverse, adjudication_by_multiverse_id)
        for multiverse, _ledger, _entries in ledger_rows
    ]
    weight_total = sum(raw_weights)
    excluded_mass = sum(
        float(entry.original_path_probability or 0.0)
        for entry in adjudication_by_multiverse_id.values()
        if not entry.include_in_final
    )
    if weight_total <= 0 and adjudication_by_multiverse_id:
        return {
            "entries": [
                {
                    "endpoint_key": "endpoint_pruned",
                    "label": "Endpoint pruned",
                    "description": "All timeline path mass was excluded by timeline adjudication.",
                    "status": "unresolved",
                    "probability": 1.0,
                    "realization_criteria": ["At least one usable timeline survives adjudication."],
                    "authority_refs": [],
                    "evidence_refs": [
                        {
                            "source": "timeline_adjudication",
                            "excluded_path_probability_mass": round(excluded_mass, 10),
                        }
                    ],
                    "negative_evidence_refs": [],
                    "blockers": ["All timelines pruned from final aggregation"],
                    "status_basis": "timeline_adjudication_all_pruned",
                    "contradiction_notes": None,
                    "rationale": "No usable effective path mass remained after timeline adjudication.",
                    "last_observed_tick_index": None,
                    "meta": {"source": "timeline_adjudication"},
                }
            ],
            "payload": {
                "aggregation": "path_probability_weighted",
                "path_probability_mass": 0.0,
                "excluded_path_probability_mass": round(excluded_mass, 10),
                "path_probability_distribution": [],
                "source_multiverse_count": len(ledger_rows),
                "adjudication_applied": True,
            },
        }
    if weight_total <= 0:
        normalized_weights = [1.0 / len(raw_weights)] * len(raw_weights)
    else:
        normalized_weights = [weight / weight_total for weight in raw_weights]

    aggregated: dict[str, dict[str, Any]] = {}
    path_distribution = []
    for (multiverse, ledger, entries), timeline_weight, raw_weight in zip(ledger_rows, normalized_weights, raw_weights, strict=True):
        adjudication = adjudication_by_multiverse_id.get(str(multiverse.id))
        path_distribution.append(
            {
                "multiverse_id": str(multiverse.id),
                "ui_label": multiverse.ui_label,
                "status": multiverse.status,
                "path_probability": round(raw_weight, 10),
                "original_path_probability": round(_multiverse_path_probability(multiverse), 10),
                "normalized_weight": round(timeline_weight, 10),
                "ledger_version_id": str(ledger.id),
                "viability_status": adjudication.viability_status if adjudication else "unadjudicated",
                "include_in_final": adjudication.include_in_final if adjudication else True,
                "prune_reason": adjudication.prune_reason if adjudication else None,
            }
        )
        timeline_had_representative = False
        for entry in entries:
            if entry.status == "eliminated":
                continue
            representative = _representative_status_for_weighting(entry.status, multiverse.status)
            if representative is None:
                continue
            contribution = timeline_weight
            timeline_had_representative = True
            entry_meta = entry.meta if isinstance(entry.meta, dict) else {}
            target = aggregated.setdefault(
                entry.endpoint_key,
                {
                    "endpoint_key": entry.endpoint_key,
                    "label": entry.label,
                    "description": entry.description,
                    "status_weights": Counter(),
                    "path_mass": 0.0,
                    "realization_criteria": [],
                    "authority_refs": [],
                    "evidence_refs": [],
                    "negative_evidence_refs": [],
                    "blockers": set(),
                    "status_basis": [],
                    "contradiction_notes": [],
                    "rationale": [],
                    "last_observed_tick_index": entry.last_observed_tick_index,
                    "endpoint_role": entry_meta.get("endpoint_role"),
                    "candidate_endpoint_id": entry_meta.get("candidate_endpoint_id"),
                },
            )
            if not target.get("endpoint_role") and entry_meta.get("endpoint_role"):
                target["endpoint_role"] = entry_meta.get("endpoint_role")
            if not target.get("candidate_endpoint_id") and entry_meta.get("candidate_endpoint_id"):
                target["candidate_endpoint_id"] = entry_meta.get("candidate_endpoint_id")
            target["path_mass"] += contribution
            target["status_weights"][representative] += contribution
            target["evidence_refs"].append(
                {
                    "source": "multiverse_ledger",
                    "multiverse_id": str(multiverse.id),
                    "ui_label": multiverse.ui_label,
                    "ledger_version_id": str(ledger.id),
                    "path_weight": round(timeline_weight, 10),
                    "endpoint_status": entry.status,
                }
            )
            target["authority_refs"].extend(entry.authority_refs or [])
            target["realization_criteria"].extend(entry.realization_criteria or [])
            target["negative_evidence_refs"].extend(entry.negative_evidence_refs or [])
            target["blockers"].update(str(item) for item in (entry.blockers or []))
            if entry.status_basis:
                target["status_basis"].append(f"{multiverse.ui_label}: {entry.status_basis}")
            if entry.contradiction_notes:
                target["contradiction_notes"].append(f"{multiverse.ui_label}: {entry.contradiction_notes}")
            if entry.rationale:
                target["rationale"].append(f"{multiverse.ui_label}: {entry.rationale}")
            if entry.last_observed_tick_index is not None:
                current = target.get("last_observed_tick_index")
                target["last_observed_tick_index"] = max(current or 0, int(entry.last_observed_tick_index))

        if not timeline_had_representative and timeline_weight > 0:
            contribution = timeline_weight
            target = aggregated.setdefault(
                "endpoint_insufficient_ticks",
                {
                    "endpoint_key": "endpoint_insufficient_ticks",
                    "label": "Insufficient ticks",
                    "description": "Timeline stopped without enough ticks to resolve a terminal endpoint.",
                    "status_weights": Counter(),
                    "path_mass": 0.0,
                    "realization_criteria": ["A retained source timeline resolves its terminal endpoint before the tick limit."],
                    "authority_refs": [],
                    "evidence_refs": [],
                    "negative_evidence_refs": [],
                    "blockers": set(),
                    "status_basis": ["Retained timeline ended without realized endpoint evidence."],
                    "contradiction_notes": [],
                    "rationale": [],
                    "last_observed_tick_index": None,
                },
            )
            target["path_mass"] += contribution
            target["status_weights"]["insufficient_ticks"] += contribution
            target["evidence_refs"].append(
                {
                    "source": "path_mass_insufficient_ticks",
                    "multiverse_id": str(multiverse.id),
                    "ui_label": multiverse.ui_label,
                    "ledger_version_id": str(ledger.id),
                    "path_weight": round(timeline_weight, 10),
                }
            )

    entries = [_weighted_entry_payload(item) for item in aggregated.values()]
    return {
        "entries": entries,
        "payload": {
            "aggregation": "path_mass_by_endpoint_status",
            "path_probability_mass": round(weight_total, 10),
            "excluded_path_probability_mass": round(excluded_mass, 10),
            "path_probability_distribution": path_distribution,
            "endpoint_path_mass_distribution": _endpoint_path_mass_distribution(entries),
            "plot_distribution": _plot_distribution(entries),
            "source_multiverse_count": len(ledger_rows),
            "adjudication_applied": bool(adjudication_by_multiverse_id),
        },
    }


def _weighted_entry_payload(item: dict[str, Any]) -> dict[str, Any]:
    status_weights: Counter = item.pop("status_weights")
    status = status_weights.most_common(1)[0][0] if status_weights else "active"
    blockers = sorted(item.pop("blockers"))
    status_basis = " | ".join(item.pop("status_basis")[:6]) or "path_probability_weighted"
    contradiction_notes = " | ".join(item.pop("contradiction_notes")[:6]) or None
    rationale = " | ".join(item.pop("rationale")[:6]) or "Weighted from multiverse path probabilities."
    return {
        "endpoint_key": item["endpoint_key"],
        "label": item["label"],
        "description": item.get("description"),
        "status": status,
        "probability": None,
        "realization_criteria": item.get("realization_criteria") or [],
        "authority_refs": item.get("authority_refs") or [],
        "evidence_refs": item.get("evidence_refs") or [],
        "negative_evidence_refs": item.get("negative_evidence_refs") or [],
        "blockers": blockers,
        "status_basis": status_basis,
        "contradiction_notes": contradiction_notes,
        "rationale": rationale,
        "last_observed_tick_index": item.get("last_observed_tick_index"),
        "meta": {
            "source": "path_mass_by_endpoint_status",
            "path_mass": round(float(item.get("path_mass") or 0.0), 10),
            "status_path_masses": {key: round(float(value), 10) for key, value in status_weights.items()},
            "endpoint_role": item.get("endpoint_role"),
            "candidate_endpoint_id": item.get("candidate_endpoint_id"),
        },
    }


def _representative_status_for_weighting(endpoint_status: str, multiverse_status: str | None) -> str | None:
    if endpoint_status == "realized":
        return "realized"
    if endpoint_status == "insufficient_ticks":
        return "insufficient_ticks"
    if endpoint_status in {"active", "weakened", "unresolved", "process_only"} and str(multiverse_status or "").lower() in {
        "completed",
        "terminated",
    }:
        return "insufficient_ticks"
    if endpoint_status in {"active", "weakened", "unresolved", "process_only"}:
        return "unresolved"
    return None


def _endpoint_path_mass_distribution(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "endpoint_key": entry.get("endpoint_key"),
            "label": entry.get("label"),
            "endpoint_role": (entry.get("meta") or {}).get("endpoint_role"),
            "candidate_endpoint_id": (entry.get("meta") or {}).get("candidate_endpoint_id"),
            "status": entry.get("status"),
            "realized": _realized_value(str(entry.get("status") or "")),
            "path_mass": (entry.get("meta") or {}).get("path_mass", 0.0),
            "status_path_masses": (entry.get("meta") or {}).get("status_path_masses", {}),
        }
        for entry in sorted(
            entries,
            key=lambda item: float((item.get("meta") or {}).get("path_mass") or 0.0),
            reverse=True,
        )
    ]


def _plot_distribution(entries: list[dict[str, Any]]) -> dict[str, Any]:
    rows = _endpoint_path_mass_distribution(entries)
    return {
        "x": [row["label"] for row in rows],
        "series": [
            {
                "name": status,
                "values": [
                    float((row.get("status_path_masses") or {}).get(status) or 0.0)
                    for row in rows
                ],
            }
            for status in ("realized", "insufficient_ticks", "unresolved", "eliminated")
        ],
        "rows": rows,
    }


def _multiverse_path_probability(multiverse: models.Multiverse) -> float:
    value = getattr(multiverse, "path_probability", None)
    try:
        parsed = float(value if value is not None else 1.0)
    except (TypeError, ValueError):
        parsed = 1.0
    if parsed != parsed:
        return 1.0
    return max(0.0, min(1.0, parsed))


def _effective_multiverse_path_probability(
    multiverse: models.Multiverse,
    adjudication_by_multiverse_id: dict[str, Any],
) -> float:
    adjudication = adjudication_by_multiverse_id.get(str(multiverse.id))
    if adjudication is None:
        return _multiverse_path_probability(multiverse)
    try:
        return max(0.0, min(1.0, float(adjudication.effective_path_probability or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def _try_llm_endpoint_evaluation(
    db: Session,
    *,
    big_bang_id,
    scope: str,
    evidence: dict[str, Any],
    base_entries: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, models.LLMCall | None]:
    route = _endpoint_ledger_route(db)
    if route.primary.provider == "deterministic":
        return None, None
    prompt = {
        "scope": scope,
        "existing_entries": _compact_value(base_entries, max_items=12),
        "evidence": _compact_evidence(evidence),
        "instructions": [
            "Track endpoint states, not process moves.",
            "Use statuses active, weakened, eliminated, realized, unresolved, or process_only.",
            "Use insufficient_ticks when a final tick limit stops the timeline before a terminal endpoint resolves.",
            "Use eliminated only when the endpoint is impossible from hard evidence or final-horizon God review, not merely because it has not happened yet.",
            "Weight authority decisions over social noise.",
            "Return stable endpoint keys and statuses. Do not assign per-endpoint probabilities.",
            "When scenario_candidate_endpoints contains primary yes/no endpoints, resolve those explicit binary candidates before auxiliary mechanisms.",
            "At the forecast deadline, settle yes/no from simulated path evidence and the original forecast-card source packet.",
            "Realize no only when the simulated path says the deadline passed without the event or an authoritative delay/miss occurred; absence of direct observed proof alone is not enough.",
            "Auxiliary mechanism endpoints must not keep a binary forecast unresolved after the yes/no candidate has settled.",
            "For every endpoint, include realization_criteria, authority_refs, evidence_refs, negative_evidence_refs, and status_basis.",
            "Downgrade unsupported or process-only entries instead of leaving them as active terminal endpoints.",
        ],
    }
    try:
        response, call = complete_with_audit(
            db,
            big_bang_id=big_bang_id,
            purpose=f"endpoint_ledger_evaluation_{scope}",
            model=route.primary.model,
            route=AuditedLLMRoute.ENDPOINT_LEDGER,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the WorldFork endpoint ledger evaluator. Return JSON only. "
                        "Create or update endpoint ledger entries from the supplied simulation evidence. "
                        "Do not treat audits, reviews, pauses, negotiations, or messaging as terminal endpoints "
                        "unless authority evidence shows the endpoint is resolved."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(prompt, ensure_ascii=True, sort_keys=True, default=str),
                },
            ],
            json_schema=ENDPOINT_LEDGER_JSON_SCHEMA,
            metadata={"max_tokens": 1600, "temperature": 0.15, "agent_type": "endpoint_ledger_evaluator"},
        )
    except (LLMCallError, ValueError):
        return None, None
    parsed = response.parsed if isinstance(response.parsed, dict) else None
    return parsed, call


def _entries_from_llm_payload(
    payload: dict[str, Any],
    *,
    fallback_entries: list[dict[str, Any]],
    evidence: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    raw_entries = payload.get("entries")
    if raw_entries is None:
        raw_entries = payload.get("endpoint_ledger")
    if raw_entries is None:
        raw_entries = payload.get("ledger_entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        return fallback_entries
    return _merge_entry_updates(fallback_entries, raw_entries, evidence=evidence)


def _merge_entry_updates(
    base_entries: list[dict[str, Any]],
    updates: list[Any],
    *,
    evidence: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    merged = {entry.get("endpoint_key"): dict(entry) for entry in base_entries if entry.get("endpoint_key")}
    for update in updates:
        if not isinstance(update, dict):
            continue
        key = update.get("endpoint_key") or update.get("key") or update.get("label")
        if not key:
            continue
        endpoint_key = _endpoint_key(key)
        current = merged.get(endpoint_key, {"endpoint_key": endpoint_key, "label": _label_from_text(str(key))})
        existing_meta = current.get("meta") if isinstance(current.get("meta"), dict) else {}
        update_meta = update.get("meta") if isinstance(update.get("meta"), dict) else {}
        current.update({k: v for k, v in update.items() if k != "meta" and v not in (None, "")})
        current["endpoint_key"] = endpoint_key
        current.setdefault("label", _label_from_text(str(key)))
        if existing_meta or update_meta:
            current["meta"] = {**existing_meta, **update_meta}
        merged[endpoint_key] = current
    return _finalize_entries(list(merged.values()), evidence=evidence)


def _normalize_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        key = _endpoint_key(item.get("endpoint_key") or item.get("label") or item.get("description") or "endpoint")
        if key in seen:
            continue
        seen.add(key)
        status = str(item.get("status") or "active").lower()
        if status not in ENDPOINT_STATUSES:
            status = "active"
        realization_criteria = _list_value(item.get("realization_criteria"))
        if not realization_criteria:
            realization_criteria = [f"Observable evidence confirms {item.get('label') or key}."]
        authority_refs = _list_value(item.get("authority_refs"))
        evidence_refs = _list_value(item.get("evidence_refs"))
        negative_evidence_refs = _list_value(item.get("negative_evidence_refs"))
        blockers = _list_value(item.get("blockers"))
        status_basis = _truncate(str(item.get("status_basis") or ""), 2000) or None
        if not evidence_refs and status not in {"eliminated", "unresolved"}:
            status = "process_only"
            blockers = [*blockers, "supporting evidence missing"]
            status_basis = status_basis or "downgraded_missing_supporting_evidence"
        if status == "realized" and not authority_refs:
            status = "active"
            blockers = [*blockers, "authority evidence missing for realized endpoint"]
            status_basis = status_basis or "downgraded_realized_without_authority_evidence"
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        if item.get("probability") is not None:
            meta = {**meta, "legacy_probability_ignored": item.get("probability")}
        normalized.append(
            {
                "endpoint_key": key,
                "label": _truncate(str(item.get("label") or _label_from_text(key)), 240),
                "description": _truncate(str(item.get("description") or ""), 2000) or None,
                "status": status,
                "probability": None,
                "realization_criteria": realization_criteria,
                "authority_refs": authority_refs,
                "evidence_refs": evidence_refs,
                "negative_evidence_refs": negative_evidence_refs,
                "blockers": blockers,
                "status_basis": status_basis,
                "contradiction_notes": _truncate(str(item.get("contradiction_notes") or ""), 2000) or None,
                "rationale": _truncate(str(item.get("rationale") or ""), 2000) or None,
                "last_observed_tick_index": _optional_int(item.get("last_observed_tick_index")),
                "meta": meta,
            }
        )
    return normalized


def _finalize_entries(entries: list[dict[str, Any]], *, evidence: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    entries = _normalize_entries(entries)
    if evidence is not None:
        entries = _settle_primary_binary_candidates_from_terminal_event(entries, evidence=evidence)
        entries = _settle_primary_binary_candidates_from_source_packet_baseline(entries, evidence=evidence)
        entries = _settle_primary_binary_candidates_from_counterpart(entries, evidence=evidence)
    if _final_horizon_reached(evidence):
        entries = _settle_primary_binary_candidates_at_final_horizon(entries, evidence=evidence)
        entries = [_mark_insufficient_ticks(entry, evidence=evidence) for entry in entries]
    elif evidence is not None:
        entries = [_revert_final_horizon_overlay(entry) for entry in entries]
    return sorted(entries, key=lambda item: (item.get("status") != "eliminated", item.get("label") or ""), reverse=True)


def _assign_probabilities(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Backward-compatible alias: endpoint entries no longer carry probabilities."""
    return _finalize_entries(entries)


def _final_horizon_reached(evidence: dict[str, Any] | None) -> bool:
    if not evidence:
        return False
    max_ticks = (((evidence.get("big_bang") or {}).get("simulation_config") or {}).get("max_ticks"))
    if max_ticks is None:
        return False
    latest_tick = max((int(tick.get("tick_index") or 0) for tick in evidence.get("ticks") or []), default=None)
    if latest_tick is None or latest_tick < int(max_ticks):
        return False
    if _deadline_aware_binary_forecast(evidence):
        return True
    multiverse = evidence.get("multiverse") or {}
    return str(multiverse.get("status") or "").lower() in {"completed", "terminated"}


def _settle_primary_binary_candidates_at_final_horizon(
    entries: list[dict[str, Any]],
    *,
    evidence: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not _deadline_aware_binary_forecast(evidence):
        return entries
    by_candidate: dict[str, dict[str, Any]] = {}
    for entry in entries:
        meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
        if meta.get("endpoint_role") != "primary_candidate":
            continue
        candidate_id = str(meta.get("candidate_endpoint_id") or entry.get("endpoint_key") or "").lower()
        if candidate_id in {"yes", "no"}:
            by_candidate[candidate_id] = entry
    if not {"yes", "no"}.issubset(by_candidate):
        return entries

    yes = by_candidate["yes"]
    no = by_candidate["no"]
    if _has_terminal_event_settlement(yes) or _has_terminal_event_settlement(no) or _has_source_packet_baseline_settlement(yes) or _has_source_packet_baseline_settlement(no):
        return entries
    if yes.get("status") == "realized":
        replacement = {
            "yes": _mark_candidate_deadline_settlement(yes, status="realized", evidence=evidence),
            "no": _mark_candidate_deadline_settlement(
                no,
                status="eliminated",
                evidence=evidence,
                rationale="The yes candidate was realized by the forecast deadline, so the no candidate is eliminated.",
            ),
        }
    elif no.get("status") == "realized":
        replacement = {
            "yes": _mark_candidate_deadline_settlement(
                yes,
                status="eliminated",
                evidence=evidence,
                rationale="The no candidate was realized by the forecast deadline, so the yes candidate is eliminated.",
            ),
            "no": _mark_candidate_deadline_settlement(no, status="realized", evidence=evidence),
        }
    else:
        return entries

    settled: list[dict[str, Any]] = []
    for entry in entries:
        meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
        candidate_id = str(meta.get("candidate_endpoint_id") or entry.get("endpoint_key") or "").lower()
        settled.append(replacement.get(candidate_id, entry))
    return settled


def _has_terminal_event_settlement(entry: dict[str, Any]) -> bool:
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    return bool(meta.get("terminal_event_settlement"))


def _has_source_packet_baseline_settlement(entry: dict[str, Any]) -> bool:
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    return bool(meta.get("source_packet_baseline_settlement"))


def _settle_primary_binary_candidates_from_terminal_event(
    entries: list[dict[str, Any]],
    *,
    evidence: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not _deadline_aware_binary_forecast(evidence):
        return entries
    by_candidate = _primary_binary_candidates(entries)
    if not {"yes", "no"}.issubset(by_candidate):
        return entries
    signal = _terminal_binary_event_signal(evidence)
    if signal is None:
        return entries

    yes = by_candidate["yes"]
    no = by_candidate["no"]
    if signal["candidate_id"] == "yes":
        replacement = {
            "yes": _mark_candidate_terminal_event_settlement(
                yes,
                status="realized",
                evidence=evidence,
                event=signal["event"],
                rationale="An executed terminal event in the simulated path explicitly resolved the forecast question as yes.",
            ),
            "no": _mark_candidate_terminal_event_settlement(
                no,
                status="eliminated",
                evidence=evidence,
                event=signal["event"],
                rationale="An executed terminal event in the simulated path resolved yes, so the no candidate is eliminated.",
            ),
        }
    else:
        replacement = {
            "yes": _mark_candidate_terminal_event_settlement(
                yes,
                status="eliminated",
                evidence=evidence,
                event=signal["event"],
                rationale="An executed terminal event in the simulated path resolved no, so the yes candidate is eliminated.",
            ),
            "no": _mark_candidate_terminal_event_settlement(
                no,
                status="realized",
                evidence=evidence,
                event=signal["event"],
                rationale="An executed terminal event in the simulated path explicitly resolved the forecast question as no.",
            ),
        }

    settled: list[dict[str, Any]] = []
    for entry in entries:
        meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
        candidate_id = str(meta.get("candidate_endpoint_id") or entry.get("endpoint_key") or "").lower()
        settled.append(replacement.get(candidate_id, entry))
    return settled


def _settle_primary_binary_candidates_from_source_packet_baseline(
    entries: list[dict[str, Any]],
    *,
    evidence: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not _final_horizon_reached(evidence) or not _deadline_aware_binary_forecast(evidence):
        return entries
    by_candidate = _primary_binary_candidates(entries)
    if not {"yes", "no"}.issubset(by_candidate):
        return entries

    yes = by_candidate["yes"]
    no = by_candidate["no"]
    if _has_terminal_event_settlement(yes) or _has_terminal_event_settlement(no) or _has_source_packet_baseline_settlement(yes) or _has_source_packet_baseline_settlement(no):
        return entries
    if yes.get("status") != "eliminated" or no.get("status") not in {"realized", "active", "weakened", "unresolved", "insufficient_ticks"}:
        return entries
    if not _source_packet_baseline_supports_yes(evidence):
        return entries
    if not _no_realization_is_absence_only(no, yes, evidence=evidence):
        return entries

    replacement = {
        "yes": _mark_candidate_source_baseline_settlement(
            yes,
            status="realized",
            evidence=evidence,
            rationale=(
                "The source packet's official baseline supports occurrence by the forecast deadline, "
                "and this path contains no hard authoritative miss or delay; absence-only no evidence is insufficient."
            ),
        ),
        "no": _mark_candidate_source_baseline_settlement(
            no,
            status="eliminated",
            evidence=evidence,
            rationale=(
                "The no candidate was based on absence of verification rather than hard miss/delay evidence, "
                "so it is eliminated against the source-packet baseline at the forecast deadline."
            ),
        ),
    }
    settled: list[dict[str, Any]] = []
    for entry in entries:
        meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
        candidate_id = str(meta.get("candidate_endpoint_id") or entry.get("endpoint_key") or "").lower()
        settled.append(replacement.get(candidate_id, entry))
    return settled


def _settle_primary_binary_candidates_from_counterpart(
    entries: list[dict[str, Any]],
    *,
    evidence: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not _deadline_aware_binary_forecast(evidence):
        return entries
    by_candidate = _primary_binary_candidates(entries)
    if not {"yes", "no"}.issubset(by_candidate):
        return entries

    yes = by_candidate["yes"]
    no = by_candidate["no"]
    if _has_terminal_event_settlement(yes) or _has_terminal_event_settlement(no) or _has_source_packet_baseline_settlement(yes) or _has_source_packet_baseline_settlement(no):
        return entries
    if yes.get("status") == "realized" or no.get("status") == "eliminated":
        replacement = {
            "yes": _mark_candidate_counterpart_settlement(
                yes,
                status="realized",
                counterpart=no,
                evidence=evidence,
                rationale="The no candidate was eliminated by endpoint evidence, so the yes candidate is realized.",
            ),
            "no": _mark_candidate_counterpart_settlement(
                no,
                status="eliminated",
                counterpart=yes,
                evidence=evidence,
                rationale="The yes candidate is realized, so the no candidate is eliminated.",
            ),
        }
    elif no.get("status") == "realized" or yes.get("status") == "eliminated":
        replacement = {
            "yes": _mark_candidate_counterpart_settlement(
                yes,
                status="eliminated",
                counterpart=no,
                evidence=evidence,
                rationale="The no candidate is realized, so the yes candidate is eliminated.",
            ),
            "no": _mark_candidate_counterpart_settlement(
                no,
                status="realized",
                counterpart=yes,
                evidence=evidence,
                rationale="The yes candidate was eliminated by endpoint evidence, so the no candidate is realized.",
            ),
        }
    else:
        return entries

    settled: list[dict[str, Any]] = []
    for entry in entries:
        meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
        candidate_id = str(meta.get("candidate_endpoint_id") or entry.get("endpoint_key") or "").lower()
        settled.append(replacement.get(candidate_id, entry))
    return settled


def _primary_binary_candidates(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_candidate: dict[str, dict[str, Any]] = {}
    for entry in entries:
        meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
        if meta.get("endpoint_role") != "primary_candidate":
            continue
        candidate_id = str(meta.get("candidate_endpoint_id") or entry.get("endpoint_key") or "").lower()
        if candidate_id in {"yes", "no"}:
            by_candidate[candidate_id] = entry
    return by_candidate


def _deadline_aware_binary_forecast(evidence: dict[str, Any] | None) -> bool:
    if not evidence:
        return False
    forecast_metadata = evidence.get("forecast_metadata")
    if not isinstance(forecast_metadata, dict):
        return False
    if not forecast_metadata.get("forecast_deadline_date"):
        return False
    if forecast_metadata.get("tick_horizon_policy") not in {None, "deadline_aware"}:
        return False
    candidates = evidence.get("scenario_candidate_endpoints")
    if not isinstance(candidates, list):
        return False
    candidate_ids = {
        str(item.get("id") or item.get("endpoint_key") or "").strip().lower()
        for item in candidates
        if isinstance(item, dict)
    }
    return {"yes", "no"}.issubset(candidate_ids)


def _mark_candidate_deadline_settlement(
    entry: dict[str, Any],
    *,
    status: str,
    evidence: dict[str, Any] | None,
    rationale: str | None = None,
) -> dict[str, Any]:
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    forecast_metadata = (evidence or {}).get("forecast_metadata")
    latest_tick = max((int(tick.get("tick_index") or 0) for tick in (evidence or {}).get("ticks") or []), default=None)
    return {
        **entry,
        "status": status,
        "blockers": [] if status in {"realized", "eliminated"} else list(entry.get("blockers") or []),
        "status_basis": "deadline_aware_binary_candidate_settlement",
        "rationale": rationale or entry.get("rationale") or "Settled explicit binary candidate at the forecast deadline.",
        "last_observed_tick_index": entry.get("last_observed_tick_index") or latest_tick,
        "evidence_refs": [
            *list(entry.get("evidence_refs") or []),
            {
                "source": "forecast_deadline",
                "forecast_deadline_date": (forecast_metadata or {}).get("forecast_deadline_date")
                if isinstance(forecast_metadata, dict)
                else None,
                "tick_index": latest_tick,
            },
        ],
        "meta": {**meta, "final_horizon_candidate_settlement": True},
    }


def _mark_candidate_terminal_event_settlement(
    entry: dict[str, Any],
    *,
    status: str,
    evidence: dict[str, Any] | None,
    event: dict[str, Any],
    rationale: str,
) -> dict[str, Any]:
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    latest_tick = max((int(tick.get("tick_index") or 0) for tick in (evidence or {}).get("ticks") or []), default=None)
    event_tick = _optional_int(event.get("scheduled_tick")) or latest_tick
    event_ref = {
        "source": "terminal_event",
        "event_id": event.get("event_id"),
        "title": event.get("title"),
        "event_type": event.get("event_type"),
        "scheduled_tick": event_tick,
        "actual_impact_summary": _terminal_event_actual_summary(event),
    }
    authority_ref = f"terminal_event:{event.get('event_type') or 'event'}"
    return {
        **entry,
        "status": status,
        "authority_refs": list(entry.get("authority_refs") or []) or [authority_ref],
        "evidence_refs": [*list(entry.get("evidence_refs") or []), event_ref],
        "blockers": [] if status in {"realized", "eliminated"} else list(entry.get("blockers") or []),
        "status_basis": "terminal_event_binary_candidate_settlement",
        "rationale": rationale,
        "last_observed_tick_index": entry.get("last_observed_tick_index") or event_tick,
        "meta": {**meta, "terminal_event_settlement": True},
    }


def _mark_candidate_source_baseline_settlement(
    entry: dict[str, Any],
    *,
    status: str,
    evidence: dict[str, Any] | None,
    rationale: str,
) -> dict[str, Any]:
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    latest_tick = max((int(tick.get("tick_index") or 0) for tick in (evidence or {}).get("ticks") or []), default=None)
    guidance = (evidence or {}).get("forecast_source_guidance")
    if not isinstance(guidance, dict):
        guidance = {}
    guidance_ref = {
        "source": "forecast_source_packet_baseline",
        "baseline_summary": guidance.get("baseline_summary"),
        "tick_index": latest_tick,
    }
    return {
        **entry,
        "status": status,
        "evidence_refs": [*list(entry.get("evidence_refs") or []), guidance_ref],
        "blockers": [] if status in {"realized", "eliminated"} else list(entry.get("blockers") or []),
        "status_basis": "source_packet_baseline_binary_candidate_settlement",
        "rationale": rationale,
        "last_observed_tick_index": entry.get("last_observed_tick_index") or latest_tick,
        "meta": {**meta, "source_packet_baseline_settlement": True},
    }


def _mark_candidate_counterpart_settlement(
    entry: dict[str, Any],
    *,
    status: str,
    counterpart: dict[str, Any],
    evidence: dict[str, Any] | None,
    rationale: str,
) -> dict[str, Any]:
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    latest_tick = max((int(tick.get("tick_index") or 0) for tick in (evidence or {}).get("ticks") or []), default=None)
    counterpart_refs = list(counterpart.get("evidence_refs") or [])
    counterpart_authority_refs = list(counterpart.get("authority_refs") or [])
    return {
        **entry,
        "status": status,
        "authority_refs": list(entry.get("authority_refs") or []) or counterpart_authority_refs,
        "evidence_refs": [
            *list(entry.get("evidence_refs") or []),
            *counterpart_refs,
            {
                "source": "binary_candidate_counterpart",
                "counterpart_endpoint_key": counterpart.get("endpoint_key"),
                "counterpart_status": counterpart.get("status"),
                "tick_index": latest_tick,
            },
        ],
        "blockers": [] if status in {"realized", "eliminated"} else list(entry.get("blockers") or []),
        "status_basis": "binary_candidate_counterpart_settlement",
        "rationale": rationale,
        "last_observed_tick_index": entry.get("last_observed_tick_index") or counterpart.get("last_observed_tick_index") or latest_tick,
        "meta": {**meta, "binary_counterpart_settlement": True},
    }


def _terminal_binary_event_signal(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    signals: list[dict[str, Any]] = []
    latest_tick = max((int(tick.get("tick_index") or 0) for tick in (evidence or {}).get("ticks") or []), default=None)
    for event in (evidence or {}).get("events") or []:
        if not isinstance(event, dict) or str(event.get("status") or "").lower() != "executed":
            continue
        event_tick = _optional_int(event.get("scheduled_tick"))
        if latest_tick is not None and event_tick is not None and event_tick > latest_tick:
            continue
        candidate_id = _terminal_binary_candidate_from_event(event)
        if candidate_id in {"yes", "no"}:
            signals.append({"candidate_id": candidate_id, "event": event})
    candidate_ids = {item["candidate_id"] for item in signals}
    if len(candidate_ids) != 1:
        return None
    return signals[0]


def _terminal_binary_candidate_from_event(event: dict[str, Any]) -> str | None:
    actual = event.get("actual_impact") if isinstance(event.get("actual_impact"), dict) else {}
    for key in ("endpoint", "outcome", "result", "candidate_endpoint_id"):
        value = str(actual.get(key) or "").strip().lower()
        if value in {"yes", "no"}:
            return value
    text = _terminal_event_actual_summary(event).lower()
    normalized = re.sub(r"['\"`]", "", text)
    if re.search(
        r"\b(resolve|resolves|resolved|settle|settles|settled)\b.{0,80}"
        r"\bforecast(?: question| endpoint)?\b.{0,80}\b(?:as|to) yes\b",
        normalized,
    ):
        return "yes"
    if re.search(
        r"\b(resolve|resolves|resolved|settle|settles|settled)\b.{0,80}"
        r"\bforecast(?: question| endpoint)?\b.{0,80}\b(?:as|to) no\b",
        normalized,
    ):
        return "no"
    return None


def _terminal_event_actual_summary(event: dict[str, Any]) -> str:
    actual = event.get("actual_impact") if isinstance(event.get("actual_impact"), dict) else {}
    parts = [
        event.get("title"),
        actual.get("summary"),
        actual.get("rationale"),
        actual.get("description"),
    ]
    return " ".join(str(part) for part in parts if part)


def _source_packet_baseline_supports_yes(evidence: dict[str, Any] | None) -> bool:
    guidance = (evidence or {}).get("forecast_source_guidance")
    if not isinstance(guidance, dict):
        return False
    return bool(guidance.get("official_baseline_supports_yes") and guidance.get("no_requires_hard_negative_evidence"))


def _no_realization_is_absence_only(
    no_entry: dict[str, Any],
    yes_entry: dict[str, Any],
    *,
    evidence: dict[str, Any] | None,
) -> bool:
    text = " ".join(
        [
            _entry_resolution_text(no_entry),
            _entry_resolution_text(yes_entry),
            _events_resolution_text(evidence),
        ]
    ).lower()
    cleaned = _remove_negated_hard_negative_mentions(text)
    if _contains_hard_negative_outcome(cleaned):
        return False
    absence_cues = (
        "absence",
        "absent",
        "no direct evidence",
        "no event confirms",
        "no verified",
        "not verified",
        "unverified",
        "not confirmed",
        "unconfirmed",
        "not evidenced",
        "without verified",
        "without direct",
        "stops short",
        "short of",
        "preorder",
        "estimated ship",
        "ship window",
        "ship-date",
    )
    return any(cue in text for cue in absence_cues)


def _entry_resolution_text(entry: dict[str, Any]) -> str:
    values: list[Any] = [
        entry.get("rationale"),
        entry.get("status_basis"),
        entry.get("contradiction_notes"),
        entry.get("blockers"),
        entry.get("evidence_refs"),
        entry.get("negative_evidence_refs"),
    ]
    return " ".join(_text_fragments(values))


def _events_resolution_text(evidence: dict[str, Any] | None) -> str:
    values: list[Any] = []
    for event in (evidence or {}).get("events") or []:
        if not isinstance(event, dict) or str(event.get("status") or "").lower() != "executed":
            continue
        values.append(event.get("title"))
        actual = event.get("actual_impact") if isinstance(event.get("actual_impact"), dict) else {}
        values.extend([actual.get("summary"), actual.get("rationale"), actual.get("description")])
    return " ".join(_text_fragments(values))


def _text_fragments(values: Any) -> list[str]:
    if isinstance(values, str):
        return [values]
    if isinstance(values, dict):
        return _text_fragments(list(values.values()))
    if isinstance(values, list) or isinstance(values, tuple):
        fragments: list[str] = []
        for value in values:
            fragments.extend(_text_fragments(value))
        return fragments
    if values is None:
        return []
    return [str(values)]


def _remove_negated_hard_negative_mentions(text: str) -> str:
    replacements = [
        r"\bno\s+(?:hard\s+|authoritative\s+|official\s+)?(?:delay|miss|cancellation|denial)\b",
        r"\bnot\s+(?:delayed|cancelled|canceled|denied|missed|unavailable)\b",
        r"\bwithout\s+(?:a\s+)?(?:hard\s+|authoritative\s+|official\s+)?(?:delay|miss|cancellation|denial)\b",
    ]
    cleaned = text
    for pattern in replacements:
        cleaned = re.sub(pattern, " ", cleaned)
    return cleaned


def _contains_hard_negative_outcome(text: str) -> bool:
    patterns = [
        r"\b(?:official|authoritative|confirmed|announced)\s+(?:delay|miss|cancellation|cancelation|denial)\b",
        r"\b(?:delayed|pushed|slipped|postponed)\s+(?:past|beyond|after)\s+(?:the\s+)?deadline\b",
        r"\bmissed\s+(?:the\s+)?deadline\b",
        r"\bdeadline\s+passed\s+without\b",
        r"\bcontinued\s+non-availability\b",
        r"\bnot\s+available\s+by\s+(?:the\s+)?deadline\b",
        r"\bcancelled\b",
        r"\bcanceled\b",
        r"\bdenied\b",
        r"\bwill\s+not\s+(?:occur|happen|be\s+available|launch|release)\b",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


def _mark_insufficient_ticks(entry: dict[str, Any], *, evidence: dict[str, Any] | None) -> dict[str, Any]:
    if entry.get("status") in {"realized", "eliminated", "insufficient_ticks"}:
        return entry
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    if meta.get("final_horizon_overlay") == "insufficient_ticks":
        return entry
    latest_tick = max((int(tick.get("tick_index") or 0) for tick in (evidence or {}).get("ticks") or []), default=None)
    return {
        **entry,
        "status": "insufficient_ticks",
        "blockers": [*list(entry.get("blockers") or []), "max_ticks_reached_before_terminal_endpoint"],
        "status_basis": "max_tick_limit_reached",
        "last_observed_tick_index": entry.get("last_observed_tick_index") or latest_tick,
        "meta": {
            **meta,
            "final_horizon_overlay": "insufficient_ticks",
            "previous_status": entry.get("status"),
            "reversible_on_resume": True,
        },
    }


def _revert_final_horizon_overlay(entry: dict[str, Any]) -> dict[str, Any]:
    meta = entry.get("meta") if isinstance(entry.get("meta"), dict) else {}
    if meta.get("final_horizon_overlay") != "insufficient_ticks" or not meta.get("reversible_on_resume"):
        return entry
    previous_status = meta.get("previous_status") or "active"
    blockers = [
        blocker
        for blocker in list(entry.get("blockers") or [])
        if blocker != "max_ticks_reached_before_terminal_endpoint"
    ]
    cleaned_meta = {key: value for key, value in meta.items() if key not in {"final_horizon_overlay", "previous_status", "reversible_on_resume"}}
    return {**entry, "status": previous_status, "blockers": blockers, "meta": cleaned_meta}


def _endpoint_ledger_model(db: Session) -> str:
    settings = get_settings()
    return _endpoint_ledger_route(db, fallback_model=settings.god_agent_model).primary.model


def _endpoint_ledger_route(db: Session, *, fallback_model: str | None = None) -> ResolvedLLMRoute:
    settings = get_settings()
    return resolve_audited_llm_route(
        db,
        route=AuditedLLMRoute.ENDPOINT_LEDGER,
        fallback_provider=settings.default_llm_provider,
        fallback_model=fallback_model or settings.god_agent_model,
    )


def _endpoint_histogram(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "endpoint_key": item.get("endpoint_key"),
            "label": item.get("label"),
            "status": item.get("status"),
            "realized": _realized_value(str(item.get("status") or "")),
            "path_mass": (item.get("meta") or {}).get("path_mass"),
            "status_path_masses": (item.get("meta") or {}).get("status_path_masses", {}),
            "supporting_evidence_count": len(item.get("evidence_refs") or []),
        }
        for item in entries
    ]


def _realized_value(status: str) -> bool | None:
    if status == "realized":
        return True
    if status == "eliminated":
        return False
    return None


def _terminality_assessment(entries: list[dict[str, Any]]) -> dict[str, Any]:
    if any(item.get("status") in TERMINAL_ENTRY_STATUSES for item in entries):
        status = "terminal_verified"
    elif any(item.get("status") == "process_only" for item in entries):
        status = "process_only"
    else:
        status = "unresolved"
    return {
        "status": status,
        "reason": "Derived from endpoint ledger entry statuses.",
        "realized_count": sum(1 for item in entries if item.get("status") in TERMINAL_ENTRY_STATUSES),
        "active_count": sum(1 for item in entries if item.get("status") == "active"),
    }


def _contradiction_check(entries: list[dict[str, Any]]) -> dict[str, Any]:
    notes = [
        {"endpoint_key": item.get("endpoint_key"), "note": item.get("contradiction_notes")}
        for item in entries
        if item.get("contradiction_notes")
    ]
    return {
        "status": "recorded" if notes else "not_recorded",
        "notes": notes[:8],
    }


def _canonical_entries(entries: list[dict[str, Any]]) -> str:
    return json.dumps(_normalize_entries(entries), sort_keys=True, default=str, separators=(",", ":"))


def _compact_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "big_bang": evidence.get("big_bang"),
        "scope": evidence.get("scope"),
        "multiverse": evidence.get("multiverse"),
        "initializer": _compact_value(evidence.get("initializer") or {}, max_items=10),
        "scenario_candidate_endpoints": _compact_value(evidence.get("scenario_candidate_endpoints") or [], max_items=8),
        "forecast_metadata": _compact_value(evidence.get("forecast_metadata") or {}, max_items=8),
        "forecast_source_guidance": _compact_value(evidence.get("forecast_source_guidance") or {}, max_items=8),
        "timeline_statuses": evidence.get("timeline_statuses"),
        "ticks": _compact_value((evidence.get("ticks") or [])[:8], max_items=8),
        "events": _compact_value((evidence.get("events") or [])[:12], max_items=8),
        "god_reviews": _compact_value((evidence.get("god_reviews") or [])[:8], max_items=8),
        "candidate_endpoint": _compact_value(evidence.get("candidate_endpoint") or {}, max_items=8),
    }


def _forecast_source_guidance(scenario: dict[str, Any], initializer: dict[str, Any]) -> dict[str, Any]:
    scenario_text = str(scenario.get("scenario_text") or scenario.get("prompt") or scenario.get("premise") or "")
    brief = initializer.get("simulation_brief") if isinstance(initializer.get("simulation_brief"), dict) else {}
    brief_text = " ".join(
        str(part)
        for part in [
            brief.get("summary"),
            brief.get("question"),
            brief.get("resolution_rule"),
            brief.get("forecast_horizon"),
        ]
        if part
    )
    source_text = "\n".join(part for part in [scenario_text, brief_text] if part)
    lowered = source_text.lower()
    no_requires_hard_negative = (
        "resolve no only" in lowered
        or "absence of independent proof" in lowered
        or "absence of external proof" in lowered
        or "absence of direct" in lowered
        or "not enough by itself" in lowered
    )
    official_baseline_supports_yes = bool(
        re.search(
            r"\b(?:company|authority|official|announced|schedule|scheduled|plan|planned|expected|expects|will)\b"
            r".{0,140}\b(?:will|begin|availability|available|release|launch|occur|happen|start|open)\b",
            lowered,
        )
        or re.search(
            r"\b(?:availability|release|launch|event)\b.{0,80}\b(?:will|scheduled|expected|planned|announced)\b",
            lowered,
        )
    )
    baseline_summary = _first_matching_sentence(
        source_text,
        (
            "announced schedule",
            "availability will",
            "will begin",
            "scheduled",
            "expected",
            "planned",
            "official schedule",
            "company says",
        ),
    )
    return {
        "official_baseline_supports_yes": official_baseline_supports_yes,
        "no_requires_hard_negative_evidence": no_requires_hard_negative,
        "baseline_summary": baseline_summary,
    }


def _first_matching_sentence(text: str, needles: tuple[str, ...]) -> str | None:
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text)
    for sentence in sentences:
        stripped = " ".join(sentence.split())
        if not stripped:
            continue
        lowered = stripped.lower()
        if any(needle in lowered for needle in needles):
            return _truncate(stripped, 500)
    return None


def _extract_known_uncertainties(*values: Any) -> list[Any]:
    found: list[Any] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        for key in ("known_uncertainty", "known_uncertainties", "uncertainties", "endpoint_options"):
            item = value.get(key)
            if isinstance(item, list):
                found.extend(item)
            elif item:
                found.append(item)
    return found[:12]


def _refs_from_value(value: dict[str, Any], *, keys: tuple[str, ...]) -> list[Any]:
    refs = []
    for key in keys:
        if value.get(key):
            refs.append({key: value[key]})
    return refs


def _endpoint_key(value: Any) -> str:
    text = str(value or "endpoint").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    if not text:
        return "endpoint"
    return text[:120]


def _label_from_text(value: Any) -> str:
    text = str(value or "Endpoint").replace("_", " ").strip()
    return _truncate(text[:1].upper() + text[1:], 240)


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _list_value(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _compact_value(value: Any, *, max_items: int = 6) -> Any:
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                compact["_truncated"] = True
                break
            if str(key) in {"plain_text_corpus", "raw_text", "scenario_text"}:
                compact[str(key)] = {"present": bool(item)}
            else:
                compact[str(key)] = _compact_value(item, max_items=max_items)
        return compact
    if isinstance(value, list):
        items = [_compact_value(item, max_items=max_items) for item in value[:max_items]]
        if len(value) > max_items:
            items.append({"_truncated_count": len(value) - max_items})
        return items
    if isinstance(value, str):
        return _truncate(value, 500)
    if isinstance(value, UUID):
        return str(value)
    return value


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."
