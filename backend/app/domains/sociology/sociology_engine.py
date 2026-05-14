from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.domains.sociology.graph_engine import build_graph_prompt_summary

SOCIOLOGY_MODELS = [
    "bounded_confidence",
    "threshold_mobilization",
    "public_silence",
    "homophily",
    "complex_contagion",
    "social_identity",
    "attention_decay",
]


def run_sociology_update(
    db: Session,
    *,
    big_bang_id,
    multiverse_id,
    tick_index: int,
    executed_events: list[dict],
    social_observations: list[dict] | None = None,
) -> dict:
    social_observations = social_observations or []
    graph_summary = build_graph_prompt_summary(db, multiverse_id=multiverse_id)
    actors = list(db.scalars(select(models.Actor).where(models.Actor.big_bang_id == big_bang_id)).all())
    cohort_rows = _latest_state_rows(db, models.CohortState, multiverse_id)
    hero_rows = _latest_state_rows(db, models.HeroState, multiverse_id)
    metrics = _derive_metrics(graph_summary, executed_events, social_observations, cohort_rows)
    signals = _build_signals(metrics, graph_summary)

    for item in signals:
        db.add(
            models.SociologySignal(
                big_bang_id=big_bang_id,
                multiverse_id=multiverse_id,
                tick_index=tick_index,
                model=item["model"],
                signal=item,
            )
        )

    cohort_updates = _persist_cohort_state_updates(
        db,
        big_bang_id=big_bang_id,
        multiverse_id=multiverse_id,
        tick_index=tick_index,
        rows=cohort_rows,
        metrics=metrics,
        signals=signals,
        graph_summary=graph_summary,
    )
    hero_updates = _persist_hero_state_updates(
        db,
        big_bang_id=big_bang_id,
        multiverse_id=multiverse_id,
        tick_index=tick_index,
        rows=hero_rows,
        metrics=metrics,
        signals=signals,
        graph_summary=graph_summary,
    )
    influences = _build_prompt_influences(actors, tick_index, metrics, signals, graph_summary)
    for item in influences:
        db.add(
            models.SociologyPromptInfluence(
                big_bang_id=big_bang_id,
                multiverse_id=multiverse_id,
                actor_id=item.get("actor_id"),
                tick_index=tick_index,
                applies_to_tick_index=tick_index + 1,
                influence=item["influence"],
            )
        )
    db.flush()
    return {
        "signals": signals,
        "prompt_influences_for_next_tick": [item["influence"] for item in influences],
        "graph_summary": graph_summary,
        "cohort_state_updates": cohort_updates,
        "hero_state_updates": hero_updates,
        "metrics": metrics,
    }


