"""
Homophily-based trust rewiring.

Per-tick, a fraction of low-trust edges may be detached and replaced
with edges to cohorts whose `issue_stance` is similar (Euclidean
distance below a threshold). Returns the number of rewires performed.
"""
from __future__ import annotations

import math
import random
from collections.abc import Mapping

from backend.app.schemas.actors import CohortState
from backend.app.schemas.sociology import SociologyParams
from backend.app.sociology.trust import TrustGraph


def _stance_distance(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    """Euclidean distance over the union of axis keys."""
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    s = 0.0
    for k in keys:
        d = float(a.get(k, 0.0)) - float(b.get(k, 0.0))
        s += d * d
    return math.sqrt(s)


def rewire(
    *,
    trust_graph: TrustGraph,
    cohort_states: dict[str, CohortState],
    params: SociologyParams,
    rng: random.Random,
) -> int:
    """Perform a probabilistic rewiring pass.

    Returns
    -------
    int
        Number of edges removed/replaced.
    """
    h = params.homophily
    g = trust_graph.graph
    edges = list(g.edges(data=True))
    if not edges:
        return 0

    # Lowest-trust edges first.
    edges.sort(key=lambda e: float(e[2].get("trust", 0.0)))
    n_candidates = max(1, int(len(edges) * h.rewiring_rate))
    candidates = edges[:n_candidates]

    rewires = 0
    cohort_ids = list(cohort_states.keys())
    if not cohort_ids:
        return 0

    for u, v, _ in candidates:
        if rewires >= h.max_rewires_per_tick:
            break
        # Probability gate
        if rng.random() > h.rewiring_rate:
            continue
        if u not in cohort_states:
            continue

        # Find a similar cohort to attach to.
        u_state = cohort_states[u]
        # Sample a small candidate pool; pick the closest within threshold.
        sample_size = min(8, len(cohort_ids))
        sample = rng.sample(cohort_ids, sample_size)
        best_id: str | None = None
        best_dist = float("inf")
        for cid in sample:
            if cid == u or cid == v:
                continue
            d = _stance_distance(
                u_state.issue_stance,
                cohort_states[cid].issue_stance,
            )
            if d < best_dist:
                best_dist = d
                best_id = cid

        if best_id is None:
            continue
        # similarity_threshold is on a [0,1] similarity scale, distance must
        # be at most (1 - similarity_threshold) for a rewire.
        max_distance = 1.0 - h.similarity_threshold
        if best_dist > max_distance:
            continue

        # Detach old edge, attach new (low initial trust to allow it to grow).
        if g.has_edge(u, v):
            g.remove_edge(u, v)
        g.add_edge(
            u,
            best_id,
            trust=0.1,
            exposure=0,
            last_align=0.0,
        )
        rewires += 1

    return rewires


__all__ = ["rewire"]
