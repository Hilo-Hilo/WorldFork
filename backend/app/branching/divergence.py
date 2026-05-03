"""Divergence-score heuristic for the branch-policy gate (PRD §13.5).

The God-agent proposes a branch by emitting a :class:`BranchDelta` discriminated
union (counterfactual_event_rewrite | parameter_shift | actor_state_override |
hero_decision_override).  The branch policy needs a cheap divergence estimate
in ``[0,1]`` that can be compared to ``policy.min_divergence_score`` to drop
low-value branches without spinning up a child sim.

This is intentionally a coarse heuristic — the goal is "enough signal to prune
the obvious nothing-burgers."  Real divergence is measured after a few ticks
of the child run by ``backend.app.runtime.metrics`` (the multiverse
divergence metrics live there).
"""
from __future__ import annotations

import logging
from typing import Any

from backend.app.schemas.branching import (
    ActorStateOverrideDelta,
    BranchDelta,
    CounterfactualEventRewriteDelta,
    HeroDecisionOverrideDelta,
    ParameterShiftDelta,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Field-type weights for actor_state_override deltas.
# ---------------------------------------------------------------------------

# These weights are PRD-derived intuitions: emotion swings ripple through
# spiral-of-silence and mobilization; stance shifts move the issue axis and
# therefore visible posts; trust/exposure changes ripple slower.
_FIELD_WEIGHTS: dict[str, float] = {
    "emotion": 0.6,
    "emotions": 0.6,
    "anger": 0.6,
    "fear": 0.6,
    "stance": 0.7,
    "issue_stance": 0.7,
    "perceived_majority": 0.55,
    "willingness_to_speak": 0.5,
    "trust": 0.45,
    "trust_in_institutions": 0.45,
    "trust_in_media": 0.45,
}

# Baselines per delta type — used when we can't compute anything more refined.
_BASELINE: dict[str, float] = {
    "counterfactual_event_rewrite": 0.5,
    "parameter_shift": 0.4,
    "actor_state_override": 0.45,
    "hero_decision_override": 0.55,
}


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _scale_parameter_shift(delta: ParameterShiftDelta) -> float:
    """Sum the absolute magnitudes in ``delta.delta`` and squash to [0,1].

    A magnitude of 1.0 across one knob already represents a meaningful swing,
    so we use a saturating scale: ``score = baseline + min(0.5, |sum|/2)``.
    """
    if not delta.delta:
        return _BASELINE["parameter_shift"]
    total = sum(abs(float(v)) for v in delta.delta.values())
    bonus = min(0.5, total / 2.0)
    return _clamp(_BASELINE["parameter_shift"] + bonus)


def _scale_actor_state_override(delta: ActorStateOverrideDelta) -> float:
    """Use the named field to look up a weight; fall back to baseline."""
    field = (delta.field or "").lower()
    # Match on full field name first, then dotted-leaf, then any substring.
    if field in _FIELD_WEIGHTS:
        return _FIELD_WEIGHTS[field]
    leaf = field.rsplit(".", 1)[-1]
    if leaf in _FIELD_WEIGHTS:
        return _FIELD_WEIGHTS[leaf]
    for key, weight in _FIELD_WEIGHTS.items():
        if key in field:
            return weight
    return _BASELINE["actor_state_override"]


def _history_volatility(parent_metrics_history: list[dict[str, Any]]) -> float:
    """Measure recent metric volatility — calmer parents amplify deltas.

    If we have at least two recent ticks with a numeric ``divergence_score``
    field, use the standard deviation as a proxy for volatility.  Otherwise
    return 0.0 (no adjustment).  The result is in roughly ``[0,0.2]``.
    """
    if not parent_metrics_history or len(parent_metrics_history) < 2:
        return 0.0
    scores: list[float] = []
    for snap in parent_metrics_history:
        v = snap.get("divergence_score") if isinstance(snap, dict) else None
        if isinstance(v, (int, float)):
            scores.append(float(v))
    if len(scores) < 2:
        return 0.0
    mean = sum(scores) / len(scores)
    var = sum((s - mean) ** 2 for s in scores) / len(scores)
    std = var ** 0.5
    # Clamp into a small adjustment window.
    return float(min(0.2, std))


def compute_divergence_estimate(
    parent_metrics_history: list[dict[str, Any]],
    branch_delta: BranchDelta | dict[str, Any] | None,
) -> float:
    """Return a rough divergence prediction in ``[0, 1]``.

    Behaviour by delta type:

    * ``parameter_shift`` — scales with the absolute magnitude of delta values.
    * ``counterfactual_event_rewrite`` — fixed 0.5 baseline.
    * ``actor_state_override`` — weighted by field type
      (emotion=0.6, stance=0.7, ...).
    * ``hero_decision_override`` — fixed 0.55 baseline.

    The score is then nudged by recent metric volatility (calmer parents make
    deltas relatively more impactful — see :func:`_history_volatility`).

    A ``None`` ``branch_delta`` returns 0.0 (nothing to evaluate).
    """
    if branch_delta is None:
        return 0.0

    # Accept both pydantic models and plain dicts (defensive — God output may
    # have been parsed loosely).
    if isinstance(branch_delta, dict):
        try:
            from pydantic import TypeAdapter

            branch_delta = TypeAdapter(BranchDelta).validate_python(branch_delta)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "compute_divergence_estimate: invalid branch_delta dict (%s); "
                "returning baseline 0.4",
                exc,
            )
            return 0.4

    base: float
    if isinstance(branch_delta, ParameterShiftDelta):
        base = _scale_parameter_shift(branch_delta)
    elif isinstance(branch_delta, CounterfactualEventRewriteDelta):
        base = _BASELINE["counterfactual_event_rewrite"]
    elif isinstance(branch_delta, ActorStateOverrideDelta):
        base = _scale_actor_state_override(branch_delta)
    elif isinstance(branch_delta, HeroDecisionOverrideDelta):
        base = _BASELINE["hero_decision_override"]
    else:  # pragma: no cover — discriminated union should be exhaustive
        _log.warning(
            "compute_divergence_estimate: unknown delta type %r; baseline=0.4",
            type(branch_delta).__name__,
        )
        base = 0.4

    return _clamp(base + _history_volatility(parent_metrics_history))


__all__ = ["compute_divergence_estimate"]
