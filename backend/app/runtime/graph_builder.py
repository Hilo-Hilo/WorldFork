from __future__ import annotations

from dataclasses import dataclass

from app.runtime.enums import NodeKind
from app.runtime.policies import RetryPolicy


@dataclass(frozen=True, slots=True)
class TickGraphPlan:
    """Small placeholder graph contract for the runtime rewrite bootstrap."""

    retry_policy: RetryPolicy
    node_order: tuple[NodeKind, ...]
    checkpoint_nodes: frozenset[NodeKind]


DEFAULT_NODE_ORDER = (
    NodeKind.PROMPT_ASSEMBLY,
    NodeKind.COHORT_DECISION,
    NodeKind.HERO_DECISION,
    NodeKind.TOOL_CALL,
    NodeKind.VALIDATION,
    NodeKind.EVENT_GENERATION,
    NodeKind.SOCIOLOGY_UPDATE,
    NodeKind.GRAPH_UPDATE,
    NodeKind.GOD_REVIEW,
    NodeKind.TICK_SUMMARY,
    NodeKind.STATE_COMMIT,
)

DEFAULT_CHECKPOINT_NODES = frozenset(
    {
        NodeKind.COHORT_DECISION,
        NodeKind.HERO_DECISION,
        NodeKind.TOOL_CALL,
        NodeKind.EVENT_GENERATION,
        NodeKind.SOCIOLOGY_UPDATE,
        NodeKind.GRAPH_UPDATE,
        NodeKind.GOD_REVIEW,
        NodeKind.TICK_SUMMARY,
    }
)


def build_tick_graph(retry_policy: RetryPolicy | None = None) -> TickGraphPlan:
    """Return the initial runtime graph contract.

    This intentionally starts as a lightweight placeholder so the rewrite can
    land public interfaces before the real LangGraph state machine is wired in.
    """

    return TickGraphPlan(
        retry_policy=retry_policy or RetryPolicy(),
        node_order=DEFAULT_NODE_ORDER,
        checkpoint_nodes=DEFAULT_CHECKPOINT_NODES,
    )
