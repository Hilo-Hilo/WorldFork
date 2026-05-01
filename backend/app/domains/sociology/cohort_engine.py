from __future__ import annotations

from sqlalchemy.orm import Session

from app.db import models


def generate_split_candidates(db: Session, *, big_bang_id, multiverse_id, tick_index: int, sociology_result: dict) -> list[dict]:
    if _recent_approved(db, models.CohortSplit, multiverse_id, tick_index, cooldown_ticks=1):
        return []
    metrics = sociology_result.get("metrics", {})
    signals = sociology_result.get("signals", [])
    score = _clamp(
        0.08
        + _num(metrics.get("divergence_index")) * 0.24
        + _num(metrics.get("silence_pressure")) * 0.12
        + _num(metrics.get("conflict_pressure")) * 0.2
        + _num(metrics.get("mobilization_readiness")) * 0.2
        + _num(metrics.get("identity_salience")) * 0.1
        + _num(metrics.get("cumulative_structural_pressure")) * 0.2
        - _num(metrics.get("trust_average")) * 0.04
    )
    if score < 0.46:
        return []
    conflict_edge = _top_edge(sociology_result, "conflict")
    payload = {
        "score": round(score, 4),
        "reason": "bounded_confidence_and_spiral_of_silence_divergence",
        "suggested_split_axis": _relationship_axis(conflict_edge, "institutional compliance versus mobilized resistance"),
        "criteria": {
            "minimum_score": 0.46,
            "branch_relevant_score": 0.7,
            "requires_god_agent_approval": True,
        },
        "evidence": _candidate_evidence(metrics, signals, ["bounded_confidence", "public_silence", "social_identity"]),
    }
    candidate = models.CohortSplitCandidate(
        big_bang_id=big_bang_id,
        multiverse_id=multiverse_id,
        tick_index=tick_index,
        status="candidate",
        payload=payload,
    )
    db.add(candidate)
    db.flush()
    return [{"id": str(candidate.id), "payload": candidate.payload}]


def generate_merge_candidates(db: Session, *, big_bang_id, multiverse_id, tick_index: int, sociology_result: dict) -> list[dict]:
    if _recent_approved(db, models.CohortMerge, multiverse_id, tick_index, cooldown_ticks=2):
        return []
    metrics = sociology_result.get("metrics", {})
    signals = sociology_result.get("signals", [])
    score = _clamp(
        _num(metrics.get("coalition_max")) * 0.42
        + _num(metrics.get("trust_average")) * 0.24
        + _signal_value(signals, "homophily", "cohesion") * 0.28
        + _num(metrics.get("reconciliation_pressure")) * 0.26
        + _num(metrics.get("previous_fatigue")) * 0.12
        - _num(metrics.get("conflict_pressure")) * 0.26
    )
    if score < 0.48:
        return []
    coalition_edge = _top_edge(sociology_result, "coalition")
    payload = {
        "score": round(score, 4),
        "reason": "coalition_homophily_and_trust_overlap",
        "suggested_merge_axis": _relationship_axis(coalition_edge, "shared dependency and resource coordination"),
        "criteria": {
            "minimum_score": 0.48,
            "requires_plan_merge_before_approval": True,
        },
        "evidence": _candidate_evidence(metrics, signals, ["homophily", "complex_contagion"]),
    }
    candidate = models.CohortMergeCandidate(
        big_bang_id=big_bang_id,
        multiverse_id=multiverse_id,
        tick_index=tick_index,
        status="candidate",
        payload=payload,
    )
    db.add(candidate)
    db.flush()
    return [{"id": str(candidate.id), "payload": candidate.payload}]


def generate_emergence_candidates(db: Session, *, big_bang_id, multiverse_id, tick_index: int, sociology_result: dict) -> list[dict]:
    if _recent_approved(db, models.CohortEmergence, multiverse_id, tick_index, cooldown_ticks=3):
        return []
    metrics = sociology_result.get("metrics", {})
    signals = sociology_result.get("signals", [])
    score = _clamp(
        _num(metrics.get("mobilization_readiness")) * 0.46
        + _num(metrics.get("reinforcement")) * 0.28
        + _num(metrics.get("identity_salience")) * 0.24
        + _num(metrics.get("coalition_max")) * 0.12
        + _num(metrics.get("cumulative_structural_pressure")) * 0.1
    )
    if score < 0.66:
        return []
    coalition_edge = _top_edge(sociology_result, "coalition")
    payload = {
        "score": round(score, 4),
        "reason": "threshold_mobilization_complex_contagion_and_identity_salience",
        "suggested_new_cohort": _relationship_axis(coalition_edge, "networked mutual-aid or resistance bloc"),
        "criteria": {
            "minimum_score": 0.54,
            "branch_relevant_score": 0.7,
            "requires_god_agent_approval": True,
        },
        "evidence": _candidate_evidence(metrics, signals, ["threshold_mobilization", "complex_contagion", "social_identity"]),
    }
    candidate = models.CohortEmergenceCandidate(
        big_bang_id=big_bang_id,
        multiverse_id=multiverse_id,
        tick_index=tick_index,
        status="candidate",
        payload=payload,
    )
    db.add(candidate)
    db.flush()
    return [{"id": str(candidate.id), "payload": candidate.payload}]


def _candidate_evidence(metrics: dict, signals: list[dict], names: list[str]) -> dict:
    return {
        "metrics": {
            key: metrics.get(key)
            for key in [
                "trust_average",
                "conflict_pressure",
                "divergence_index",
                "silence_pressure",
                "mobilization_readiness",
                "reinforcement",
                "identity_salience",
                "coalition_max",
            ]
        },
        "signals": [signal for signal in signals if signal.get("model") in names],
    }


def _signal_value(signals: list[dict], model: str, field: str) -> float:
    for signal in signals:
        if signal.get("model") == model:
            return _num(signal.get(field))
    return 0.0


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _recent_approved(db: Session, model, multiverse_id, tick_index: int, cooldown_ticks: int) -> bool:
    rows = db.query(model).filter(model.multiverse_id == multiverse_id, model.status == "persisted").all()
    return any(tick_index - int(row.tick_index or 0) <= cooldown_ticks for row in rows)


def _top_edge(sociology_result: dict, layer: str) -> dict:
    layer_summary = ((sociology_result.get("graph_summary") or {}).get("layers") or {}).get(layer) or {}
    edges = layer_summary.get("top_edges") or []
    return edges[0] if edges else {}


def _relationship_axis(edge: dict, fallback: str) -> str:
    source = edge.get("source")
    target = edge.get("target")
    if source and target:
        return f"{source} versus {target}"
    return fallback
