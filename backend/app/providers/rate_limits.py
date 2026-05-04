"""Redis-Lua token-bucket rate limiter and per-provider gate.

Implements provider rate-limit policy — RPM, TPM, max_concurrency, daily budget cap,
branch-reserved capacity slice. Token bucket is enforced in a single atomic
Lua script so concurrent workers cannot over-spend a quota.
"""
from __future__ import annotations

import asyncio
import contextlib
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from backend.app.providers.errors import BudgetExceededError, RateLimitError

if TYPE_CHECKING:
    import redis.asyncio as aioredis


# ---------------------------------------------------------------------------
# Lua: atomic token-bucket refill+consume.
#
# Args:
#   KEYS[1]      = bucket key (e.g. "rl:rpm:openrouter:bucket")
#   ARGV[1]      = capacity   (max tokens)
#   ARGV[2]      = refill_per_sec (float, tokens/sec)
#   ARGV[3]      = cost       (tokens to consume now)
#   ARGV[4]      = now_ms     (current time millis from caller — single
#                              source of truth so all workers agree)
#   ARGV[5]      = ttl_seconds (key TTL to keep idle buckets clean)
#
# State stored at KEYS[1] is a hash {tokens, last_ms}.
#
# Returns a Lua table:
#   {1, remaining_tokens}      -- success; cost was deducted
#   {-1, wait_seconds}         -- denied; estimated seconds until cost can be paid
# ---------------------------------------------------------------------------

_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill = tonumber(ARGV[2])
local cost = tonumber(ARGV[3])
local now_ms = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

local data = redis.call('HMGET', key, 'tokens', 'last_ms')
local tokens = tonumber(data[1])
local last_ms = tonumber(data[2])

if tokens == nil then
  tokens = capacity
  last_ms = now_ms
end

local elapsed = (now_ms - last_ms) / 1000.0
if elapsed < 0 then elapsed = 0 end

tokens = tokens + (elapsed * refill)
if tokens > capacity then tokens = capacity end

if tokens >= cost then
  tokens = tokens - cost
  redis.call('HMSET', key, 'tokens', tokens, 'last_ms', now_ms)
  redis.call('EXPIRE', key, ttl)
  return {1, tostring(tokens)}
else
  local need = cost - tokens
  local wait = 0.0
  if refill > 0 then wait = need / refill end
  redis.call('HMSET', key, 'tokens', tokens, 'last_ms', now_ms)
  redis.call('EXPIRE', key, ttl)
  return {-1, tostring(wait)}
end
"""

# A small refund script for crediting back unused TPM tokens after a call.
_REFUND_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local credit = tonumber(ARGV[2])
local now_ms = tonumber(ARGV[3])
local ttl = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'last_ms')
local tokens = tonumber(data[1])
if tokens == nil then tokens = capacity end

tokens = tokens + credit
if tokens > capacity then tokens = capacity end

redis.call('HMSET', key, 'tokens', tokens, 'last_ms', now_ms)
redis.call('EXPIRE', key, ttl)
return tostring(tokens)
"""

_FIXED_WINDOW_CONSUME_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local cost = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
local wait = tonumber(ARGV[4])

local used = tonumber(redis.call('GET', key))
if used == nil then used = 0 end

if used + cost <= capacity then
  local new_used = redis.call('INCRBY', key, cost)
  redis.call('EXPIRE', key, ttl)
  return {1, tostring(capacity - new_used)}
end

redis.call('EXPIRE', key, ttl)
return {-1, tostring(wait)}
"""

_FIXED_WINDOW_REFUND_LUA = """
local key = KEYS[1]
local credit = tonumber(ARGV[1])
local ttl = tonumber(ARGV[2])

local used = tonumber(redis.call('GET', key))
if used == nil then return "0" end

