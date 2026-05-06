from __future__ import annotations

from collections import Counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import models

TERMINAL_MULTIVERSE_STATUSES = {"completed", "terminated"}
HARD_INVALID_MULTIVERSE_STATUSES = {"failed", "cancelled", "killed", "error"}
LOW_PATH_PROBABILITY_THRESHOLD = 0.0001


def latest_timeline_adjudication(
    db: Session,
    *,
    big_bang_id,
) -> models.TimelineAdjudicationVersion | None:
    return db.scalar(
        select(models.TimelineAdjudicationVersion)
        .where(models.TimelineAdjudicationVersion.big_bang_id == big_bang_id)
        .order_by(
            models.TimelineAdjudicationVersion.version.desc(),
            models.TimelineAdjudicationVersion.created_at.desc(),
            models.TimelineAdjudicationVersion.id.desc(),
        )
        .limit(1)
    )


def timeline_adjudication_entries(
    db: Session,
    adjudication_version_id,
) -> list[models.TimelineAdjudicationEntry]:
    return list(
        db.scalars(
            select(models.TimelineAdjudicationEntry)
            .where(models.TimelineAdjudicationEntry.adjudication_version_id == adjudication_version_id)
            .order_by(
                models.TimelineAdjudicationEntry.include_in_final.desc(),
                models.TimelineAdjudicationEntry.effective_path_probability.desc().nullslast(),
                models.TimelineAdjudicationEntry.ui_label,
            )
        )
        .all()
    )


def timeline_adjudication_detail(
    db: Session,
    adjudication: models.TimelineAdjudicationVersion,
) -> dict[str, Any]:
    return {"adjudication": adjudication, "entries": timeline_adjudication_entries(db, adjudication.id)}


def latest_timeline_adjudication_map(
    db: Session,
    *,
    big_bang_id,
) -> dict[str, models.TimelineAdjudicationEntry]:
    latest = latest_timeline_adjudication(db, big_bang_id=big_bang_id)
    if latest is None:
        return {}
    return {str(entry.multiverse_id): entry for entry in timeline_adjudication_entries(db, latest.id)}


def evaluate_timeline_adjudication(
    db: Session,
    *,
    big_bang: models.BigBang,
    source_type: str = "posthoc_adjudication",
    source_report_version_id=None,
    created_by: str = "timeline_adjudicator",
    summary: str | None = None,
) -> models.TimelineAdjudicationVersion:
    multiverses = db.scalars(
        select(models.Multiverse)
        .where(models.Multiverse.big_bang_id == big_bang.id)
        .order_by(models.Multiverse.ui_label)
    ).all()
    latest = latest_timeline_adjudication(db, big_bang_id=big_bang.id)
    version_number = int(getattr(latest, "version", 0) or 0) + 1
    rows = [_adjudicate_multiverse(db, multiverse=multiverse) for multiverse in multiverses]
    duplicate_signatures: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        if not row["include_in_final"]:
            continue
        signature = (
            row["endpoint_key"],
            row["endpoint_status"],
            row["evidence_summary"].get("latest_tick_index"),
            row["evidence_summary"].get("top_endpoint_probability"),
            row["evidence_summary"].get("branch_hypothesis_signature"),
        )
        previous = duplicate_signatures.get(signature)
        if previous is None:
            duplicate_signatures[signature] = row
            continue
        if row["original_path_probability"] < previous["original_path_probability"]:
            row.update(
                {
                    "viability_status": "duplicate",
                    "include_in_final": False,
                    "effective_path_probability": 0.0,
                    "mass_disposition": "excluded_duplicate",
                    "prune_reason": f"Duplicates {previous['ui_label']} endpoint/status/tick signature.",
                }
            )
        else:
            previous.update(
                {
                    "viability_status": "duplicate",
                    "include_in_final": False,
                    "effective_path_probability": 0.0,
                    "mass_disposition": "excluded_duplicate",
                    "prune_reason": f"Duplicates {row['ui_label']} endpoint/status/tick signature.",
                }
            )
            duplicate_signatures[signature] = row

    included_mass = round(sum(row["effective_path_probability"] for row in rows), 10)
    excluded_mass = round(sum(row["original_path_probability"] for row in rows if not row["include_in_final"]), 10)
    statuses = Counter(row["viability_status"] for row in rows)
    adjudication = models.TimelineAdjudicationVersion(
        big_bang_id=big_bang.id,
        version=version_number,
        status="completed",
        source_type=source_type,
        source_report_version_id=source_report_version_id,
        parent_adjudication_version_id=latest.id if latest else None,
        created_by=created_by,
        summary=summary
        or (
            f"Timeline adjudication retained {sum(1 for row in rows if row['include_in_final'])}/"
            f"{len(rows)} timelines for final endpoint aggregation."
        ),
        payload={
            "included_path_probability_mass": included_mass,
            "excluded_path_probability_mass": excluded_mass,
            "viability_statuses": dict(statuses),
            "policy": {
                "low_path_probability_threshold": LOW_PATH_PROBABILITY_THRESHOLD,
                "non_terminal_timelines": "excluded_evidence_insufficient",
                "process_only_timelines": "excluded_process_only",
                "duplicates": "conservative_signature_match",
            },
        },
    )
    db.add(adjudication)
    db.flush()
    for row in rows:
        db.add(
            models.TimelineAdjudicationEntry(
                adjudication_version_id=adjudication.id,
                big_bang_id=big_bang.id,
                multiverse_id=row["multiverse_id"],
                ui_label=row["ui_label"],
                viability_status=row["viability_status"],
                include_in_final=row["include_in_final"],
                prune_reason=row["prune_reason"],
                original_path_probability=row["original_path_probability"],
                effective_path_probability=row["effective_path_probability"],
                mass_disposition=row["mass_disposition"],
                endpoint_key=row["endpoint_key"],
                endpoint_status=row["endpoint_status"],
                evidence_summary=row["evidence_summary"],
            )
        )
    db.flush()
    return adjudication


