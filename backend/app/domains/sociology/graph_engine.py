from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models

LAYERS = ["exposure", "trust", "dependency", "influence", "coalition", "conflict", "oasis_interaction"]
BASE_LAYER_WEIGHTS = {
    "exposure": 0.44,
    "trust": 0.42,
    "dependency": 0.52,
    "influence": 0.38,
    "coalition": 0.22,
    "conflict": 0.24,
    "oasis_interaction": 0.34,
}


def update_graph_layers(
    db: Session,
    *,
    big_bang_id,
    multiverse_id,
    tick_index: int,
    social_observations: list[dict] | None = None,
    executed_events: list[dict] | None = None,
) -> list[dict]:
    social_observations = social_observations or []
    executed_events = executed_events or []
    actors = db.scalars(select(models.Actor).where(models.Actor.big_bang_id == big_bang_id)).all()
    actor_by_id = {actor.id: actor for actor in actors}
    latest_edges = _latest_edges_by_pair_and_layer(db, multiverse_id)
    metrics = _tick_pressure_metrics(social_observations, executed_events)
    snapshots: list[dict] = []

    for layer in LAYERS:
        layer_edges = [edge for key, edge in latest_edges.items() if key[2] == layer]
        if not layer_edges:
            layer_edges = _seed_layer_edges(
                db,
                big_bang_id=big_bang_id,
                multiverse_id=multiverse_id,
                tick_index=max(0, tick_index - 1),
                layer=layer,
                actors=actors,
            )
        evolved_edges = _evolve_layer_edges(
            db,
            big_bang_id=big_bang_id,
            multiverse_id=multiverse_id,
            tick_index=tick_index,
            layer=layer,
            layer_edges=layer_edges,
            social_observations=social_observations,
            executed_events=executed_events,
            metrics=metrics,
        )
        db.flush()
        edge_summaries = [_edge_summary(edge, actor_by_id) for edge in evolved_edges]
        node_summaries = _node_summaries(actors, edge_summaries)
        layer_summary = summarize_graph_layer(layer, edge_summaries)
        graph = {
            "layer": layer,
            "tick_index": tick_index,
            "nodes": node_summaries,
            "edges": edge_summaries,
            "summary": layer_summary,
            "tick_pressure": metrics,
            "social_observations": social_observations if layer == "oasis_interaction" else [],
            "event_count": len(executed_events),
        }
        db.add(
            models.GraphSnapshot(
                big_bang_id=big_bang_id,
                multiverse_id=multiverse_id,
                tick_index=tick_index,
                layer=layer,
                graph=graph,
            )
        )
        snapshots.append(graph)
    db.flush()
    return snapshots


def build_graph_prompt_summary(db: Session, *, multiverse_id) -> dict:
    latest = _latest_edges_by_pair_and_layer(db, multiverse_id)
    actor_ids = {edge.source_actor_id for edge in latest.values()} | {edge.target_actor_id for edge in latest.values()}
    actor_ids.discard(None)
    actors = db.scalars(select(models.Actor).where(models.Actor.id.in_(actor_ids))).all() if actor_ids else []
    actor_by_id = {actor.id: actor for actor in actors}
    by_layer: dict[str, list[dict]] = {layer: [] for layer in LAYERS}
    for (_, _, layer), edge in latest.items():
        by_layer.setdefault(layer, []).append(_edge_summary(edge, actor_by_id))
    layer_summaries = {layer: summarize_graph_layer(layer, edges) for layer, edges in by_layer.items()}
    return {
        "layers": {
            layer: {
                "edge_count": len(edges),
                "average_weight": layer_summaries[layer]["average_weight"],
                "max_weight": layer_summaries[layer]["max_weight"],
                "interpretation": layer_summaries[layer]["interpretation"],
                "top_edges": sorted(edges, key=lambda item: item["weight"], reverse=True)[:5],
            }
            for layer, edges in by_layer.items()
        },
        "pressure": _graph_pressure(layer_summaries),
    }


def summarize_graph_layer(layer: str, edges: list[dict]) -> dict:
    if not edges:
        return {
            "edge_count": 0,
            "average_weight": 0.0,
            "max_weight": 0.0,
            "min_weight": 0.0,
            "average_abs_delta": 0.0,
            "interpretation": "no active edges",
        }
    weights = [float(edge.get("weight") or 0) for edge in edges]
    deltas = [abs(float(edge.get("delta") or 0)) for edge in edges]
    average = sum(weights) / len(weights)
    maximum = max(weights)
    return {
        "edge_count": len(edges),
        "average_weight": round(average, 4),
        "max_weight": round(maximum, 4),
        "min_weight": round(min(weights), 4),
        "average_abs_delta": round(sum(deltas) / len(deltas), 4),
        "interpretation": _interpret_layer(layer, average, maximum),
    }


