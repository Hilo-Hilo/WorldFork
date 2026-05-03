"""Per-tick metrics for a universe.

Computes the post-tick metrics blob fed into the God-agent prompt and
recorded on the universe row for the recursive multiverse explorer (PRD
§13.5 ``min_divergence_score``, §16/§24 observability).
"""
from __future__ import annotations

import math
from collections.abc import Iterable

from backend.app.schemas.actors import CohortState
from backend.app.schemas.events import Event
from backend.app.schemas.posts import SocialPost

# Stable feature vector used for cosine divergence between metric snapshots.
# Order matters — both child and parent must be projected through the same
# sequence.  Keep this small and intentional; adding features changes
# divergence retroactively.
_DIVERGENCE_FEATURES = (
    "expression_mass",
    "mobilization_risk",
    "post_volume",
    "post_reach_total",
    "issue_polarization",
    "trust_index",
    "active_cohorts",
    "pending_events",
)


def _safe_div(num: float, denom: float) -> float:
    return num / denom if denom else 0.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_universe_metrics(
    cohorts: list[CohortState],
    events: list[Event],
    posts: list[SocialPost],
    *,
    prev_metrics: dict | None,
    tick: int | None = None,
    branch_count: int = 0,
    parent_metrics: dict | None = None,
) -> dict:
    """Return the canonical per-tick metric dict for a universe.

    Args:
      cohorts: All cohorts currently in the universe (active or not).
      events: All events in the universe at this tick.
      posts: All social posts created **this** tick.
      prev_metrics: Metric dict from the previous tick (optional).
      tick: Override tick value.  Defaults to ``cohorts[0].tick`` when
        present, else 0.
      branch_count: Number of child universes branched from this universe.
      parent_metrics: Optional parent-universe metrics for divergence.
    """
    tick_value = tick if tick is not None else (cohorts[0].tick if cohorts else 0)

    active_cohorts_list = [c for c in cohorts if c.is_active]
    active_count = len(active_cohorts_list)
    total_pop = sum(c.represented_population for c in active_cohorts_list)

    # Population-weighted emotion means.
    emotion_means = _population_weighted_emotions(active_cohorts_list)

    # Dominant emotion.
    if emotion_means:
        dominant_key = max(emotion_means, key=emotion_means.get)
        dominant_emotion = (dominant_key, round(emotion_means[dominant_key], 4))
    else:
        dominant_emotion = ("none", 0.0)

    # Expression mass (population-weighted).
    expression_mass = _weighted_mean(
        [(c.expression_level, c.represented_population) for c in active_cohorts_list]
    )

    # Mobilization risk: fraction of population in organize/mobilize/escalate
    # weighted by anger + grievance.
    mobilization_risk = _mobilization_risk(active_cohorts_list)

    # Pending events = scheduled but not yet completed.
    pending_events = sum(
        1 for e in events if e.status in ("proposed", "scheduled", "active")
    )

    # Post volume / reach.
    post_volume = len(posts)
    post_reach_total = sum(float(p.reach_score) for p in posts)

    # Issue polarization: stddev of mean issue stance per cohort, weighted.
    issue_polarization = _issue_polarization(active_cohorts_list)

    # Trust index: average of sympathy/trust-related cohort emotion scores
    # divided by 10 to project to [0, 1].
    trust_index = _trust_index(active_cohorts_list)

    # Divergence vs parent if a parent_metrics blob was provided.
    divergence_vs_parent: float | None = None
    if parent_metrics:
        # Build a temp dict of fields needed for divergence; we already have
        # the values locally so do not assemble the final dict twice.
        current_features = {
            "expression_mass": expression_mass,
            "mobilization_risk": mobilization_risk,
            "post_volume": post_volume,
            "post_reach_total": post_reach_total,
            "issue_polarization": issue_polarization,
            "trust_index": trust_index,
            "active_cohorts": active_count,
            "pending_events": pending_events,
        }
        divergence_vs_parent = compute_divergence(current_features, parent_metrics)

    return {
        "tick": tick_value,
        "active_cohorts": active_count,
        "total_population_modeled": total_pop,
        "dominant_emotion": dominant_emotion,
        "emotion_means": {k: round(v, 4) for k, v in emotion_means.items()},
        "expression_mass": round(expression_mass, 4),
        "mobilization_risk": round(mobilization_risk, 4),
        "pending_events": pending_events,
        "branch_count": branch_count,
        "post_volume": post_volume,
        "post_reach_total": round(post_reach_total, 4),
        "issue_polarization": round(issue_polarization, 4),
        "trust_index": round(trust_index, 4),
        "divergence_vs_parent": (
            round(divergence_vs_parent, 4) if divergence_vs_parent is not None else None
        ),
    }