def _adjudicate_multiverse(
    db: Session,
    *,
    multiverse: models.Multiverse,
) -> dict[str, Any]:
    original_path = _path_probability(multiverse)
    latest_tick_index = _latest_tick_index(db, multiverse.id)
    ledger, entries = _latest_multiverse_ledger_entries(db, multiverse=multiverse)
    top = _top_non_eliminated_entry(entries)
    endpoint_status = top.status if top is not None else None
    endpoint_key = top.endpoint_key if top is not None else None
    top_probability = float(top.probability or 0.0) if top is not None else None
    evidence_count = len(top.evidence_refs or []) if top is not None else 0
    authority_count = len(top.authority_refs or []) if top is not None else 0
    status = str(multiverse.status or "").lower()
    viability_status = "valid"
    include = True
    prune_reason = None
    mass_disposition = "retained"

    if status in HARD_INVALID_MULTIVERSE_STATUSES:
        viability_status = "impossible"
        include = False
        prune_reason = f"Timeline status {status} is not a usable terminal world."
        mass_disposition = "excluded_impossible"
    elif status not in TERMINAL_MULTIVERSE_STATUSES:
        viability_status = "evidence_insufficient"
        include = False
        prune_reason = f"Timeline is {status or 'unknown'}, not terminal."
        mass_disposition = "excluded_non_terminal"
    elif not entries:
        viability_status = "evidence_insufficient"
        include = False
        prune_reason = "No endpoint ledger entries exist for this timeline."
        mass_disposition = "excluded_no_endpoint_ledger"
    elif all(entry.status == "eliminated" for entry in entries):
        viability_status = "impossible"
        include = False
        prune_reason = "All endpoint ledger entries are eliminated."
        mass_disposition = "excluded_impossible"
    elif endpoint_status == "process_only":
        viability_status = "process_only"
        include = False
        prune_reason = "Top endpoint is process-only rather than terminal."
        mass_disposition = "excluded_process_only"
    elif original_path < LOW_PATH_PROBABILITY_THRESHOLD and endpoint_status != "realized":
        viability_status = "dominated"
        include = False
        prune_reason = f"Path probability {original_path:.10f} is below pruning threshold."
        mass_disposition = "excluded_dominated"

    return {
        "multiverse_id": multiverse.id,
        "ui_label": multiverse.ui_label,
        "viability_status": viability_status,
        "include_in_final": include,
        "prune_reason": prune_reason,
        "original_path_probability": original_path,
        "effective_path_probability": original_path if include else 0.0,
        "mass_disposition": mass_disposition,
        "endpoint_key": endpoint_key,
        "endpoint_status": endpoint_status,
        "evidence_summary": {
            "latest_tick_index": latest_tick_index,
            "ledger_version_id": str(ledger.id) if ledger else None,
            "top_endpoint_key": endpoint_key,
            "top_endpoint_status": endpoint_status,
            "top_endpoint_probability": top_probability,
            "top_endpoint_evidence_refs": evidence_count,
            "top_endpoint_authority_refs": authority_count,
            "multiverse_status": multiverse.status,
            "branch_probability": _branch_probability(multiverse),
            "path_probability": original_path,
            "branch_hypothesis_signature": _branch_hypothesis_signature(multiverse),
        },
    }


