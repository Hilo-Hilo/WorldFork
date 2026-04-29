from app.runtime import NodeKind, TickRuntimeState, build_tick_graph


def _state() -> TickRuntimeState:
    return TickRuntimeState(
        multiverse_id="m1",
        tick_id="t1",
        last_tick_id="t0",
        cohort_ids=["cohort-a", "cohort-b"],
        hero_ids=["hero-a"],
    )


def test_build_tick_graph_fans_out_actor_nodes_and_joins_at_barrier():
    plan = build_tick_graph(_state())

    assert plan.cohort_nodes == ("cohort:cohort-a", "cohort:cohort-b")
    assert plan.hero_nodes == ("hero:hero-a",)
    assert plan.actor_nodes == (
        "cohort:cohort-a",
        "cohort:cohort-b",
        "hero:hero-a",
    )
    assert plan.barrier_nodes == ("barrier:actor_decisions",)
    assert ("cohort:cohort-a", "barrier:actor_decisions") in plan.edges
    assert ("cohort:cohort-b", "barrier:actor_decisions") in plan.edges
    assert ("hero:hero-a", "barrier:actor_decisions") in plan.edges


def test_build_tick_graph_inserts_interrupt_checks_between_checkpoint_phases():
    plan = build_tick_graph(_state())

    assert plan.interrupt_nodes == (
        "interrupt_check:after_actor_barrier",
        "interrupt_check:after_event_generation",
        "interrupt_check:after_sociology_update",
        "interrupt_check:after_graph_update",
        "interrupt_check:after_god_review",
        "interrupt_check:after_tick_summary",
    )
    assert ("barrier:actor_decisions", "interrupt_check:after_actor_barrier") in plan.edges
    assert ("interrupt_check:after_actor_barrier", "event_generation") in plan.edges
    assert ("event_generation", "interrupt_check:after_event_generation") in plan.edges
    assert ("interrupt_check:after_event_generation", "sociology_update") in plan.edges
    assert ("graph_update", "interrupt_check:after_graph_update") in plan.edges
    assert ("interrupt_check:after_tick_summary", "state_commit") in plan.edges


def test_build_tick_graph_preserves_deterministic_checkpoint_order():
    plan = build_tick_graph(_state())

    assert plan.checkpoint_order == (
        "cohort:cohort-a",
        "cohort:cohort-b",
        "hero:hero-a",
        "event_generation",
        "sociology_update",
        "graph_update",
        "god_review",
        "tick_summary",
    )
    assert plan.node_specs["cohort:cohort-a"].kind is NodeKind.COHORT_DECISION
    assert plan.node_specs["hero:hero-a"].kind is NodeKind.HERO_DECISION
    assert plan.node_specs["event_generation"].kind is NodeKind.EVENT_GENERATION
    assert plan.node_specs["state_commit"].checkpoint is False
