"""
the sociology model.2 — Attention update.

attention_next =
  attention * (1 - attention_decay_rate)
+ event_salience
+ feed_salience
+ personal_impact
+ identity_threat

Clamped to [0, params.attention.max_attention].
"""
from __future__ import annotations

import numpy as np

from backend.app.schemas.actors import CohortState
from backend.app.schemas.sociology import SociologyParams


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


def update_attention(
    *,
    cohort: CohortState,
    event_salience: float,
    feed_salience: float,
    personal_impact: float,
    identity_threat: float,
    params: SociologyParams,
) -> float:
    """Scalar attention update for one cohort. Pure function."""
    a = params.attention
    decayed = cohort.attention * (1.0 - a.default_decay_rate)
    raw = (
        decayed
        + event_salience * a.event_salience_weight
        + feed_salience * a.feed_salience_weight
        + personal_impact * a.personal_impact_weight
        + identity_threat * a.identity_threat_weight
    )
    return _clip(raw, 0.0, a.max_attention)


def update_attention_batch(
    states: np.ndarray,
    salience_matrix: np.ndarray,
    params: SociologyParams,
) -> np.ndarray:
    """Vectorized attention update.

    Parameters
    ----------
    states:
        1-D array of shape ``(N,)`` containing each cohort's current attention.
    salience_matrix:
        2-D array of shape ``(N, 4)`` whose columns are
        ``[event_salience, feed_salience, personal_impact, identity_threat]``.
    params:
        The full SociologyParams (only ``params.attention`` is used).

    Returns
    -------
    np.ndarray
        1-D array ``(N,)`` of updated attentions, clipped to
        ``[0, params.attention.max_attention]``.
    """
    a = params.attention
    states = np.asarray(states, dtype=np.float64)
    sal = np.asarray(salience_matrix, dtype=np.float64)
    if sal.ndim != 2 or sal.shape[1] != 4:
        raise ValueError("salience_matrix must have shape (N, 4)")
    if sal.shape[0] != states.shape[0]:
        raise ValueError("salience_matrix rows must match states length")

    weights = np.array(
        [
            a.event_salience_weight,
            a.feed_salience_weight,
            a.personal_impact_weight,
            a.identity_threat_weight,
        ],
        dtype=np.float64,
    )
    additive = sal @ weights
    decayed = states * (1.0 - a.default_decay_rate)
    out = decayed + additive
    return np.clip(out, 0.0, a.max_attention)


__all__ = ["update_attention", "update_attention_batch"]
