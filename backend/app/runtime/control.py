from __future__ import annotations

from app.runtime.enums import NodeKind

ACTOR_BARRIER_KEY = "barrier:actor_decisions"
INTERRUPT_CHECK_PREFIX = "interrupt_check:after_"

DOWNSTREAM_PHASE_KINDS: tuple[NodeKind, ...] = (
    NodeKind.EVENT_GENERATION,
    NodeKind.SOCIOLOGY_UPDATE,
    NodeKind.GRAPH_UPDATE,
    NodeKind.GOD_REVIEW,
    NodeKind.TICK_SUMMARY,
)


def actor_node_key(kind: NodeKind, actor_id: str) -> str:
    if kind is NodeKind.COHORT_DECISION:
        return f"cohort:{actor_id}"
    if kind is NodeKind.HERO_DECISION:
        return f"hero:{actor_id}"
    raise ValueError(f"unsupported actor kind: {kind}")


def phase_node_key(kind: NodeKind) -> str:
    return kind.value


def interrupt_node_key(after_key: str) -> str:
    if after_key == ACTOR_BARRIER_KEY:
        return f"{INTERRUPT_CHECK_PREFIX}actor_barrier"
    suffix = after_key.removeprefix("barrier:")
    return f"{INTERRUPT_CHECK_PREFIX}{suffix}"


def tool_call_node_key(index: int, tool_name: str) -> str:
    safe_name = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in tool_name)
    return f"tool_call:{index}:{safe_name[:120]}"
