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
from app.domains.multiverse.statuses import TERMINAL_MULTIVERSE_STATUSES
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
    merged = _merge_entry_updates(base_entries, raw_updates)
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
            entries = _entries_from_llm_payload(llm_payload, fallback_entries=entries)
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
    simulation_config = config.simulation_config if config is not None and isinstance(config.simulation_config, dict) else {}
    raw_initializer = scenario.get("initializer_output")
    initializer: dict[str, Any] = raw_initializer if isinstance(raw_initializer, dict) else {}
    multiverse_state = multiverse.state if multiverse is not None and isinstance(multiverse.state, dict) else {}
    return {
        "big_bang": {
            "id": str(big_bang.id),
            "name": big_bang.name,
            "status": big_bang.status,
            "scenario_input": _compact_value(scenario, max_items=8),
            "simulation_config": _compact_value(simulation_config, max_items=8),
        },
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
        meta_value = payload.get("meta")
        meta = meta_value if isinstance(meta_value, dict) else {}
        payload["meta"] = {"source": "initializer_endpoint_ledger", **meta}
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
                "meta": {"source": "initializer_branch_hypothesis"},
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
                "meta": {"source": "known_uncertainty"},
            },
        )
    for event in evidence.get("events") or []:
        actual = event.get("actual_impact") if isinstance(event.get("actual_impact"), dict) else {}
        endpoint = actual.get("endpoint") or actual.get("outcome") or actual.get("result")
        if endpoint:
            key = _endpoint_key(endpoint)
            entries[key] = {
                "endpoint_key": key,
                "label": _label_from_text(endpoint),
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
                "meta": {"source": "event_actual_impact"},
            }
    candidate = evidence.get("candidate_endpoint")
    if isinstance(candidate, dict):
        label = candidate.get("label") or candidate.get("endpoint_key") or candidate.get("description") or "Candidate endpoint"
        key = _endpoint_key(candidate.get("endpoint_key") or label)
        entries[key] = {
            "endpoint_key": key,
            "label": _label_from_text(label),
            "description": candidate.get("description") or candidate.get("rationale"),
            "status": str(candidate.get("status") or "active").lower(),
            "probability": candidate.get("probability"),
            "realization_criteria": _list_value(candidate.get("realization_criteria")),
            "authority_refs": _list_value(candidate.get("authority_refs")),
            "evidence_refs": [
                {"source": "posthoc_candidate", "label": candidate.get("label") or key},
                *_list_value(candidate.get("evidence_refs")),
            ],
            "negative_evidence_refs": _list_value(candidate.get("negative_evidence_refs")),
            "blockers": _list_value(candidate.get("blockers")),
            "status_basis": candidate.get("status_basis") or "posthoc_candidate",
            "contradiction_notes": candidate.get("contradiction_notes")
            or "Posthoc candidate requires comparison against existing timeline evidence.",
            "rationale": candidate.get("rationale") or "Added through post-simulation endpoint evaluation.",
            "last_observed_tick_index": _optional_int(candidate.get("last_observed_tick_index")),
            "meta": {"source": "posthoc_candidate"},
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
                },
            )
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
        },
    }


def _representative_status_for_weighting(endpoint_status: str, multiverse_status: str | None) -> str | None:
    if endpoint_status == "realized":
        return "realized"
    if endpoint_status == "insufficient_ticks":
        return "insufficient_ticks"
    if (
        endpoint_status in {"active", "weakened", "unresolved", "process_only"}
        and str(multiverse_status or "").lower() in TERMINAL_MULTIVERSE_STATUSES
    ):
        return "insufficient_ticks"
    if endpoint_status in {"active", "weakened", "unresolved", "process_only"}:
        return "unresolved"
    return None


def _endpoint_path_mass_distribution(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "endpoint_key": entry.get("endpoint_key"),
            "label": entry.get("label"),
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


def _entries_from_llm_payload(payload: dict[str, Any], *, fallback_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    raw_entries = payload.get("entries")
    if raw_entries is None:
        raw_entries = payload.get("endpoint_ledger")
    if raw_entries is None:
        raw_entries = payload.get("ledger_entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        return fallback_entries
    return _merge_entry_updates(fallback_entries, raw_entries)


def _merge_entry_updates(base_entries: list[dict[str, Any]], updates: list[Any]) -> list[dict[str, Any]]:
    merged = {entry.get("endpoint_key"): dict(entry) for entry in base_entries if entry.get("endpoint_key")}
    for update in updates:
        if not isinstance(update, dict):
            continue
        key = update.get("endpoint_key") or update.get("key") or update.get("label")
        if not key:
            continue
        endpoint_key = _endpoint_key(key)
        current = merged.get(endpoint_key, {"endpoint_key": endpoint_key, "label": _label_from_text(str(key))})
        current.update({k: v for k, v in update.items() if v not in (None, "")})
        current["endpoint_key"] = endpoint_key
        current.setdefault("label", _label_from_text(str(key)))
        merged[endpoint_key] = current
    return _finalize_entries(list(merged.values()))


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
        meta_value = item.get("meta")
        meta = meta_value if isinstance(meta_value, dict) else {}
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
    if _final_horizon_reached(evidence):
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
    multiverse_value = evidence.get("multiverse")
    multiverse = multiverse_value if isinstance(multiverse_value, dict) else {}
    if str(multiverse.get("status") or "").lower() not in TERMINAL_MULTIVERSE_STATUSES:
        return False
    big_bang_value = evidence.get("big_bang")
    big_bang = big_bang_value if isinstance(big_bang_value, dict) else {}
    simulation_config_value = big_bang.get("simulation_config")
    simulation_config = (
        simulation_config_value if isinstance(simulation_config_value, dict) else {}
    )
    max_ticks = simulation_config.get("max_ticks")
    if max_ticks is None:
        return False
    ticks_value = evidence.get("ticks")
    ticks = ticks_value if isinstance(ticks_value, list) else []
    latest_tick = max(
        (int(tick.get("tick_index") or 0) for tick in ticks if isinstance(tick, dict)),
        default=None,
    )
    return latest_tick is not None and latest_tick >= int(max_ticks)


def _mark_insufficient_ticks(entry: dict[str, Any], *, evidence: dict[str, Any] | None) -> dict[str, Any]:
    if entry.get("status") in {"realized", "eliminated", "insufficient_ticks"}:
        return entry
    meta_value = entry.get("meta")
    meta = meta_value if isinstance(meta_value, dict) else {}
    if meta.get("final_horizon_overlay") == "insufficient_ticks":
        return entry
    evidence_value = evidence or {}
    ticks_value = evidence_value.get("ticks")
    ticks = ticks_value if isinstance(ticks_value, list) else []
    latest_tick = max(
        (int(tick.get("tick_index") or 0) for tick in ticks if isinstance(tick, dict)),
        default=None,
    )
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
    meta_value = entry.get("meta")
    meta = meta_value if isinstance(meta_value, dict) else {}
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
        "timeline_statuses": evidence.get("timeline_statuses"),
        "ticks": _compact_value((evidence.get("ticks") or [])[:8], max_items=8),
        "events": _compact_value((evidence.get("events") or [])[:12], max_items=8),
        "god_reviews": _compact_value((evidence.get("god_reviews") or [])[:8], max_items=8),
        "candidate_endpoint": _compact_value(evidence.get("candidate_endpoint") or {}, max_items=8),
    }


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