def _derive_metrics(graph_summary: dict, executed_events: list[dict], social_observations: list[dict], cohort_rows: list) -> dict:
    pressure = graph_summary.get("pressure", {})
    previous_attention = _average_state_value(cohort_rows, ["attention_level", "attention"], 0.45)
    previous_expression = _average_state_value(cohort_rows, ["expression_level", "expression"], 0.35)
    previous_fatigue = _average_state_value(cohort_rows, ["fatigue"], 0.1)
    event_count = len(executed_events)
    social_count = len(social_observations)
    event_intensity = min(1.0, event_count / 8)
    social_intensity = min(1.0, social_count / 14)
    trust_average = _num(pressure.get("trust_average"), 0.45)
    conflict_max = _num(pressure.get("conflict_max"), 0.25)
    coalition_max = _num(pressure.get("coalition_max"), 0.2)
    exposure_average = _num(pressure.get("exposure_average"), 0.4)
    dependency_average = _num(pressure.get("dependency_average"), 0.45)
    salience = _clamp(previous_attention * 0.58 + event_intensity * 0.18 + social_intensity * 0.14 + exposure_average * 0.12)
    trust_gap = _clamp(0.55 - trust_average)
    conflict_pressure = _clamp(conflict_max * 0.75 + trust_gap * 0.35)
    mobilization_readiness = _clamp(
        _average_state_value(cohort_rows, ["mobilization_readiness"], 0.2) * 0.42
        + social_intensity * 0.2
        + event_intensity * 0.18
        + conflict_pressure * 0.14
        + coalition_max * 0.1
    )
    silence_pressure = _clamp(0.18 + conflict_pressure * 0.32 + trust_gap * 0.16 - previous_expression * 0.16)
    divergence_index = _clamp(conflict_pressure * 0.46 + trust_gap * 0.22 + dependency_average * 0.12 + silence_pressure * 0.2)
    reinforcement = _clamp(social_count * 0.11 + coalition_max * 0.28 + exposure_average * 0.18 + conflict_pressure * 0.12)
    identity_salience = _clamp(conflict_pressure * 0.38 + coalition_max * 0.28 + dependency_average * 0.17 + event_count * 0.08)
    cumulative_structural_pressure = _clamp(
        _average_state_value(cohort_rows, ["cumulative_structural_pressure"], 0.0) * 0.72
        + event_intensity * 0.12
        + social_intensity * 0.08
        + conflict_pressure * 0.08
    )
    reconciliation_pressure = _clamp(
        coalition_max * 0.34
        + trust_average * 0.22
        + dependency_average * 0.16
        + previous_fatigue * 0.18
        - conflict_pressure * 0.12
    )
    return {
        "event_count": event_count,
        "social_count": social_count,
        "event_intensity": round(event_intensity, 4),
        "social_intensity": round(social_intensity, 4),
        "previous_attention": round(previous_attention, 4),
        "previous_expression": round(previous_expression, 4),
        "previous_fatigue": round(previous_fatigue, 4),
        "trust_average": round(trust_average, 4),
        "trust_gap": round(trust_gap, 4),
        "conflict_max": round(conflict_max, 4),
        "conflict_pressure": round(conflict_pressure, 4),
        "coalition_max": round(coalition_max, 4),
        "exposure_average": round(exposure_average, 4),
        "dependency_average": round(dependency_average, 4),
        "salience": round(salience, 4),
        "mobilization_readiness": round(mobilization_readiness, 4),
        "silence_pressure": round(silence_pressure, 4),
        "divergence_index": round(divergence_index, 4),
        "reinforcement": round(reinforcement, 4),
        "identity_salience": round(identity_salience, 4),
        "cumulative_structural_pressure": round(cumulative_structural_pressure, 4),
        "reconciliation_pressure": round(reconciliation_pressure, 4),
    }


