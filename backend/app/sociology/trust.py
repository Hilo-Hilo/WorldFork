"""
Trust graph (the sociology model).

A NetworkX-backed directed graph that tracks pairwise cohort/hero trust.
Edge attribute ``trust`` is updated EWMA-style by combining ingroup affinity,
recent action alignment, and exposure count.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import networkx as nx
import numpy as np

from backend.app.schemas.actors import CohortState

# Default EWMA blend used when callers don't override.
_DEFAULT_ALPHA = 0.10


class TrustGraph:
    """Wrapper around ``networkx.DiGraph`` for pairwise trust scores.

    Edge attributes
    ---------------
    trust:        scalar in [-1, 1] (EWMA-blended).
    exposure:     cumulative exposure count.
    last_align:   most-recent alignment value passed in.
    """

    def __init__(self, alpha: float = _DEFAULT_ALPHA) -> None:
        self._g: nx.DiGraph = nx.DiGraph()
        self._alpha = float(alpha)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_or_update_edge(
        self,
        src_cohort_id: str,
        dst_cohort_id: str,
        *,
        ingroup_affinity: float,
        recent_alignment: float,
        exposure_count: int,
    ) -> None:
        """Update the trust edge from `src` to `dst` using an EWMA blend.

        signal = ingroup_affinity * 0.5 + recent_alignment * 0.5
        trust_new = (1 - alpha) * trust_old + alpha * signal
        """
        signal = 0.5 * float(ingroup_affinity) + 0.5 * float(recent_alignment)
        signal = max(-1.0, min(1.0, signal))

        if self._g.has_edge(src_cohort_id, dst_cohort_id):
            data = self._g[src_cohort_id][dst_cohort_id]
            old = float(data.get("trust", 0.0))
            data["trust"] = (1.0 - self._alpha) * old + self._alpha * signal
            data["exposure"] = int(data.get("exposure", 0)) + int(exposure_count)
            data["last_align"] = float(recent_alignment)
        else:
            # Cold-start: weight signal in immediately.
            self._g.add_edge(
                src_cohort_id,
                dst_cohort_id,
                trust=signal,
                exposure=int(exposure_count),
                last_align=float(recent_alignment),
            )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def current_trust(self, src: str, dst: str) -> float:
        """Return the trust scalar for src→dst, or 0.0 if no edge exists."""
        if not self._g.has_edge(src, dst):
            return 0.0
        return float(self._g[src][dst].get("trust", 0.0))

    def neighbors_above(self, src: str, threshold: float) -> set[str]:
        """Return the set of `dst` for which src→dst trust > threshold."""
        if src not in self._g:
            return set()
        return {
            dst
            for dst in self._g.successors(src)
            if float(self._g[src][dst].get("trust", 0.0)) > threshold
        }

    def to_matrix(self, cohort_ids: list[str]) -> np.ndarray:
        """Return an NxN dense matrix in the given cohort_id order."""
        n = len(cohort_ids)
        idx = {cid: i for i, cid in enumerate(cohort_ids)}
        m = np.zeros((n, n), dtype=np.float64)
        for u, v, data in self._g.edges(data=True):
            if u in idx and v in idx:
                m[idx[u], idx[v]] = float(data.get("trust", 0.0))
        return m

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    @classmethod
    def from_cohort_states(
        cls,
        cohorts: Iterable[CohortState],
        alpha: float = _DEFAULT_ALPHA,
    ) -> TrustGraph:
        """Initialize a TrustGraph from each cohort's `visible_trust_summary`.

        ``visible_trust_summary`` is expected to be either:
          - ``{target_id: trust_value}`` (preferred), or
          - ``{"trust": {target_id: trust_value}, ...}`` (tolerated).
        """
        g = cls(alpha=alpha)
        for c in cohorts:
            g._g.add_node(c.cohort_id)
        for c in cohorts:
            summary = c.visible_trust_summary or {}
            inner = summary.get("trust") if isinstance(summary, dict) else None
            pairs: list[tuple[str, Any]]
            if isinstance(inner, dict):
                pairs = list(inner.items())
            elif isinstance(summary, dict):
                pairs = [
                    (k, v)
                    for k, v in summary.items()
                    if isinstance(v, (int, float))
                ]
            else:
                pairs = []
            for tgt, val in pairs:
                try:
                    fval = float(val)
                except (TypeError, ValueError):
                    continue
                g._g.add_edge(
                    c.cohort_id,
                    tgt,
                    trust=fval,
                    exposure=0,
                    last_align=0.0,
                )
        return g

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_jsonl_rows(self) -> list[dict[str, Any]]:
        """Return a JSONL-serializable list of edge records."""
        rows: list[dict[str, Any]] = []
        for u, v, data in self._g.edges(data=True):
            rows.append(
                {
                    "src": u,
                    "dst": v,
                    "trust": float(data.get("trust", 0.0)),
                    "exposure": int(data.get("exposure", 0)),
                    "last_align": float(data.get("last_align", 0.0)),
                }
            )
        return rows

    # ------------------------------------------------------------------
    # NetworkX escape hatch (read-only)
    # ------------------------------------------------------------------

    @property
    def graph(self) -> nx.DiGraph:
        return self._g

    def __len__(self) -> int:
        return self._g.number_of_edges()  # type: ignore[no-any-return]


__all__ = ["TrustGraph"]