def _latest_edges_by_pair_and_layer(db: Session, multiverse_id) -> dict:
    all_edges = db.scalars(
        select(models.GraphEdge)
        .where(models.GraphEdge.multiverse_id == multiverse_id)
        .order_by(models.GraphEdge.tick_index, models.GraphEdge.created_at)
    ).all()
    latest = {}
    for edge in all_edges:
        latest[(edge.source_actor_id, edge.target_actor_id, edge.layer)] = edge
    return latest


def _seed_layer_edges(
    db: Session,
    *,
    big_bang_id,
    multiverse_id,
    tick_index: int,
    layer: str,
    actors: list[models.Actor],
) -> list[models.GraphEdge]:
    if len(actors) < 2:
        return []
    seeded: list[models.GraphEdge] = []
    for source in actors[:6]:
        for target in actors[:6]:
            if source.id == target.id or len(seeded) >= 18:
                continue
            edge = models.GraphEdge(
                big_bang_id=big_bang_id,
                multiverse_id=multiverse_id,
                tick_index=tick_index,
                source_actor_id=source.id,
                target_actor_id=target.id,
                layer=layer,
                weight=BASE_LAYER_WEIGHTS[layer],
                payload={
                    "reason": f"Runtime seed for missing {layer} graph edge.",
                    "evidence": "Initializer did not provide enough relationship coverage for this layer.",
                    "status": "seeded",
                },
            )
            db.add(edge)
            seeded.append(edge)
    db.flush()
    return seeded


def _evolve_layer_edges(
    db: Session,
    *,
    big_bang_id,
    multiverse_id,
    tick_index: int,
    layer: str,
    layer_edges: list[models.GraphEdge],
    social_observations: list[dict],
    executed_events: list[dict],
    metrics: dict[str, float],
) -> list[models.GraphEdge]:
    social_counts = _social_counts_by_actor(social_observations)
    evolved: list[models.GraphEdge] = []
    for edge in layer_edges:
        old_weight = float(edge.weight or 0)
        delta = _layer_delta(layer, edge, social_counts, metrics)
        new_weight = _clamp(old_weight + delta)
        if edge.tick_index == tick_index and abs(delta) < 0.0001:
            evolved.append(edge)
            continue
        evolved_edge = models.GraphEdge(
            big_bang_id=big_bang_id,
            multiverse_id=multiverse_id,
            tick_index=tick_index,
            source_actor_id=edge.source_actor_id,
            target_actor_id=edge.target_actor_id,
            layer=layer,
            weight=new_weight,
            payload={
                **(edge.payload or {}),
                "previous_weight": old_weight,
                "delta": round(delta, 4),
                "reason": _delta_reason(layer, delta, metrics),
                "status": "active",
                "evidence": {
                    "social_observation_count": len(social_observations),
                    "executed_event_count": len(executed_events),
                    "source_activity": social_counts.get(str(edge.source_actor_id), 0),
                    "target_activity": social_counts.get(str(edge.target_actor_id), 0),
                    "tick_pressure": metrics,
                },
            },
        )
        db.add(evolved_edge)
        evolved.append(evolved_edge)
    return evolved


def _layer_delta(layer: str, edge: models.GraphEdge, social_counts: dict[str, int], metrics: dict[str, float]) -> float:
    source_activity = social_counts.get(str(edge.source_actor_id), 0)
    target_activity = social_counts.get(str(edge.target_actor_id), 0)
    pair_activity = min(1.0, 0.04 * (source_activity + target_activity))
    event_pressure = metrics["event_pressure"]
    conflict_language = metrics["conflict_language"]
    cooperation_language = metrics["cooperation_language"]
    institutional_stress = metrics["institutional_stress"]

    if layer == "exposure":
        return min(0.075, pair_activity + 0.02 * event_pressure)
    if layer == "oasis_interaction":
        return min(0.09, 0.035 * source_activity + 0.015 * target_activity)
    if layer == "trust":
        return _clamp_delta(0.025 * cooperation_language - 0.045 * conflict_language - 0.022 * institutional_stress)
    if layer == "dependency":
        return min(0.055, 0.035 * event_pressure + 0.015 * institutional_stress)
    if layer == "influence":
        return min(0.07, 0.025 * source_activity + 0.02 * event_pressure + 0.012 * cooperation_language)
    if layer == "coalition":
        return _clamp_delta(0.04 * cooperation_language + 0.018 * pair_activity - 0.025 * conflict_language)
    if layer == "conflict":
        return min(0.08, 0.04 * conflict_language + 0.026 * event_pressure + 0.012 * pair_activity)
    return 0.0


