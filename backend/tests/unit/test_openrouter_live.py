"""Live OpenRouter sanity test — opt-in via -m live_openrouter.

Skipped by default (no ``OPENROUTER_API_KEY`` env var). When run, hits the
real /v1/models endpoint with a 5s timeout — no LLM call, no spend.
"""
from __future__ import annotations

import os

import pytest

from backend.app.providers.openrouter import OpenRouterProvider


@pytest.mark.live_openrouter
async def test_openrouter_healthcheck_live() -> None:
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        pytest.skip("OPENROUTER_API_KEY not set — skipping live healthcheck")

    provider = OpenRouterProvider(
        api_key=api_key,
        base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        default_model=os.environ.get("DEFAULT_MODEL", "google/gemini-3.1-flash-lite-preview"),
        fallback_model=os.environ.get("FALLBACK_MODEL", "google/gemini-3.1-flash-lite-preview"),
        http_referer="http://localhost:3003",
        x_title="WorldFork-tests",
    )
    health = await provider.healthcheck()
    assert health.provider == "openrouter"
    assert health.ok is True, f"OpenRouter healthcheck failed: {health.details}"
    assert (health.details.get("model_count") or 0) > 0
