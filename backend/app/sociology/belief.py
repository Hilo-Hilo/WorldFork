"""
PRD §12.4 — Belief drift with bounded persuasion kernel.

  belief_i(t+1) =
    belief_i(t)
  + eta * sum_j(trust_ij * exposure_ij * bounded_kernel(distance(i,j))
                * (belief_j - belief_i))
  + event_shock_i
  - stubbornness_i * (belief_i(t) - baseline_belief_i)

The bounded kernel is a Gaussian: ``K_ij = exp(-(b_i - b_j)^2 / (2 * h^2))``.
Per-axis updates run independently and use a shared trust + exposure matrix.
"""
from __future__ import annotations

import numpy as np

from backend.app.schemas.actors import CohortState
from backend.app.schemas.sociology import SociologyParams


def update_beliefs(
    *,
    cohorts: list[CohortState],
    trust_matrix: np.ndarray,
    exposure_matrix: np.ndarray,
    axes: list[str],
    params: SociologyParams,
    event_shocks: dict[str, dict[str, float]] | None = None,
    baselines: dict[str, dict[str, float]] | None = None,
    stubbornness: dict[str, float] | None = None,
) -> dict[str, dict[str, float]]:
    """Compute next-tick belief vectors per axis for each cohort.

    Parameters
    ----------
    cohorts:
        Ordered list of CohortState, length N. Order MUST match the rows
        and columns of `trust_matrix` and `exposure_matrix`.
    trust_matrix:
        ``(N, N)`` non-negative trust weights ``T_ij``. Diagonal is ignored.
    exposure_matrix:
        ``(N, N)`` non-negative exposure weights ``E_ij``. Diagonal is ignored.
    axes:
        List of issue-stance axis names to update.
    params:
        SociologyParams (uses ``params.belief_drift``).
    event_shocks:
        Optional ``{cohort_id: {axis: shock_value}}`` additive term.
    baselines:
        Optional ``{cohort_id: {axis: baseline_value}}``. Defaults to the
        cohort's current value (no anchoring).
    stubbornness:
        Optional ``{cohort_id: weight}``. Defaults to
        ``params.belief_drift.stubbornness_anchor_weight`` for every cohort.

    Returns
    -------
    dict
        ``{cohort_id: {axis: new_belief_value}}``.
    """
    n = len(cohorts)
    bd = params.belief_drift
    eta = bd.eta
    h = max(bd.kernel_bandwidth, 1e-6)
    h_sq2 = 2.0 * h * h
    max_step = bd.max_step_per_tick

    if n == 0:
        return {}

    T = np.asarray(trust_matrix, dtype=np.float64)
    E = np.asarray(exposure_matrix, dtype=np.float64)
    if T.shape != (n, n) or E.shape != (n, n):
        raise ValueError(
            f"trust/exposure matrices must be ({n}, {n}); "
            f"got {T.shape} and {E.shape}"
        )

    # Pre-zero diagonals so a cohort doesn't influence itself.
    np.fill_diagonal(T, 0.0)
    np.fill_diagonal(E, 0.0)

    out: dict[str, dict[str, float]] = {c.cohort_id: {} for c in cohorts}

    for axis in axes:
        b = np.array(
            [float(c.issue_stance.get(axis, 0.0)) for c in cohorts],
            dtype=np.float64,
        )

        # Pairwise distances and bounded kernel
        diff = b[np.newaxis, :] - b[:, np.newaxis]  # diff[i,j] = b_j - b_i
        sq = diff * diff
        K = np.exp(-sq / h_sq2)

        # Influence weight per pair
        W = T * E * K
        # Sum over j of W_ij * (b_j - b_i)
        delta = (W * diff).sum(axis=1)

        new_b = b + eta * delta

        # Per-cohort shocks and anchoring
        for i, c in enumerate(cohorts):
            shock = 0.0
            if event_shocks is not None:
                shock = float(
                    event_shocks.get(c.cohort_id, {}).get(axis, 0.0)
                )
            base = (
                baselines.get(c.cohort_id, {}).get(axis, b[i])
                if baselines
                else b[i]
            )
            stub = (
                stubbornness[c.cohort_id]
                if stubbornness and c.cohort_id in stubbornness
                else bd.stubbornness_anchor_weight
            )
            new_b[i] = new_b[i] + shock - stub * (b[i] - float(base))

        # Cap per-tick step magnitude
        step = new_b - b
        step = np.clip(step, -max_step, max_step)
        new_b = b + step

        for i, c in enumerate(cohorts):
            out[c.cohort_id][axis] = float(new_b[i])

    return out


def trust_weighted_persuasion(
    *,
    source_credibility: float,
    listener_evidence_sensitivity: float,
    message_strength: float,
) -> float:
    """Small helper used to size event-driven shocks before they enter
    `event_shocks`. Multiplicative in [0, +inf); pure function."""
    s = max(0.0, float(source_credibility))
    listener = max(0.0, float(listener_evidence_sensitivity))
    m = float(message_strength)
    return s * listener * m


__all__ = ["update_beliefs", "trust_weighted_persuasion"]
