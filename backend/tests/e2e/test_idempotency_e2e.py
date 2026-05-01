"""End-to-end idempotency tests for worker scheduling helpers."""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


async def test_already_running_returns_true_on_second_call(
    monkeypatch,
    redis_client,
):
    """`already_running(key)` claims the key on first call, reports
    True on second call."""
    from backend.app.workers import scheduler as sched

    # Patch the redis_client getter to use our fake.
    monkeypatch.setattr(
        "backend.app.core.redis_client.get_redis_client",
        lambda: redis_client,
    )

    key = "sim:run-x:U-001:t1:a1"
    first = await sched.already_running(key)
    second = await sched.already_running(key)
    assert first is False  # first claim succeeded
    assert second is True  # second call sees existing claim


async def test_mark_done_caches_result_path(monkeypatch, redis_client):
    """`mark_done` + `get_done_result` round-trip the cached result path."""
    from backend.app.workers import scheduler as sched

    monkeypatch.setattr(
        "backend.app.core.redis_client.get_redis_client",
        lambda: redis_client,
    )

    key = "sim:run-y:U-002:t2:a1"
    await sched.mark_done(key, result_path="runs/BB_test/U002/tick_002")
    cached = await sched.get_done_result(key)
    assert cached == "runs/BB_test/U002/tick_002"
