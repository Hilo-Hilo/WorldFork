"""Tests for the Redis-Lua token-bucket rate limiter (B2-B)."""
from __future__ import annotations

import asyncio
import time

import fakeredis.aioredis
import pytest
import pytest_asyncio

from backend.app.providers.errors import BudgetExceededError, RateLimitError
from backend.app.providers.rate_limits import ProviderRateLimiter, RedisTokenBucket
from backend.app.providers.routing import build_provider_rate_limiter

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def redis_client():
    """Yield a fresh fakeredis async client per test."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose(close_connection_pool=True)


# ---------------------------------------------------------------------------
# RedisTokenBucket
# ---------------------------------------------------------------------------

async def test_token_bucket_consume_success(redis_client) -> None:
    bucket = RedisTokenBucket(redis_client)
    ok, wait = await bucket.try_consume("rl:test:1", cost=10, capacity=100, refill_per_sec=10.0)
    assert ok is True
    assert wait == 0.0


async def test_token_bucket_denies_when_empty(redis_client) -> None:
    bucket = RedisTokenBucket(redis_client)
    # Spend the whole capacity in one shot.
    ok, _ = await bucket.try_consume("rl:test:2", cost=100, capacity=100, refill_per_sec=1.0)
    assert ok is True
    # Next call must fail and report a positive wait.
    ok, wait = await bucket.try_consume("rl:test:2", cost=10, capacity=100, refill_per_sec=1.0)
    assert ok is False
    assert wait > 0.0


async def test_token_bucket_refills_over_time(redis_client) -> None:
    bucket = RedisTokenBucket(redis_client)
    key = "rl:test:refill"
    # Drain.
    await bucket.try_consume(key, cost=100, capacity=100, refill_per_sec=100.0)
    # Wait > 0.5s to refill ~50 tokens.
    await asyncio.sleep(0.6)
    ok, _ = await bucket.try_consume(key, cost=40, capacity=100, refill_per_sec=100.0)
    assert ok is True


async def test_token_bucket_acquire_raises_on_timeout(redis_client) -> None:
    bucket = RedisTokenBucket(redis_client)
    key = "rl:test:acq"
    # Drain entire bucket.
    await bucket.try_consume(key, cost=100, capacity=100, refill_per_sec=0.1)
    # Asking for 100 more with refill 0.1/s means ~1000s wait.
    t0 = time.monotonic()
    with pytest.raises(RateLimitError) as exc_info:
        await bucket.acquire(key, cost=100, capacity=100, refill_per_sec=0.1, timeout=0.3)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.5  # raised quickly, didn't actually sleep 1000s
    assert exc_info.value.retry_after is not None and exc_info.value.retry_after > 0


async def test_token_bucket_refund(redis_client) -> None:
    bucket = RedisTokenBucket(redis_client)
    key = "rl:test:refund"
    await bucket.try_consume(key, cost=80, capacity=100, refill_per_sec=1.0)
    # Refund 50 — bucket should now have ~70 (capped at 100).
    await bucket.refund(key, credit=50, capacity=100)
    ok, _ = await bucket.try_consume(key, cost=60, capacity=100, refill_per_sec=1.0)
    assert ok is True


# ---------------------------------------------------------------------------
# ProviderRateLimiter
# ---------------------------------------------------------------------------

async def test_provider_rate_limiter_gate_acquires_and_releases(redis_client) -> None:
    limiter = ProviderRateLimiter(
        redis_client,
        provider="testprov",
        rpm_limit=600,
        tpm_limit=1_000_000,
        max_concurrency=4,
        daily_budget_usd=None,
    )
    async with limiter.gate(estimated_tokens=1000) as ticket:
        # Concurrency counter should be 1 inside the gate.
        value = await redis_client.get("rl:conc:testprov")
        assert int(value) == 1
        assert ticket.estimated_tokens == 1000
    # After exit, decremented.
    value = await redis_client.get("rl:conc:testprov")
    assert int(value) == 0


async def test_build_provider_rate_limiter_uses_enabled_db_row(redis_client) -> None:
    class _Row:
        enabled = True
        rpm_limit = 17
        tpm_limit = 1800
        max_concurrency = 3
        burst_multiplier = 1.5
        retry_policy = "exponential_backoff"
        jitter = True
        daily_budget_usd = 0.25
        branch_reserved_capacity_pct = 15.0

    class _Session:
        async def get(self, _model, provider):
            assert provider == "openrouter"
            return _Row()

    limiter = await build_provider_rate_limiter(_Session(), redis_client, provider="openrouter")

    assert limiter.rpm_limit == 17
    assert limiter.tpm_limit == 1800
    assert limiter.max_concurrency == 3
    assert limiter.burst_multiplier == 1.5
    assert limiter.daily_budget_usd == 0.25
    assert limiter.branch_reserved_pct == 15.0
    assert limiter.jitter is True


async def test_provider_rate_limiter_does_not_refill_fixed_minute(redis_client) -> None:
    limiter = ProviderRateLimiter(
        redis_client,
        provider="fixedminute",
        rpm_limit=2,
        tpm_limit=1_000_000,
        max_concurrency=4,
        burst_multiplier=1.0,
        jitter=False,
    )

    async with limiter.gate(estimated_tokens=10, timeout=0.1):
        pass
    async with limiter.gate(estimated_tokens=10, timeout=0.1):
        pass
    await asyncio.sleep(0.05)

    with pytest.raises(RateLimitError) as exc_info:
        async with limiter.gate(estimated_tokens=10, timeout=0.1):
            pass

    assert exc_info.value.retry_after is not None
    assert exc_info.value.retry_after > 0


async def test_provider_rate_limiter_concurrency_caps(redis_client) -> None:
    limiter = ProviderRateLimiter(
        redis_client,
        provider="cap",
        rpm_limit=6000,
        tpm_limit=10_000_000,
        max_concurrency=2,
        jitter=False,
    )

    started = asyncio.Event()
    release = asyncio.Event()
    in_flight: list[int] = []

    async def worker(idx: int) -> None:
        async with limiter.gate(estimated_tokens=10):
            in_flight.append(idx)
            started.set()
            await release.wait()
            in_flight.remove(idx)

    t1 = asyncio.create_task(worker(1))
    t2 = asyncio.create_task(worker(2))
    await started.wait()
    # Now both should be in-flight; a third must wait.
    t3 = asyncio.create_task(worker(3))
    await asyncio.sleep(0.1)
    assert 3 not in in_flight  # blocked on concurrency
    release.set()
    await asyncio.gather(t1, t2, t3)


async def test_provider_rate_limiter_budget_check(redis_client) -> None:
    limiter = ProviderRateLimiter(
        redis_client,
        provider="bud",
        rpm_limit=600,
        tpm_limit=1_000_000,
        max_concurrency=4,
        daily_budget_usd=1.0,
    )
    # First call: well under budget.
    await limiter.check_daily_budget(0.10)
    await limiter._record_spend(0.50)
    # Second call: 0.50 + 0.40 = 0.90 < 1.0 → ok.
    await limiter.check_daily_budget(0.40)
    await limiter._record_spend(0.40)
    # Third call: 0.90 + 0.20 > 1.0 → raises.
    with pytest.raises(BudgetExceededError) as exc_info:
        await limiter.check_daily_budget(0.20)
    assert exc_info.value.provider == "bud"
    assert exc_info.value.cap == 1.0
    assert exc_info.value.daily_spent >= 0.89  # float tolerance


async def test_provider_rate_limiter_record_actual_refunds(redis_client) -> None:
    limiter = ProviderRateLimiter(
        redis_client,
        provider="refund",
        rpm_limit=6000,
        tpm_limit=10_000,  # small so refund is observable
        max_concurrency=4,
    )
    # First gate: estimate large, use small.
    async with limiter.gate(estimated_tokens=8000) as ticket:
        pass
    # Used 100 of estimated 8000; refund should credit ~7900 back.
    await limiter.record_actual_usage(ticket, actual_tokens=100, cost_usd=0.0)

    # Second gate should be able to consume 7000 without issue.
    async with limiter.gate(estimated_tokens=7000) as ticket2:
        assert ticket2.estimated_tokens == 7000


async def test_provider_rate_limiter_branch_reservation(redis_client) -> None:
    limiter = ProviderRateLimiter(
        redis_client,
        provider="branchres",
        rpm_limit=6000,
        tpm_limit=10_000_000,
        max_concurrency=10,
        branch_reserved_capacity_pct=50.0,  # branch jobs see cap=5
        jitter=False,
    )

    started = asyncio.Event()
    release = asyncio.Event()

    async def branch_worker(idx: int) -> None:
        async with limiter.gate(estimated_tokens=10, is_branch_job=True, is_p0=False):
            started.set()
            await release.wait()

    # Spawn 5 branch workers — fills the branch-effective cap of 5.
    workers = [asyncio.create_task(branch_worker(i)) for i in range(5)]
    await started.wait()
    await asyncio.sleep(0.05)
    # The 6th must block.
    blocker = asyncio.create_task(branch_worker(99))
    await asyncio.sleep(0.1)
    assert not blocker.done()
    release.set()
    await asyncio.gather(*workers, blocker)
