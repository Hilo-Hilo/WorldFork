from app.runtime import (
    CacheKey,
    NodeKind,
    RetryPolicy,
    TickRuntimeState,
    ValidationResult,
    build_tick_graph,
)


def test_runtime_package_exports_public_symbols():
    assert TickRuntimeState.__name__ == "TickRuntimeState"
    assert NodeKind.COHORT_DECISION.value == "cohort_decision"
    assert RetryPolicy().max_attempts == 1
    assert CacheKey(namespace="tick", key="m1:t1").as_string() == "tick:v1:m1:t1"
    assert ValidationResult(ok=True).ok is True
    assert callable(build_tick_graph)
