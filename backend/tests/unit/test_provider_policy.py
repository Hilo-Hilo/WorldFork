"""Tests for backend.app.providers.call_with_policy (B2-B §16.7 backoff)."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import fakeredis.aioredis
import pytest
import pytest_asyncio

from backend.app.providers import (
    BudgetExceededError,
    FallbackExhaustedError,
    InvalidJSONError,
    ProviderError,
    ProviderRateLimiter,
    ProviderTimeoutError,
    RateLimitError,
    RoutingTable,
    call_with_policy,
    clear_registry,
    register_provider,
)
from backend.app.providers.base import BaseProvider
from backend.app.schemas.common import Clock
from backend.app.schemas.llm import LLMResult, PromptPacket

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture
def limiter(redis_client) -> ProviderRateLimiter:
    return ProviderRateLimiter(
        redis_client,
        provider="openrouter",
        rpm_limit=600,
        tpm_limit=1_000_000,
        max_concurrency=8,
        daily_budget_usd=None,
        jitter=False,
    )


@pytest.fixture
def routing() -> RoutingTable:
    return RoutingTable.defaults()


@pytest.fixture
def prompt() -> PromptPacket:
    return PromptPacket(
        system="be brief",
        clock=Clock(
            current_tick=1,
            tick_duration_minutes=120,
            elapsed_minutes=120,
            previous_tick_minutes=120,
            max_schedule_horizon_ticks=5,
        ),
        actor_id="cohort_test",
        actor_kind="cohort",
        archetype=None,
        state={},
        sot_excerpt={},
        visible_feed=[],
        visible_events=[],
        own_queued_events=[],
        own_recent_actions=[],
        retrieved_memory=None,
        allowed_tools=["stay_silent"],
        output_schema_id="cohort_decision_schema",
        temperature=0.7,
        metadata={},
    )


@pytest.fixture(autouse=True)
def _clear_registry_around_tests():
    clear_registry()
    yield
    clear_registry()


# ---------------------------------------------------------------------------
# Mock provider
# ---------------------------------------------------------------------------

class MockProvider(BaseProvider):
    """Scriptable mock provider — pop the next response off ``script``."""

    def __init__(self, name: str = "openrouter") -> None:
        self.name = name
        self.script: list[Exception | LLMResult] = []
        self.calls: int = 0

    def _build_result(self, model: str) -> LLMResult:
        return LLMResult(
            call_id=self._make_call_id("mock"),
            provider=self.name,
            model_used=model,
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            cost_usd=0.001,
            latency_ms=20,
            parsed_json={"social_actions": [{"tool_id": "stay_silent", "args": {}}]},
            tool_calls=[],
            raw_response={},
            created_at=datetime.now(UTC),
        )

    async def generate_structured(self, prompt, config):
        self.calls += 1
        if not self.script:
            return self._build_result(config.model)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def generate_text(self, prompt, config):
        return await self.generate_structured(prompt, config)

    async def embed(self, texts, config):  # pragma: no cover
        raise NotImplementedError

    async def healthcheck(self):  # pragma: no cover
        from backend.app.schemas.llm import ProviderHealth
        return ProviderHealth(provider=self.name, ok=True, latency_ms=1, details={})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_happy_path(routing, limiter, prompt) -> None:
    provider = MockProvider()
    register_provider("openrouter", provider)

    result = await call_with_policy(
        job_type="agent_deliberation_batch",
        prompt=prompt,
        routing=routing,
        limiter=limiter,
        run_id="run_test",
        universe_id="U000",
        tick=1,
    )
    assert result.parsed_json == {"social_actions": [{"tool_id": "stay_silent", "args": {}}]}
    assert provider.calls == 1


async def test_sync_actor_decision_model_uses_agent_deliberation_routing(monkeypatch) -> None:
    from backend.app.simulation import agent_engine

    class _Result:
        def mappings(self):
            return self

        def first(self):
            return {"preferred_model": "db/agent-model"}

    class _Db:
        def execute(self, statement, params):
            assert params == {"job_type": "agent_deliberation_batch"}
            return _Result()

    monkeypatch.setattr(
        agent_engine,
        "get_settings",
        lambda: SimpleNamespace(default_model="settings/default-model"),
    )

    assert agent_engine._agent_deliberation_model(_Db()) == "db/agent-model"


async def test_429_then_success(routing, limiter, prompt) -> None:
    provider = MockProvider()
    provider.script = [RateLimitError("429", retry_after=0.05)]
    register_provider("openrouter", provider)

    result = await call_with_policy(
        job_type="agent_deliberation_batch",
        prompt=prompt,
        routing=routing,
        limiter=limiter,
        run_id="run_test",
        max_attempts=3,
    )
    assert result.parsed_json is not None
    assert provider.calls == 2  # one 429 + one success


async def test_5xx_triggers_fallback(routing, limiter, prompt) -> None:
    primary = MockProvider(name="openrouter")
    # Primary always fails with a generic ProviderError (treated like 5xx).
    primary.script = [ProviderError("upstream 503")] * 6
    register_provider("openrouter", primary)
    # Fallback succeeds on first try. The default routing has both primary and
    # fallback under the openrouter provider — so we share one mock that flips.

    # Custom: build a routing table where fallback uses a separate provider.
    from backend.app.schemas.settings import ModelRoutingEntry

    entry = ModelRoutingEntry(
        job_type="agent_deliberation_batch",
        preferred_provider="openrouter",
        preferred_model="primary-model",
        fallback_provider="fbprovider",
        fallback_model="fb-model",
        temperature=0.5,
        top_p=0.95,
        max_tokens=512,
        max_concurrency=4,
        requests_per_minute=60,
        tokens_per_minute=150_000,
        timeout_seconds=120,
        retry_policy="exponential_backoff",
        daily_budget_usd=None,
    )
    routing_custom = RoutingTable({"agent_deliberation_batch": entry})  # type: ignore[arg-type]

    fallback = MockProvider(name="fbprovider")
    register_provider("fbprovider", fallback)

    result = await call_with_policy(
        job_type="agent_deliberation_batch",
        prompt=prompt,
        routing=routing_custom,
        limiter=limiter,
        run_id="run_test",
        max_attempts=2,
    )
    assert result.model_used == "fb-model"
    assert primary.calls >= 2
    assert fallback.calls == 1


async def test_invalid_json_returns_safe_noop(routing, limiter, prompt) -> None:
    provider = MockProvider()
    provider.script = [InvalidJSONError("bad", raw_text="not json", validator_message="parse")]
    register_provider("openrouter", provider)

    result = await call_with_policy(
        job_type="agent_deliberation_batch",
        prompt=prompt,
        routing=routing,
        limiter=limiter,
        run_id="run_test",
        max_attempts=2,
    )
    assert result.parsed_json is not None
    # Safe no-op should include a stay_silent action.
    assert result.parsed_json["social_actions"][0]["tool_id"] == "stay_silent"
    assert result.repaired_once is True


async def test_budget_exceeded_propagates(redis_client, routing, prompt) -> None:
    # Tight $0.001 budget; one call alone projects more than that.
    limiter = ProviderRateLimiter(
        redis_client,
        provider="openrouter",
        rpm_limit=600,
        tpm_limit=1_000_000,
        max_concurrency=4,
        daily_budget_usd=0.000001,
        jitter=False,
    )
    provider = MockProvider()
    register_provider("openrouter", provider)

    with pytest.raises(BudgetExceededError):
        await call_with_policy(
            job_type="agent_deliberation_batch",
            prompt=prompt,
            routing=routing,
            limiter=limiter,
            run_id="run_test",
        )
    # Budget check happens before the provider call.
    assert provider.calls == 0


async def test_fallback_exhausted_raises(routing, limiter, prompt) -> None:
    provider = MockProvider()
    # Always rate-limited; quick retry-after to keep the test fast.
    provider.script = [RateLimitError("429", retry_after=0.01)] * 50
    register_provider("openrouter", provider)

    with pytest.raises(FallbackExhaustedError):
        await call_with_policy(
            job_type="agent_deliberation_batch",
            prompt=prompt,
            routing=routing,
            limiter=limiter,
            run_id="run_test",
            max_attempts=2,
        )


async def test_timeout_then_fallback(routing, limiter, prompt) -> None:
    # Custom routing so we can distinguish primary vs fallback providers.
    from backend.app.schemas.settings import ModelRoutingEntry
    entry = ModelRoutingEntry(
        job_type="agent_deliberation_batch",
        preferred_provider="primary",
        preferred_model="m1",
        fallback_provider="fbprovider",
        fallback_model="m2",
        temperature=0.5,
        top_p=0.95,
        max_tokens=512,
        max_concurrency=4,
        requests_per_minute=60,
        tokens_per_minute=150_000,
        timeout_seconds=120,
        retry_policy="exponential_backoff",
        daily_budget_usd=None,
    )
    routing_custom = RoutingTable({"agent_deliberation_batch": entry})  # type: ignore[arg-type]

    primary = MockProvider(name="primary")
    primary.script = [ProviderTimeoutError("t/o"), ProviderTimeoutError("t/o")]
    register_provider("primary", primary)

    fb = MockProvider(name="fbprovider")
    register_provider("fbprovider", fb)

    result = await call_with_policy(
        job_type="agent_deliberation_batch",
        prompt=prompt,
        routing=routing_custom,
        limiter=limiter,
        run_id="run_test",
        max_attempts=3,
    )
    assert result.model_used == "m2"
    assert primary.calls == 2  # one retry, then fallback
    assert fb.calls == 1