def _tick_pressure_metrics(social_observations: list[dict], executed_events: list[dict]) -> dict[str, float]:
    text = " ".join([str(item) for item in social_observations + executed_events]).lower()
    social_count = len(social_observations)
    event_count = len(executed_events)
    return {
        "social_count": float(social_count),
        "event_count": float(event_count),
        "event_pressure": min(1.0, event_count / 8),
        "social_pressure": min(1.0, social_count / 14),
        "conflict_language": _keyword_pressure(text, ["anger", "blame", "unequal", "protest", "fear", "conflict", "shutdown", "enforcement"]),
        "cooperation_language": _keyword_pressure(text, ["mutual", "aid", "coordinate", "coalition", "share", "help", "support", "transparent"]),
        "institutional_stress": _keyword_pressure(text, ["ban", "emergency", "restriction", "collapse", "gridlock", "failure", "unclear"]),
    }


def _keyword_pressure(text: str, keywords: list[str]) -> float:
    if not text:
        return 0.0
    hits = sum(text.count(keyword) for keyword in keywords)
    return min(1.0, hits / 10)


def _social_counts_by_actor(social_observations: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in social_observations:
        if not isinstance(item, dict):
            continue
        actor_id = item.get("actor_id")
        if actor_id:
            counts[str(actor_id)] += 1
    return dict(counts)


def _edge_summary(edge: models.GraphEdge, actor_by_id: dict) -> dict:
    payload = edge.payload or {}
    source = actor_by_id.get(edge.source_actor_id)
    target = actor_by_id.get(edge.target_actor_id)
    return {
        "edge_id": str(edge.id),
        "source_actor_id": str(edge.source_actor_id) if edge.source_actor_id else None,
        "target_actor_id": str(edge.target_actor_id) if edge.target_actor_id else None,
        "source": source.name if source else None,
        "target": target.name if target else None,
        "weight": round(float(edge.weight or 0), 4),
        "delta": round(float(payload.get("delta") or 0), 4),
        "reason": payload.get("reason"),
        "evidence": payload.get("evidence"),
        "status": payload.get("status", "active"),
    }


def _node_summaries(actors: list[models.Actor], edges: list[dict]) -> list[dict]:
    totals: dict[str, dict[str, float]] = {
        str(actor.id): {"in_weight": 0.0, "out_weight": 0.0, "degree": 0.0} for actor in actors
    }
    for edge in edges:
        weight = float(edge.get("weight") or 0)
        source_id = edge.get("source_actor_id")
        target_id = edge.get("target_actor_id")
        if source_id in totals:
            totals[source_id]["out_weight"] += weight
            totals[source_id]["degree"] += 1
        if target_id in totals:
            totals[target_id]["in_weight"] += weight
            totals[target_id]["degree"] += 1
    return [
        {
            "actor_id": str(actor.id),
            "name": actor.name,
            "actor_type": actor.actor_type,
            "centrality": {
                "in_weight": round(totals[str(actor.id)]["in_weight"], 4),
                "out_weight": round(totals[str(actor.id)]["out_weight"], 4),
                "degree": int(totals[str(actor.id)]["degree"]),
            },
        }
        for actor in actors
    ]


def _graph_pressure(layer_summaries: dict[str, dict]) -> dict:
    trust = layer_summaries.get("trust", {})
    conflict = layer_summaries.get("conflict", {})
    coalition = layer_summaries.get("coalition", {})
    exposure = layer_summaries.get("exposure", {})
    dependency = layer_summaries.get("dependency", {})
    return {
        "trust_average": trust.get("average_weight", 0.0),
        "conflict_max": conflict.get("max_weight", 0.0),
        "coalition_max": coalition.get("max_weight", 0.0),
        "exposure_average": exposure.get("average_weight", 0.0),
        "dependency_average": dependency.get("average_weight", 0.0),
        "branch_pressure": round(
            max(0.0, conflict.get("max_weight", 0.0) - trust.get("average_weight", 0.0) * 0.35)
            + coalition.get("max_weight", 0.0) * 0.25,
            4,
        ),
    }


def _delta_reason(layer: str, delta: float, metrics: dict[str, float]) -> str:
    if abs(delta) < 0.001:
        return f"{layer} remained stable under current tick pressure."
    direction = "increased" if delta > 0 else "decreased"
    drivers = []
    if metrics.get("event_pressure", 0) > 0:
        drivers.append("executed events")
    if metrics.get("social_pressure", 0) > 0:
        drivers.append("OASIS activity")
    if metrics.get("conflict_language", 0) > 0:
        drivers.append("conflict language")
    if metrics.get("cooperation_language", 0) > 0:
        drivers.append("cooperation language")
    return f"{layer} {direction} from {', '.join(drivers) or 'ambient drift'}."


def _interpret_layer(layer: str, average: float, maximum: float) -> str:
    if layer == "trust" and average < 0.35:
        return "trust stress"
    if layer == "conflict" and maximum > 0.55:
        return "high conflict edge"
    if layer == "coalition" and maximum > 0.55:
        return "coalition forming"
    if layer == "exposure" and average > 0.6:
        return "high exposure"
    if layer == "dependency" and average > 0.65:
        return "dependency lock-in"
    return "stable"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _clamp_delta(value: float) -> float:
    return max(-0.07, min(0.07, value))