def compute_divergence(child_metrics: dict, parent_metrics: dict) -> float:
    """Cosine *distance* (1 - cosine similarity) between two metric vectors.

    Result is in [0, 2]; for two identical positive vectors it is 0.
    """
    a = [_safe_float(child_metrics.get(k, 0.0)) for k in _DIVERGENCE_FEATURES]
    b = [_safe_float(parent_metrics.get(k, 0.0)) for k in _DIVERGENCE_FEATURES]

    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 and norm_b == 0.0:
        return 0.0
    if norm_a == 0.0 or norm_b == 0.0:
        return 1.0
    cos_sim = dot / (norm_a * norm_b)
    # Clamp away tiny negative or >1 floating-point drift so identical
    # vectors return exactly 0.0.
    if cos_sim >= 1.0 or abs(1.0 - cos_sim) < 1e-12:
        return 0.0
    return max(0.0, 1.0 - cos_sim)


def emotional_volatility(cohort_history: list[CohortState]) -> float:
    """Population-weighted stddev of mean cohort emotion across ticks.

    Accepts the same cohort across multiple ticks (as a list of snapshots).
    Returns 0 for fewer than 2 snapshots.
    """
    if len(cohort_history) < 2:
        return 0.0
    means: list[float] = []
    for state in cohort_history:
        if not state.emotions:
            means.append(0.0)
            continue
        means.append(sum(state.emotions.values()) / len(state.emotions))
    n = len(means)
    avg = sum(means) / n
    var = sum((m - avg) ** 2 for m in means) / n
    return round(math.sqrt(var), 4)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _safe_float(v) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, tuple) and v:
        # dominant_emotion is (label, score) — pull the score.
        try:
            return float(v[1])
        except (TypeError, ValueError, IndexError):
            return 0.0
    return 0.0


def _weighted_mean(pairs: Iterable[tuple[float, float]]) -> float:
    num = 0.0
    den = 0.0
    for value, weight in pairs:
        w = float(weight)
        num += float(value) * w
        den += w
    return _safe_div(num, den)


def _population_weighted_emotions(
    cohorts: list[CohortState],
) -> dict[str, float]:
    if not cohorts:
        return {}
    keys: set[str] = set()
    for c in cohorts:
        keys.update(c.emotions.keys())

    result: dict[str, float] = {}
    for key in keys:
        pairs = [
            (c.emotions.get(key, 0.0), c.represented_population) for c in cohorts
        ]
        result[key] = _weighted_mean(pairs)
    return result


def _mobilization_risk(cohorts: list[CohortState]) -> float:
    if not cohorts:
        return 0.0
    total_pop = sum(c.represented_population for c in cohorts) or 1
    risk_pop = 0.0
    for c in cohorts:
        if c.mobilization_mode in {"organize", "mobilize", "escalate"}:
            anger = c.emotions.get("anger", 0.0) / 10.0
            grievance = float(c.grievance)
            weight = (anger + grievance) / 2.0
            risk_pop += c.represented_population * weight
    return min(1.0, _safe_div(risk_pop, total_pop))


def _issue_polarization(cohorts: list[CohortState]) -> float:
    """Population-weighted stddev of cohort mean stance across stance keys."""
    if not cohorts:
        return 0.0
    cohort_means: list[tuple[float, float]] = []
    for c in cohorts:
        if not c.issue_stance:
            continue
        m = sum(c.issue_stance.values()) / len(c.issue_stance)
        cohort_means.append((m, c.represented_population))
    if not cohort_means:
        return 0.0
    weighted_mean = _weighted_mean(cohort_means)
    total_w = sum(w for _, w in cohort_means) or 1.0
    var = sum(((v - weighted_mean) ** 2) * w for v, w in cohort_means) / total_w
    return min(1.0, math.sqrt(var))


def _trust_index(cohorts: list[CohortState]) -> float:
    """Population-weighted (trust + sympathy) - distrust on [0, 1]."""
    if not cohorts:
        return 0.5
    total_w = 0.0
    score = 0.0
    for c in cohorts:
        w = c.represented_population
        trust = c.emotions.get("trust", 5.0) / 10.0
        sympathy = c.emotions.get("sympathy", 5.0) / 10.0
        distrust = c.emotions.get("distrust", 5.0) / 10.0
        local = (trust + sympathy - distrust + 1.0) / 2.0  # ~[-0.5, 1.5] -> clip
        score += w * max(0.0, min(1.0, local))
        total_w += w
    return _safe_div(score, total_w)


__all__ = [
    "compute_universe_metrics",
    "compute_divergence",
    "emotional_volatility",
]
