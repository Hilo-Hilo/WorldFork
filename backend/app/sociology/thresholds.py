"""
Threshold mobilization and complex contagion.

mobilize_if =
  grievance
+ anger
+ trusted_peer_participation
+ perceived_efficacy
- cost_fear
> mobilization_threshold

cost_fear = legal_or_status_risk_sensitivity + fatigue * 0.5

Complex contagion: adoption requires at least k trusted neighbors already
mobilized, not merely one (Centola 2007).
"""
from __future__ import annotations

from backend.app.schemas.actors import CohortState
from backend.app.schemas.sociology import SociologyParams
from backend.app.sociology.trust import TrustGraph

_FATIGUE_COST_WEIGHT = 0.5


def mobilization_score(
    *,
    cohort: CohortState,
    trusted_peer_participation: float,
    params: SociologyParams,
) -> float:
    """Return the scalar mobilization driver."""
    m = params.mobilization
    anger = float(cohort.emotions.get("anger", 0.0)) / 10.0
    legal_risk = float(
        cohort.behavior_state.get("legal_or_status_risk_sensitivity", 0.0)
    )
    cost_fear = legal_risk + cohort.fatigue * _FATIGUE_COST_WEIGHT

    return (
        m.grievance_weight * cohort.grievance
        + m.anger_weight * anger
        + m.peer_participation_weight * float(trusted_peer_participation)
        + m.efficacy_weight * cohort.perceived_efficacy
        - m.cost_fear_weight * cost_fear
    )


def will_mobilize(score: float, params: SociologyParams) -> bool:
    """Return True if the mobilization score crosses the configured threshold."""
    return float(score) > params.mobilization.default_threshold


def complex_contagion(
    *,
    cohort_id: str,
    trust_graph: TrustGraph,
    mobilized_set: set[str],
    params: SociologyParams,
    trust_threshold: float = 0.0,
) -> bool:
    """Return True when at least ``params.mobilization.complex_contagion_k``
    trusted neighbors are already mobilized."""
    k = params.mobilization.complex_contagion_k
    neighbors = trust_graph.neighbors_above(cohort_id, trust_threshold)
    overlap = neighbors & mobilized_set
    return len(overlap) >= k


# Mobilization-mode bands.
# Bands are ordered by ascending score threshold.
_MODE_BANDS: list[tuple[float, str]] = [
    (-float("inf"), "dormant"),
    (0.20, "murmur"),
    (0.45, "organize"),
    (0.65, "mobilize"),
    (0.85, "escalate"),
]


def mobilization_mode_transition(
    *,
    cohort: CohortState,
    score: float,
) -> str:
    """Map a mobilization score onto a discrete mobilization mode."""
    selected = "dormant"
    for threshold, mode in _MODE_BANDS:
        if score >= threshold:
            selected = mode
    return selected


__all__ = [
    "mobilization_score",
    "will_mobilize",
    "complex_contagion",
    "mobilization_mode_transition",
]
