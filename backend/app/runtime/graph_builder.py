from __future__ import annotations

from dataclasses import dataclass

from app.runtime.barriers import actor_barrier_edges, actor_barrier_key
from app.runtime.control import DOWNSTREAM_PHASE_KINDS, actor_node_key, interrupt_node_key, phase_node_key
from app.runtime.enums import NodeKind
from app.runtime.node_runner import TickNodeSpec
from app.runtime.policies import RetryPolicy
from app.runtime.state import TickRuntimeState


@dataclass(frozen=True, slots=True)
class TickGraphPlan:
    retry_policy: RetryPolicy
    node_order: tuple[str, ...]
    node_specs: dict[str, TickNodeSpec]
    edges: tuple[tuple[str, str], ...]
    checkpoint_nodes: frozenset[str]
    checkpoint_order: tuple[str, ...]
    cohort_nodes: tuple[str, ...]
    hero_nodes: tuple[str, ...]
    actor_nodes: tuple[str, ...]
    barrier_nodes: tuple[str, ...]
    interrupt_nodes: tuple[str, ...]


def build_tick_graph(
    state: TickRuntimeState | None = None,
    retry_policy: RetryPolicy | None = None,
) -> TickGraphPlan:
    """Build the deterministic per-tick runtime graph contract.

    This is still a planning-layer representation rather than a fully wired
    execution graph, but it now captures the fan-out / barrier / interrupt
    semantics the runtime rewrite requires.
    """

    runtime_state = state or TickRuntimeState()
    retry = retry_policy or RetryPolicy()

    cohort_nodes = tuple(actor_node_key(NodeKind.COHORT_DECISION, cohort_id) for cohort_id in runtime_state.cohort_ids)
    hero_nodes = tuple(actor_node_key(NodeKind.HERO_DECISION, hero_id) for hero_id in runtime_state.hero_ids)
    actor_nodes = cohort_nodes + hero_nodes
    barrier_key = actor_barrier_key()

    node_specs: dict[str, TickNodeSpec] = {}
    checkpoint_order: list[str] = []
    edges: list[tuple[str, str]] = []
    node_order: list[str] = []
    interrupt_nodes: list[str] = []

    for node_key in cohort_nodes:
        node_specs[node_key] = TickNodeSpec(
            key=node_key,
            kind=NodeKind.COHORT_DECISION,
            checkpoint=True,
            actor_id=node_key.split(":", 1)[1],
        )
        checkpoint_order.append(node_key)
        node_order.append(node_key)

    for node_key in hero_nodes:
        node_specs[node_key] = TickNodeSpec(
            key=node_key,
            kind=NodeKind.HERO_DECISION,
            checkpoint=True,
            actor_id=node_key.split(":", 1)[1],
        )
        checkpoint_order.append(node_key)
        node_order.append(node_key)

    node_specs[barrier_key] = TickNodeSpec(key=barrier_key, kind=NodeKind.BARRIER, checkpoint=False)
    node_order.append(barrier_key)
    edges.extend(actor_barrier_edges(actor_nodes))

    previous_key = barrier_key
    for phase_kind in DOWNSTREAM_PHASE_KINDS:
        if phase_kind is NodeKind.TICK_SUMMARY:
            for tool_key in runtime_state.tool_call_keys:
                interrupt_key = interrupt_node_key(previous_key)
                interrupt_nodes.append(interrupt_key)
                node_specs[interrupt_key] = TickNodeSpec(
                    key=interrupt_key,
                    kind=NodeKind.INTERRUPT_CHECK,
                    checkpoint=False,
                    upstream=(previous_key,),
                )
                edges.append((previous_key, interrupt_key))
                node_order.append(interrupt_key)

                node_specs[tool_key] = TickNodeSpec(
                    key=tool_key,
                    kind=NodeKind.TOOL_CALL,
                    checkpoint=True,
                    upstream=(interrupt_key,),
                )
                edges.append((interrupt_key, tool_key))
                checkpoint_order.append(tool_key)
                node_order.append(tool_key)
                previous_key = tool_key

        interrupt_key = interrupt_node_key(previous_key)
        interrupt_nodes.append(interrupt_key)
        node_specs[interrupt_key] = TickNodeSpec(
            key=interrupt_key,
            kind=NodeKind.INTERRUPT_CHECK,
            checkpoint=False,
            upstream=(previous_key,),
        )
        edges.append((previous_key, interrupt_key))
        node_order.append(interrupt_key)

        phase_key = phase_node_key(phase_kind)
        node_specs[phase_key] = TickNodeSpec(
            key=phase_key,
            kind=phase_kind,
            checkpoint=True,
            upstream=(interrupt_key,),
        )
        edges.append((interrupt_key, phase_key))
        checkpoint_order.append(phase_key)
        node_order.append(phase_key)
        previous_key = phase_key

    final_interrupt_key = interrupt_node_key(previous_key)
    interrupt_nodes.append(final_interrupt_key)
    node_specs[final_interrupt_key] = TickNodeSpec(
        key=final_interrupt_key,
        kind=NodeKind.INTERRUPT_CHECK,
        checkpoint=False,
        upstream=(previous_key,),
    )
    edges.append((previous_key, final_interrupt_key))
    node_order.append(final_interrupt_key)

    state_commit_key = NodeKind.STATE_COMMIT.value
    node_specs[state_commit_key] = TickNodeSpec(
        key=state_commit_key,
        kind=NodeKind.STATE_COMMIT,
        checkpoint=False,
        upstream=(final_interrupt_key,),
    )
    edges.append((final_interrupt_key, state_commit_key))
    node_order.append(state_commit_key)

    node_specs = {
        key: TickNodeSpec(
            key=spec.key,
            kind=spec.kind,
            checkpoint=spec.checkpoint,
            actor_id=spec.actor_id,
            upstream=tuple(parent for parent, child in edges if child == key),
            downstream=tuple(child for parent, child in edges if parent == key),
        )
        for key, spec in node_specs.items()
    }

    return TickGraphPlan(
        retry_policy=retry,
        node_order=tuple(node_order),
        node_specs=node_specs,
        edges=tuple(edges),
        checkpoint_nodes=frozenset(checkpoint_order),
        checkpoint_order=tuple(checkpoint_order),
        cohort_nodes=cohort_nodes,
        hero_nodes=hero_nodes,
        actor_nodes=actor_nodes,
        barrier_nodes=(barrier_key,),
        interrupt_nodes=tuple(interrupt_nodes),
    )
