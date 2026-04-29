from __future__ import annotations

from app.runtime.control import ACTOR_BARRIER_KEY


def actor_barrier_key() -> str:
    return ACTOR_BARRIER_KEY


def actor_barrier_edges(actor_nodes: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    return tuple((node_key, ACTOR_BARRIER_KEY) for node_key in actor_nodes)
