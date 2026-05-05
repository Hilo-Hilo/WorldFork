"""
Multiplex graph layers per the sociology network model.

Layers:
  - exposure:     who saw what (cohort↔channel↔post)
  - trust:        directed trust scores
  - dependency:   institutional dependency / power links
  - mobilization: collective action / coordination ties
  - identity:     ingroup/outgroup affiliation
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

import networkx as nx

_LAYER = Literal["exposure", "trust", "dependency", "mobilization", "identity"]
_NODE_KIND = Literal["cohort", "hero", "institution", "channel"]


class MultiplexGraph:
    """A multi-layer directed graph backed by `networkx.MultiDiGraph`.

    Each edge stores its layer in the multi-edge ``key`` field for quick
    per-layer projection via :meth:`get_layer`.
    """

    LAYERS: tuple[str, ...] = (
        "exposure",
        "trust",
        "dependency",
        "mobilization",
        "identity",
    )

    def __init__(self) -> None:
        self._g: nx.MultiDiGraph = nx.MultiDiGraph()

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_node(
        self,
        node_id: str,
        *,
        kind: _NODE_KIND,
        label: str,
        attrs: dict[str, Any] | None = None,
    ) -> None:
        """Add or update a node."""
        if kind not in ("cohort", "hero", "institution", "channel"):
            raise ValueError(f"unknown node kind: {kind!r}")
        merged = dict(attrs or {})
        merged.update(kind=kind, label=label)
        self._g.add_node(node_id, **merged)

    def add_edge(
        self,
        layer: str,
        src: str,
        dst: str,
        *,
        weight: float,
        attrs: dict[str, Any] | None = None,
    ) -> None:
        """Add an edge on a particular layer (key=layer)."""
        if layer not in self.LAYERS:
            raise ValueError(
                f"unknown layer {layer!r}; valid: {self.LAYERS}"
            )
        # Ensure endpoints exist (NetworkX would auto-create otherwise).
        if src not in self._g:
            self._g.add_node(src)
        if dst not in self._g:
            self._g.add_node(dst)
        merged = dict(attrs or {})
        merged.update(weight=float(weight), layer=layer)
        self._g.add_edge(src, dst, key=layer, **merged)

    # ------------------------------------------------------------------
    # Projection
    # ------------------------------------------------------------------

    def get_layer(self, layer: str) -> nx.DiGraph:
        """Return a `DiGraph` containing only edges from `layer`.

        Nodes are preserved (with their attrs) so layer-specific
        visualization stays consistent with the master graph.
        """
        if layer not in self.LAYERS:
            raise ValueError(f"unknown layer {layer!r}")

        out = nx.DiGraph()
        for n, data in self._g.nodes(data=True):
            out.add_node(n, **dict(data))
        for u, v, key, data in self._g.edges(keys=True, data=True):
            if key == layer:
                # If multiple edges between the same (u,v) on this layer
                # (impossible since key=layer is unique), the last wins.
                out.add_edge(u, v, **dict(data))
        return out

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_jsonl_rows(self, layer: str) -> list[dict[str, Any]]:
        """Return JSONL-serializable edge rows for one layer."""
        if layer not in self.LAYERS:
            raise ValueError(f"unknown layer {layer!r}")
        rows: list[dict[str, Any]] = []
        for u, v, key, data in self._g.edges(keys=True, data=True):
            if key != layer:
                continue
            rows.append(
                {
                    "src": u,
                    "dst": v,
                    "layer": layer,
                    "weight": float(data.get("weight", 0.0)),
                    "attrs": {
                        k: val
                        for k, val in data.items()
                        if k not in ("weight", "layer")
                    },
                }
            )
        return rows

    # ------------------------------------------------------------------
    # Bootstrap
    # ------------------------------------------------------------------

    @classmethod
    def from_state(
        cls,
        *,
        cohorts: Iterable[Any] = (),
        heroes: Iterable[Any] = (),
        channels: Iterable[Any] = (),
        institutions: Iterable[Any] = (),
    ) -> MultiplexGraph:
        """Bootstrap a multiplex graph from the simulation state.

        Each input is iterable of objects with a ``.cohort_id`` /
        ``.hero_id`` / ``.label`` / ``.id`` attribute (or a (id, label) tuple).
        """
        g = cls()

        def _node_id(o: Any, *attrs: str) -> tuple[str, str]:
            for a in attrs:
                if hasattr(o, a):
                    return getattr(o, a), getattr(o, "label", str(getattr(o, a)))
            if isinstance(o, tuple) and len(o) >= 2:
                return str(o[0]), str(o[1])
            return str(o), str(o)

        for c in cohorts:
            nid, label = _node_id(c, "cohort_id")
            g.add_node(nid, kind="cohort", label=label, attrs={})

        for h in heroes:
            nid, label = _node_id(h, "hero_id")
            g.add_node(nid, kind="hero", label=label, attrs={})

        for ch in channels:
            nid, label = _node_id(ch, "channel_id", "id")
            g.add_node(nid, kind="channel", label=label, attrs={})

        for inst in institutions:
            nid, label = _node_id(inst, "institution_id", "id")
            g.add_node(nid, kind="institution", label=label, attrs={})

        return g

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def graph(self) -> nx.MultiDiGraph:
        return self._g

    def __len__(self) -> int:
        return self._g.number_of_edges()  # type: ignore[no-any-return]


__all__ = ["MultiplexGraph"]