used = used - credit
if used < 0 then used = 0 end
redis.call('SET', key, used, 'EX', ttl)
return tostring(used)
"""


# ---------------------------------------------------------------------------
# RedisTokenBucket
# ---------------------------------------------------------------------------

class RedisTokenBucket:
    """Async token-bucket backed by Redis with an atomic Lua refill+consume."""

    def __init__(self, redis: aioredis.Redis, *, default_ttl_seconds: int = 600) -> None:
        self._redis = redis
        self._ttl = default_ttl_seconds
        self._consume_sha: str | None = None
        self._refund_sha: str | None = None

    async def _ensure_loaded(self) -> tuple[str, str]:
        """Lazy-load both Lua scripts; return their SHAs."""
        if self._consume_sha is None:
            self._consume_sha = await self._redis.script_load(_TOKEN_BUCKET_LUA)
        if self._refund_sha is None:
            self._refund_sha = await self._redis.script_load(_REFUND_LUA)
        return self._consume_sha, self._refund_sha

    async def try_consume(
        self,
        key: str,
        cost: int,
        capacity: int,
        refill_per_sec: float,
    ) -> tuple[bool, float]:
        """Single atomic attempt to consume *cost* tokens.

        Returns ``(ok, wait_seconds)``; on success ``wait_seconds`` is 0.0.
        """
        consume_sha, _ = await self._ensure_loaded()
        now_ms = int(time.time() * 1000)
        try:
            res = await self._redis.evalsha(
                consume_sha,
                1,
                key,
                str(capacity),
                str(refill_per_sec),
                str(cost),
                str(now_ms),
                str(self._ttl),
            )
        except Exception:
            # NOSCRIPT or evalsha not available — fall back to plain eval.
            res = await self._redis.eval(
                _TOKEN_BUCKET_LUA,
                1,
                key,
                str(capacity),
                str(refill_per_sec),
                str(cost),
                str(now_ms),
                str(self._ttl),
            )
        # res is a 2-element list [status, payload]
        status = int(res[0])
        payload = float(res[1])
        return (status == 1), (0.0 if status == 1 else payload)

    async def acquire(
        self,
        key: str,
        cost: int,
        capacity: int,
        refill_per_sec: float,
        timeout: float,
        *,
        jitter: bool = True,
    ) -> None:
        """Block until *cost* tokens are paid or *timeout* elapses.

        Raises :class:`RateLimitError` with ``retry_after`` set if the total
        wait exceeds ``timeout``.
        """
        deadline = time.monotonic() + timeout
        while True:
            ok, wait = await self.try_consume(key, cost, capacity, refill_per_sec)
            if ok:
                return
            # Add jitter to avoid synchronised retries under contention.
            sleep_for = wait
            if jitter:
                sleep_for = sleep_for + random.uniform(0.0, max(0.05, wait * 0.1))
            remaining = deadline - time.monotonic()
            if sleep_for >= remaining:
                raise RateLimitError(
                    f"rate-limit acquire timeout on {key} after {timeout:.1f}s",
                    retry_after=wait,
                )
            await asyncio.sleep(max(0.001, sleep_for))

    async def refund(
        self,
        key: str,
        credit: int,
        capacity: int,
    ) -> None:
        """Best-effort credit of *credit* tokens back to the bucket."""
        if credit <= 0:
            return
        _, refund_sha = await self._ensure_loaded()
        now_ms = int(time.time() * 1000)
        try:
            await self._redis.evalsha(
                refund_sha,
                1,
                key,
                str(capacity),
                str(credit),
                str(now_ms),
                str(self._ttl),
            )
        except Exception:
            await self._redis.eval(
                _REFUND_LUA,
                1,
                key,
                str(capacity),
                str(credit),
                str(now_ms),
                str(self._ttl),
            )


# ---------------------------------------------------------------------------
# Ticket — handed back from gate(); used by record_actual_usage()
# ---------------------------------------------------------------------------

@dataclass
class GateTicket:
    """Bookkeeping for one in-flight call so we can refund / record actuals."""

    provider: str
    minute_bucket: int
    estimated_tokens: int
    rpm_capacity: int
    tpm_capacity: int
    concurrency_key: str | None = None
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ProviderRateLimiter
# ---------------------------------------------------------------------------

class ProviderRateLimiter:
    """Per-provider rate, concurrency and budget gate.

    Backed by a :class:`RedisTokenBucket` for RPM/TPM and a per-provider
    semaphore implemented via Redis ``INCR``/``DECR`` (so multiple worker
    processes share the same concurrency budget).
    """

    def __init__(
        self,
        redis: aioredis.Redis,
        *,
        provider: str,
        rpm_limit: int,
        tpm_limit: int,
        max_concurrency: int,
        branch_reserved_capacity_pct: float = 20.0,
        daily_budget_usd: float | None = None,
        burst_multiplier: float = 1.2,
        jitter: bool = True,
    ) -> None:
        self._redis = redis
        self.provider = provider
        self.rpm_limit = rpm_limit
        self.tpm_limit = tpm_limit
        self.max_concurrency = max_concurrency
        self.branch_reserved_pct = max(0.0, min(100.0, branch_reserved_capacity_pct))
        self.daily_budget_usd = daily_budget_usd
        self.burst_multiplier = max(1.0, burst_multiplier)
        self.jitter = jitter
        self._bucket = RedisTokenBucket(redis)
        self._fixed_consume_sha: str | None = None
        self._fixed_refund_sha: str | None = None

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    def _minute_bucket(self) -> int:
        return int(time.time() // 60)

    def _day_bucket(self) -> str:
        return time.strftime("%Y%m%d", time.gmtime())

    def _rpm_key(self, minute: int) -> str:
        return f"rl:rpm:{self.provider}:{minute}"

    def _tpm_key(self, minute: int) -> str:
        return f"rl:tpm:{self.provider}:{minute}"

    def _conc_key(self) -> str:
        return f"rl:conc:{self.provider}"

    def _budget_key(self) -> str:
        return f"rl:budget:{self.provider}:{self._day_bucket()}"

    def _seconds_until_next_minute(self) -> float:
        return max(0.001, 60.0 - (time.time() % 60.0))

    def _fixed_window_ttl(self) -> int:
        return max(2, int(self._seconds_until_next_minute()) + 2)

    async def _ensure_fixed_window_loaded(self) -> tuple[str, str]:
        if self._fixed_consume_sha is None:
            self._fixed_consume_sha = await self._redis.script_load(_FIXED_WINDOW_CONSUME_LUA)
        if self._fixed_refund_sha is None:
            self._fixed_refund_sha = await self._redis.script_load(_FIXED_WINDOW_REFUND_LUA)
        return self._fixed_consume_sha, self._fixed_refund_sha

    async def _try_consume_fixed_window(
        self,
        key: str,
        cost: int,
        capacity: int,
    ) -> tuple[bool, float]:
        consume_sha, _ = await self._ensure_fixed_window_loaded()
        wait = self._seconds_until_next_minute()
        ttl = self._fixed_window_ttl()
        try:
            res = await self._redis.evalsha(
                consume_sha,
                1,
                key,
                str(capacity),
                str(cost),
                str(ttl),
                str(wait),
            )
        except Exception:
            res = await self._redis.eval(
                _FIXED_WINDOW_CONSUME_LUA,
                1,
                key,
                str(capacity),
                str(cost),
                str(ttl),
                str(wait),
            )
        status = int(res[0])
        payload = float(res[1])
        return (status == 1), (0.0 if status == 1 else payload)

    async def _acquire_fixed_window(
        self,
        key_for_minute,
        cost: int,
        capacity: int,
        timeout: float,
    ) -> int:
        deadline = time.monotonic() + timeout
        while True:
            minute = self._minute_bucket()
            key = key_for_minute(minute)
            ok, wait = await self._try_consume_fixed_window(key, cost, capacity)
            if ok:
                return minute
            sleep_for = wait
            if self.jitter:
                sleep_for += random.uniform(0.0, max(0.05, wait * 0.1))
            remaining = deadline - time.monotonic()
            if sleep_for >= remaining:
                raise RateLimitError(
                    f"rate-limit acquire timeout on {key} after {timeout:.1f}s",
                    retry_after=wait,
                )
            await asyncio.sleep(max(0.001, sleep_for))

    async def _refund_fixed_window(self, key: str, credit: int) -> None:
        if credit <= 0:
            return
        _, refund_sha = await self._ensure_fixed_window_loaded()
        ttl = self._fixed_window_ttl()
        try:
            await self._redis.evalsha(refund_sha, 1, key, str(credit), str(ttl))
        except Exception:
            await self._redis.eval(
                _FIXED_WINDOW_REFUND_LUA,
                1,
                key,
                str(credit),
                str(ttl),
            )

    # ------------------------------------------------------------------
    # Concurrency gate via Redis INCR (cluster-safe distributed semaphore)
    # ------------------------------------------------------------------

    async def _acquire_concurrency(self, *, is_branch_job: bool, is_p0: bool) -> None:
        """Increment the per-provider in-flight counter, blocking if at cap.

        When ``is_branch_job`` and not ``is_p0``, the effective cap is reduced
        by the branch-reserved capacity slice so that high-priority traffic
        (P0 sim ticks) always has headroom.
        """
        cap = self.max_concurrency
        if is_branch_job and not is_p0 and self.branch_reserved_pct > 0:
            reserved = int(cap * (self.branch_reserved_pct / 100.0))
            cap = max(1, cap - reserved)

        key = self._conc_key()
        # Loop with bounded backoff; we don't want to busy-spin Redis.
        attempts = 0
        while True:
            value = await self._redis.incr(key)
            # Refresh TTL so dead workers clean up after themselves.
            await self._redis.expire(key, 60)
            if value <= cap:
                return
            # Over cap; release and wait.
            await self._redis.decr(key)
            attempts += 1
            sleep_for = min(2.0, 0.05 * (2 ** min(attempts, 5)))
            if self.jitter:
                sleep_for += random.uniform(0.0, 0.05)
            await asyncio.sleep(sleep_for)

    async def _release_concurrency(self) -> None:
        try:
            await self._redis.decr(self._conc_key())
        except Exception:
            # Never let release errors mask the real exception.
            pass

    # ------------------------------------------------------------------
    # Daily budget
    # ------------------------------------------------------------------

    async def check_daily_budget(self, projected_usd: float) -> None:
        """Raise :class:`BudgetExceededError` if ``projected_usd`` would breach the cap."""
        if self.daily_budget_usd is None:
            return
        spent_raw = await self._redis.get(self._budget_key())
        spent = float(spent_raw) if spent_raw is not None else 0.0
        if spent + max(0.0, projected_usd) > self.daily_budget_usd:
            raise BudgetExceededError(
                f"daily budget exceeded for {self.provider}: "
                f"spent={spent:.4f} cap={self.daily_budget_usd}",
                provider=self.provider,
                daily_spent=spent,
                cap=self.daily_budget_usd,
            )

    async def _record_spend(self, cost_usd: float) -> None:
        if self.daily_budget_usd is None or cost_usd <= 0:
            return
        key = self._budget_key()
        # incrbyfloat rounds; precision is fine for budget tracking
        await self._redis.incrbyfloat(key, cost_usd)
        await self._redis.expire(key, 60 * 60 * 36)  # 36h survives DST and clock skew

    # ------------------------------------------------------------------
    # Public gate context manager
    # ------------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def gate(
        self,
        estimated_tokens: int,
        *,
        is_branch_job: bool = False,
        is_p0: bool = False,
        timeout: float = 60.0,
    ):
        """Async context manager that enforces RPM, TPM, and concurrency.

        Yields a :class:`GateTicket` callers can pass to ``record_actual_usage``.
        """
        rpm_cap = max(1, int(self.rpm_limit * self.burst_multiplier))
        tpm_cap = max(1, int(self.tpm_limit * self.burst_multiplier))

        cost_tokens = max(1, int(estimated_tokens))

        # 1. RPM (one request).
        rpm_minute = await self._acquire_fixed_window(
            self._rpm_key, 1, rpm_cap, timeout=timeout
        )
        # 2. TPM (estimated tokens).
        try:
            minute = await self._acquire_fixed_window(
                self._tpm_key, cost_tokens, tpm_cap, timeout=timeout
            )
        except RateLimitError:
            # Refund the RPM token; we never made the request.
            await self._refund_fixed_window(self._rpm_key(rpm_minute), 1)
            raise
        # 3. Concurrency.
        try:
            await self._acquire_concurrency(is_branch_job=is_branch_job, is_p0=is_p0)
        except Exception:
            await self._refund_fixed_window(self._rpm_key(rpm_minute), 1)
            await self._refund_fixed_window(self._tpm_key(minute), cost_tokens)
            raise

        ticket = GateTicket(
            provider=self.provider,
            minute_bucket=minute,
            estimated_tokens=cost_tokens,
            rpm_capacity=rpm_cap,
            tpm_capacity=tpm_cap,
            concurrency_key=self._conc_key(),
        )
        try:
            yield ticket
        finally:
            await self._release_concurrency()

    # ------------------------------------------------------------------
    # Post-call accounting
    # ------------------------------------------------------------------

    async def record_actual_usage(
        self,
        ticket: GateTicket | None,
        actual_tokens: int,
        cost_usd: float,
    ) -> None:
        """Refund overestimated tokens and record actual spend.

        - If we estimated more tokens than we used, credit the difference back
          to the TPM bucket so the provider isn't artificially throttled.
        - We never *add* tokens to the bucket if actual > estimated — instead
          the next call will pay for the deficit naturally.
        """
        if ticket is not None and actual_tokens >= 0:
            unused = ticket.estimated_tokens - max(0, actual_tokens)
            if unused > 0:
                tpm_key = self._tpm_key(ticket.minute_bucket)
                await self._refund_fixed_window(tpm_key, unused)
        if cost_usd > 0:
            await self._record_spend(cost_usd)