def _build_signals(metrics: dict, graph_summary: dict) -> list[dict]:
    return [
        {
            "model": "bounded_confidence",
            "alias": "opinion_drift",
            "divergence_index": metrics["divergence_index"],
            "confidence_radius": round(max(0.12, 0.62 - metrics["conflict_pressure"] * 0.36), 4),
            "expected_drift": "polarizing" if metrics["divergence_index"] >= 0.55 else "bounded",
            "evidence": {"trust_gap": metrics["trust_gap"], "conflict_pressure": metrics["conflict_pressure"]},
        },
        {
            "model": "threshold_mobilization",
            "alias": "mobilization",
            "readiness": metrics["mobilization_readiness"],
            "threshold_state": "crossed" if metrics["mobilization_readiness"] >= 0.62 else "latent",
            "evidence": {"social_count": metrics["social_count"], "event_count": metrics["event_count"], "coalition_max": metrics["coalition_max"]},
        },
        {
            "model": "public_silence",
            "pressure": metrics["silence_pressure"],
            "spiral_state": "suppression_risk" if metrics["silence_pressure"] >= 0.58 else "expressive_space_available",
            "evidence": {"expression_baseline": metrics["previous_expression"], "trust_gap": metrics["trust_gap"]},
        },
        {
            "model": "homophily",
            "cohesion": round(_clamp(metrics["coalition_max"] * 0.58 + metrics["exposure_average"] * 0.25), 4),
            "enclave_risk": round(_clamp(metrics["divergence_index"] * 0.56 + metrics["coalition_max"] * 0.16), 4),
            "evidence": {"graph_layers": _compact_layer_readout(graph_summary)},
        },
        {
            "model": "complex_contagion",
            "reinforcement": metrics["reinforcement"],
            "spread_state": "reinforced" if metrics["reinforcement"] >= 0.55 else "single_exposure",
            "evidence": {"social_count": metrics["social_count"], "exposure_average": metrics["exposure_average"]},
        },
        {
            "model": "social_identity",
            "identity_salience": metrics["identity_salience"],
            "boundary_state": "hardening" if metrics["identity_salience"] >= 0.56 else "fluid",
            "evidence": {"conflict_max": metrics["conflict_max"], "dependency_average": metrics["dependency_average"]},
        },
        {
            "model": "attention_decay",
            "salience": metrics["salience"],
            "fatigue_pressure": round(_clamp(metrics["previous_fatigue"] + metrics["event_count"] * 0.05 + metrics["social_count"] * 0.015), 4),
            "attention_state": "sustained" if metrics["salience"] >= 0.5 else "decaying",
            "evidence": {"previous_attention": metrics["previous_attention"], "event_count": metrics["event_count"]},
        },
    ]


def _persist_cohort_state_updates(db: Session, *, big_bang_id, multiverse_id, tick_index: int, rows: list, metrics: dict, signals: list[dict], graph_summary: dict) -> list[dict]:
    updates = []
    for row in rows:
        state = dict(row.state or {})
        attention = _clamp(_first_num(state, ["attention_level", "attention"], 0.45) * 0.78 + metrics["salience"] * 0.22)
        expression = _clamp(_first_num(state, ["expression_level", "expression"], 0.35) + (metrics["mobilization_readiness"] - metrics["silence_pressure"]) * 0.18)
        fatigue = _clamp(_first_num(state, ["fatigue"], 0.1) + metrics["event_count"] * 0.035 + metrics["social_count"] * 0.012 - attention * 0.018)
        next_state = {
            **state,
            "attention_level": round(attention, 4),
            "expression_level": round(expression, 4),
            "fatigue": round(fatigue, 4),
            "fear_of_isolation": round(_clamp(_first_num(state, ["fear_of_isolation"], 0.35) * 0.7 + metrics["silence_pressure"] * 0.3), 4),
            "mobilization_readiness": metrics["mobilization_readiness"],
            "bounded_confidence": {"divergence_index": metrics["divergence_index"]},
            "cumulative_structural_pressure": metrics["cumulative_structural_pressure"],
            "reconciliation_pressure": metrics["reconciliation_pressure"],
            "perceived_majority": "fragmented" if metrics["divergence_index"] >= 0.55 else state.get("perceived_majority", "uncertain"),
            "graph_influence": graph_summary.get("pressure", {}),
            "last_sociology_tick": tick_index,
        }
        db.add(
            models.CohortState(
                big_bang_id=big_bang_id,
                multiverse_id=multiverse_id,
                actor_id=row.actor_id,
                tick_index=tick_index,
                state=next_state,
                queued_event_ids=list(row.queued_event_ids or []),
            )
        )
        updates.append({"actor_id": str(row.actor_id) if row.actor_id else None, "state": next_state})
    return updates


