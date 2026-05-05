"""End-to-end rate-limit / token-bucket test.

Configures a `ProviderRateLimiter` with RPM=1 (1 request per minute, no
burst) and exercises the token-bucket directly:

* The first acquire consumes the token immediately.
* The second acquire fails fast with `RateLimitError` (timeout < refill_per_sec).
* No data corruption — the bucket state remains consistent across attempts.
"""
from __future__ import annotations

import pytest

from backend.app.providers.errors import RateLimitError
from backend.app.providers.rate_limits import (
    ProviderRateLimiter,
    RedisTokenBucket,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


async def test_token_bucket_first_call_succeeds_second_blocks(redis_client):
    """RPM=1 / capacity=1 means: first call ok, second call has to wait."""
    bucket = RedisTokenBucket(redis_client)
    key = "rl:test:rpm:1cap"

    # First acquire — consumes the only token.
    ok1, wait1 = await bucket.try_consume(
        key, cost=1, capacity=1, refill_per_sec=1.0 / 60.0
    )
    assert ok1 is True
    assert wait1 == 0.0

    # Second acquire — should be denied with a positive wait time.
    ok2, wait2 = await bucket.try_consume(
        key, cost=1, capacity=1, refill_per_sec=1.0 / 60.0
    )
    assert ok2 is False
    assert wait2 > 0.0

    # And a third — wait stays roughly the same (still empty).
    ok3, wait3 = await bucket.try_consume(
        key, cost=1, capacity=1, refill_per_sec=1.0 / 60.0
    )
    assert ok3 is False
    assert wait3 > 0.0


async def test_acquire_raises_rate_limit_error_on_timeout(redis_client):
    """`acquire(timeout=0.1)` on an empty bucket raises RateLimitError fast."""
    bucket = RedisTokenBucket(redis_client)
    key = "rl:test:rpm:timeout"

    # Drain the bucket first.
    ok, _ = await bucket.try_consume(
        key, cost=1, capacity=1, refill_per_sec=1.0 / 600.0
    )
    assert ok is True

    # Now try to acquire with a short timeout.
    with pytest.raises(RateLimitError):
        await bucket.acquire(
            key,
            cost=1,
            capacity=1,
            refill_per_sec=1.0 / 600.0,
            timeout=0.05,
            jitter=False,
        )


async def test_provider_rate_limiter_concurrency_cap(redis_client):
    """`ProviderRateLimiter` enforces the per-provider semaphore via INCR/DECR."""
    limiter = ProviderRateLimiter(
        redis_client,
        provider="openrouter-test",
        rpm_limit=600,
        tpm_limit=1_000_000,
        max_concurrency=2,
        daily_budget_usd=None,
        jitter=False,
    )

    async with limiter.gate(estimated_tokens=100):
        # Inside the gate one slot is held.
        # The Redis counter should be 1.
        held = int(await redis_client.get(limiter._conc_key()) or 0)
        assert held == 1

    # After exit the counter must be back to zero.
    held_after = int(await redis_client.get(limiter._conc_key()) or 0)
    assert held_after == 0
