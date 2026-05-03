"""
PRD §12.3 — Expression update + §12.5 spiral-of-silence gate.

expression_next =
  base_expression
+ anger * 0.25
+ urgency * 0.20
+ perceived_efficacy * 0.15
- fear_of_isolation * 0.25
- fatigue * 0.10

Clamped to [0, 1] (or whatever ``params.expression.clamp`` says).

A cohort may privately disagree but publicly remain silent if:

  fear_of_isolation + perceived_minority_status + institutional_risk
    > expressive_courage

where expressive_courage = 1 - fear_of_isolation + behavior_state.contrarianism.
"""
from __future__ import annotations

from backend.app.schemas.actors import CohortState
from backend.app.schemas.sociology import SociologyParams


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


def update_expression(
    *,
    cohort: CohortState,
    params: SociologyParams,
) -> float:
    """Compute next expression_level. Pure function."""
    e = params.expression
    anger = float(cohort.emotions.get("anger", 0.0))
    urgency = float(cohort.emotions.get("urgency", 0.0))

    expr = (
        cohort.expression_level * e.base_expression_weight
        + (anger / 10.0) * e.anger_weight
        + (urgency / 10.0) * e.urgency_weight
        + cohort.perceived_efficacy * e.efficacy_weight
        - cohort.fear_of_isolation * e.fear_isolation_weight
        - cohort.fatigue * e.fatigue_weight
    )
    lo, hi = e.clamp
    return _clip(expr, lo, hi)


def spiral_of_silence_gate(
    *,
    cohort: CohortState,
    perceived_minority_status: float,
    institutional_risk: float,
    params: SociologyParams,
) -> bool:
    """Return True if the cohort should be silent (PRD §12.5).

    The cohort's private belief is unaffected — only public expression.
    """
    s = params.spiral_of_silence
    contrarianism = float(cohort.behavior_state.get("contrarianism", 0.0))
    expressive_courage = (
        (1.0 - cohort.fear_of_isolation) * s.expressive_courage_weight
        + contrarianism
    )
    pressure = (
        cohort.fear_of_isolation * s.fear_isolation_weight
        + perceived_minority_status * s.perceived_minority_weight
        + institutional_risk * s.institutional_risk_weight
    )
    return pressure > expressive_courage


__all__ = ["update_expression", "spiral_of_silence_gate"]
