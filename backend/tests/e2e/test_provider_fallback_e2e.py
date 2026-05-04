"""End-to-end provider-fallback test.

Configures a routing entry whose primary provider raises a 5xx-ish
ProviderError on the first attempt and succeeds on the second; the
`call_with_policy` orchestrator must back off, retry, and ultimately
return a valid LLMResult.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.app.providers import (
    ProviderRateLimiter,
    RoutingTable,
    call_with_policy,
    clear_registry,
    register_provider,
)
from backend.app.providers.base import BaseProvider
from backend.app.providers.errors import ProviderError
from backend.app.schemas.common import Clock
from backend.app.schemas.llm import (
    LLMResult,
    PromptPacket,
    ProviderHealth,
)
from backend.app.schemas.settings import ModelRoutingEntry

pytestmark = [pytest.mark.asyncio, pytest.mark.e2e]


class _FlakyPrimary(BaseProvider):
    """Provider that fails the first call with ProviderError, then succeeds."""

    def __init__(self) -> None:
        self.name = "openrouter"
        self.calls = 0

    async def generate_structured(self, prompt, config):
        self.calls += 1
        if self.calls == 1:
            raise ProviderError("simulated upstream 502")
        return LLMResult(
            call_id=self._make_call_id("primary"),
            provider=self.name,
            model_used=config.model,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            cost_usd=0.0001,
            latency_ms=5,
            parsed_json={"ok": True, "via": "primary_after_retry"},
            tool_calls=[],
            raw_response={},
            created_at=datetime.now(UTC),
            repaired_once=False,
        )

    async def generate_text(self, prompt, config):
        return await self.generate_structured(prompt, config)

    async def embed(self, texts, config):
        raise NotImplementedError

    async def healthcheck(self):
        return ProviderHealth(provider=self.name, ok=True, latency_ms=1, details={})


class _AlwaysFailingPrimary(_FlakyPrimary):
    """Variant that fails every attempt — forces fallback path."""

    async def generate_structured(self, prompt, config):
        self.calls += 1
        raise ProviderError("primary always 502")


class _GoodFallback(BaseProvider):
    def __init__(self) -> None:
        self.name = "fallback_provider"
        self.calls = 0

    async def generate_structured(self, prompt, config):
        self.calls += 1
        return LLMResult(
            call_id=self._make_call_id("fallback"),
            provider=self.name,
            model_used=config.model,
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            cost_usd=0.00005,
            latency_ms=8,
            parsed_json={"ok": True, "via": "fallback"},
            tool_calls=[],
            raw_response={},
            created_at=datetime.now(UTC),
            repaired_once=False,
        )

    async def generate_text(self, prompt, config):
        return await self.generate_structured(prompt, config)

    async def embed(self, texts, config):
        raise NotImplementedError

    async def healthcheck(self):
        return ProviderHealth(provider=self.name, ok=True, latency_ms=1, details={})


def _make_routing_with_fallback() -> RoutingTable:
    """Routing table whose actor_deliberation_call entry has a fallback."""
    entry = ModelRoutingEntry(
        job_type="actor_deliberation_call",
        preferred_provider="openrouter",
        preferred_model="openai/gpt-4o",
        fallback_provider="fallback_provider",
        fallback_model="openai/gpt-4o-mini",
        temperature=0.6,
        top_p=0.95,
        max_tokens=1024,
        max_concurrency=4,
        requests_per_minute=60,
        tokens_per_minute=150_000,
        timeout_seconds=120,
        retry_policy="exponential_backoff",
        daily_budget_usd=None,
    )
    return RoutingTable({"actor_deliberation_call": entry})


def _make_packet() -> PromptPacket:
    """Minimal prompt packet for `actor_deliberation_call`."""
    return PromptPacket(
        system="You are a brief test cohort.",
        clock=Clock(
            current_tick=1,
            tick_duration_minutes=60,
            elapsed_minutes=60,
            previous_tick_minutes=0,
            max_schedule_horizon_ticks=5,
        ),
        actor_id="coh-1",
        actor_kind="cohort",
        archetype=None,
        state={},
        sot_excerpt={},
        visible_feed=[],
        visible_events=[],
        own_queued_events=[],
        own_recent_actions=[],
        retrieved_memory=None,
        allowed_tools=[],
        output_schema_id="cohort_decision_schema",
        temperature=0.5,
        metadata={"job_type": "actor_deliberation_call"},
    )


async def test_primary_recovers_after_one_5xx(
    monkeypatch,
    redis_client,
):
    """Primary fails once with ProviderError, then succeeds on retry."""
    clear_registry()
    flaky = _FlakyPrimary()
    register_provider("openrouter", flaky)

    # Speed up the test — backoff sleeps must not stall.
    monkeypatch.setattr("backend.app.providers._jitter", lambda base=0.25: 0.0)
    monkeypatch.setattr("asyncio.sleep", _instant_sleep)

    routing = _make_routing_with_fallback()
    limiter = ProviderRateLimiter(
        redis_client,
        provider="openrouter",
        rpm_limit=600,
        tpm_limit=1_000_000,
        max_concurrency=8,
        daily_budget_usd=None,
        jitter=False,
    )

    result = await call_with_policy(
        job_type="actor_deliberation_call",
        prompt=_make_packet(),
        routing=routing,
        limiter=limiter,
        run_id="run-test",
        universe_id="U_test",
        tick=1,
    )

    # Two attempts on the primary — first failed, second succeeded.
    assert flaky.calls == 2
    assert result.parsed_json == {"ok": True, "via": "primary_after_retry"}
    assert result.model_used == "openai/gpt-4o"


async def test_primary_exhausts_then_fallback_takes_over(
    monkeypatch,
    redis_client,
):
    """Primary keeps failing → orchestrator falls back to the fallback model."""
    clear_registry()
    primary = _AlwaysFailingPrimary()
    fallback = _GoodFallback()
    register_provider("openrouter", primary)
    register_provider("fallback_provider", fallback)

    monkeypatch.setattr("backend.app.providers._jitter", lambda base=0.25: 0.0)
    monkeypatch.setattr("asyncio.sleep", _instant_sleep)

    routing = _make_routing_with_fallback()
    limiter = ProviderRateLimiter(
        redis_client,
        provider="openrouter",
        rpm_limit=600,
        tpm_limit=1_000_000,
        max_concurrency=8,
        daily_budget_usd=None,
        jitter=False,
    )

    # max_attempts=3 keeps the test fast; backoff doubles 1→2→4→8→16 seconds
    # but we replaced asyncio.sleep with a no-op so it's instant.
    result = await call_with_policy(
        job_type="actor_deliberation_call",
        prompt=_make_packet(),
        routing=routing,
        limiter=limiter,
        run_id="run-test",
        universe_id="U_test",
        tick=1,
        max_attempts=6,
    )
    assert primary.calls >= 1
    assert fallback.calls == 1
    assert result.parsed_json == {"ok": True, "via": "fallback"}
    assert result.provider == "fallback_provider"


async def _instant_sleep(_seconds: float) -> None:
    """Replacement for asyncio.sleep — yields without delay."""
    return None