def _persist_hero_state_updates(db: Session, *, big_bang_id, multiverse_id, tick_index: int, rows: list, metrics: dict, signals: list[dict], graph_summary: dict) -> list[dict]:
    updates = []
    for row in rows:
        state = dict(row.state or {})
        attention = _clamp(_first_num(state, ["attention", "attention_level"], 0.5) * 0.72 + metrics["salience"] * 0.28)
        fatigue = _clamp(_first_num(state, ["fatigue"], 0.1) + metrics["event_count"] * 0.025 + metrics["social_count"] * 0.01)
        strategy = "bridge_coalitions" if metrics["coalition_max"] >= metrics["conflict_max"] else "reduce_conflict_and_restore_trust"
        next_state = {
            **state,
            "attention": round(attention, 4),
            "fatigue": round(fatigue, 4),
            "current_strategy": strategy,
            "graph_influence": graph_summary.get("pressure", {}),
            "last_sociology_tick": tick_index,
        }
        db.add(
            models.HeroState(
                big_bang_id=big_bang_id,
                multiverse_id=multiverse_id,
                actor_id=row.actor_id,
                tick_index=tick_index,
                state=next_state,
                queued_event_ids=list(row.queued_event_ids or []),
            )
        )
        updates.append({"actor_id": str(row.actor_id) if row.actor_id else None, "state": next_state})
    return updates


def _build_prompt_influences(actors: list[models.Actor], tick_index: int, metrics: dict, signals: list[dict], graph_summary: dict) -> list[dict]:
    influences = []
    pressure = graph_summary.get("pressure", {})
    signal_map = {item["model"]: item for item in signals}
    for actor in actors:
        influence = {
            "source": "sociology_engine",
            "applies_to_tick_index": tick_index + 1,
            "actor_name": actor.name,
            "graph_pressure": pressure,
            "bounded_confidence": {
                "expected_drift": signal_map["bounded_confidence"]["expected_drift"],
                "divergence_index": metrics["divergence_index"],
            },
            "mobilization": {
                "threshold_state": signal_map["threshold_mobilization"]["threshold_state"],
                "readiness": metrics["mobilization_readiness"],
            },
            "silence": {
                "spiral_state": signal_map["public_silence"]["spiral_state"],
                "pressure": metrics["silence_pressure"],
            },
            "identity_and_contagion": {
                "identity_salience": metrics["identity_salience"],
                "reinforcement": metrics["reinforcement"],
            },
            "behavioral_instruction": (
                "Act as a simulated actor under these social pressures; do not inspect or control backend governance."
            ),
            "emotion_policy": "No emotion-vector values are included; emotion observability remains non-feedback telemetry.",
        }
        influences.append({"actor_id": actor.id, "influence": influence})
    return influences


def _latest_state_rows(db: Session, model, multiverse_id) -> list:
    rows = db.scalars(
        select(model)
        .where(model.multiverse_id == multiverse_id)
        .order_by(model.tick_index.desc(), model.created_at.desc())
    ).all()
    latest = {}
    for row in rows:
        key = row.actor_id or _anonymous_state_key(model, row.state or {})
        if key not in latest:
            latest[key] = row
    return list(latest.values())


def _anonymous_state_key(model, state: dict) -> str:
    for field in ("actor_name", "name", "cohort_name", "hero_name", "label", "archetype_name"):
        value = state.get(field)
        if value:
            return f"{model.__tablename__}:{field}:{value}"
    represented = state.get("represented_population")
    if represented is not None:
        return f"{model.__tablename__}:population:{represented}"
    return f"{model.__tablename__}:anonymous"


def _compact_layer_readout(graph_summary: dict) -> dict:
    layers = graph_summary.get("layers", {})
    return {
        name: {
            "average_weight": value.get("average_weight"),
            "max_weight": value.get("max_weight"),
            "interpretation": value.get("interpretation"),
        }
        for name, value in layers.items()
    }


def _average_state_value(rows: list, keys: list[str], default: float) -> float:
    values = []
    for row in rows:
        state = row.state or {}
        values.append(_first_num(state, keys, default))
    return sum(values) / len(values) if values else default


def _first_num(state: dict[str, Any], keys: list[str], default: float) -> float:
    for key in keys:
        if key in state:
            return _num(state.get(key), default)
    return default


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))