def _latest_multiverse_ledger_entries(
    db: Session,
    *,
    multiverse: models.Multiverse,
) -> tuple[models.EndpointLedgerVersion | None, list[models.EndpointLedgerEntry]]:
    ledger = db.scalar(
        select(models.EndpointLedgerVersion)
        .where(
            models.EndpointLedgerVersion.big_bang_id == multiverse.big_bang_id,
            models.EndpointLedgerVersion.multiverse_id == multiverse.id,
            models.EndpointLedgerVersion.scope == "multiverse",
        )
        .order_by(
            models.EndpointLedgerVersion.version.desc(),
            models.EndpointLedgerVersion.created_at.desc(),
            models.EndpointLedgerVersion.id.desc(),
        )
        .limit(1)
    )
    if ledger is None:
        return None, []
    entries = list(
        db.scalars(
            select(models.EndpointLedgerEntry)
            .where(models.EndpointLedgerEntry.ledger_version_id == ledger.id)
            .order_by(models.EndpointLedgerEntry.probability.desc().nullslast(), models.EndpointLedgerEntry.endpoint_key)
        ).all()
    )
    return ledger, entries


def _top_non_eliminated_entry(entries: list[models.EndpointLedgerEntry]) -> models.EndpointLedgerEntry | None:
    retained = [entry for entry in entries if entry.status != "eliminated"]
    if not retained:
        return entries[0] if entries else None
    primary = [entry for entry in retained if _is_primary_binary_entry(entry)]
    pool = primary or retained
    return min(pool, key=_endpoint_adjudication_sort_key)


def _is_primary_binary_entry(entry: models.EndpointLedgerEntry) -> bool:
    meta = entry.meta if isinstance(entry.meta, dict) else {}
    candidate_id = str(meta.get("candidate_endpoint_id") or entry.endpoint_key or "").strip().lower()
    return meta.get("endpoint_role") == "primary_candidate" and candidate_id in {"yes", "no"}


def _endpoint_adjudication_sort_key(entry: models.EndpointLedgerEntry) -> tuple[int, float, int, int, str]:
    status_order = {
        "realized": 0,
        "active": 1,
        "weakened": 2,
        "unresolved": 3,
        "insufficient_ticks": 4,
        "process_only": 5,
        "eliminated": 6,
    }
    try:
        probability = float(entry.probability or 0.0)
    except (TypeError, ValueError):
        probability = 0.0
    return (
        status_order.get(str(entry.status or ""), 7),
        -probability,
        -len(entry.authority_refs or []),
        -len(entry.evidence_refs or []),
        entry.endpoint_key,
    )


def _latest_tick_index(db: Session, multiverse_id) -> int | None:
    return db.scalar(
        select(func.max(models.TickSnapshot.tick_index)).where(models.TickSnapshot.multiverse_id == multiverse_id)
    )


def _path_probability(multiverse: models.Multiverse) -> float:
    return _probability(getattr(multiverse, "path_probability", None), default=1.0)


def _branch_probability(multiverse: models.Multiverse) -> float:
    return _probability(getattr(multiverse, "branch_probability", None), default=1.0)


def _branch_hypothesis_signature(multiverse: models.Multiverse) -> str | None:
    state = multiverse.state if isinstance(multiverse.state, dict) else {}
    branch = state.get("branch") if isinstance(state.get("branch"), dict) else {}
    premise = (
        branch.get("branch_hypothesis_signature")
        or branch.get("branch_premise")
        or branch.get("reason")
        or (multiverse.branch_reason if multiverse.depth and multiverse.branch_reason else None)
    )
    if not isinstance(premise, str) or not premise.strip():
        return None
    return " ".join(premise.strip().lower().split())[:500]


def _probability(value: Any, *, default: float) -> float:
    try:
        parsed = float(value if value is not None else default)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:
        return default
    return max(0.0, min(1.0, parsed))
