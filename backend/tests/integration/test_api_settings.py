"""Integration tests for /api/settings endpoints (B5-B)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

import backend.app.providers as providers
from backend.app.models.settings import (
    BranchPolicySettingModel,
    GlobalSettingModel,
)
from backend.app.schemas.llm import ProviderHealth


# ---------------------------------------------------------------------------
# Helpers — idempotent seed (merge avoids UNIQUE violations on shared engine)
# ---------------------------------------------------------------------------

async def _seed_global(db_session):
    existing = (await db_session.execute(
        select(GlobalSettingModel).where(GlobalSettingModel.setting_id == "default")
    )).scalar_one_or_none()
    if existing:
        existing.log_level = "INFO"
        existing.display_timezone = "UTC"
        existing.default_tick_duration_minutes = 120
        await db_session.commit()
        return existing
    row = GlobalSettingModel(
        setting_id="default",
        default_tick_duration_minutes=120,
        default_max_ticks=48,
        default_max_schedule_horizon_ticks=5,
        log_level="INFO",
        display_timezone="UTC",
        theme="system",
        enable_oasis_adapter=False,
        branching_defaults={},
        payload={},
    )
    db_session.add(row)
    await db_session.commit()
    return row


async def _seed_branch_policy(db_session):
    existing = (await db_session.execute(
        select(BranchPolicySettingModel).where(BranchPolicySettingModel.policy_id == "default")
    )).scalar_one_or_none()
    if existing:
        existing.max_active_universes = 50
        existing.max_total_branches = 500
        existing.max_depth = 8
        await db_session.commit()
        return existing
    row = BranchPolicySettingModel(
        policy_id="default",
        max_active_universes=50,
        max_total_branches=500,
        max_depth=8,
        max_branches_per_tick=5,
        branch_cooldown_ticks=3,
        min_divergence_score=0.35,
        auto_prune_low_value=True,
        payload={},
    )
    db_session.add(row)
    await db_session.commit()
    return row


# ---------------------------------------------------------------------------
# GET /api/settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_settings_uses_runtime_defaults_when_not_seeded(client):
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["setting_id"] == "default"
    assert data["default_tick_duration_minutes"] == 1440
    assert data["default_max_ticks"] == 12
    assert data["payload"] == {"source": "runtime_defaults"}


@pytest.mark.asyncio
async def test_get_settings_ok(client, db_session):
    await _seed_global(db_session)
    resp = await client.get("/api/settings")
    assert resp.status_code == 200
    data = resp.json()
    assert data["setting_id"] == "default"
    assert data["default_tick_duration_minutes"] == 120
    assert data["log_level"] == "INFO"


# ---------------------------------------------------------------------------
# PATCH /api/settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_settings(client, db_session):
    await _seed_global(db_session)
    resp = await client.patch(
        "/api/settings",
        json={"log_level": "DEBUG", "display_timezone": "America/Los_Angeles"},
        headers={"Idempotency-Key": "test-patch-settings-1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["log_level"] == "DEBUG"
    assert data["display_timezone"] == "America/Los_Angeles"


@pytest.mark.asyncio
async def test_patch_settings_idempotency(client, db_session):
    """Same Idempotency-Key should be accepted and return the same result."""
    await _seed_global(db_session)
    key = "idempotency-settings-xyz"
    resp1 = await client.patch("/api/settings", json={"log_level": "WARNING"}, headers={"Idempotency-Key": key})
    resp2 = await client.patch("/api/settings", json={"log_level": "WARNING"}, headers={"Idempotency-Key": key})
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json()["log_level"] == resp2.json()["log_level"]


# ---------------------------------------------------------------------------
# GET/PATCH /api/settings/providers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_providers_empty(client):
    resp = await client.get("/api/settings/providers")
    assert resp.status_code == 200
    assert resp.json()["providers"] == []


@pytest.mark.asyncio
async def test_patch_providers_creates(client, db_session):
    payload = {
        "providers": [
            {
                "provider": "openrouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key_env": "OPENROUTER_API_KEY",
                "default_model": "openai/gpt-4o",
                "fallback_model": "openai/gpt-4o-mini",
                "json_mode_required": True,
                "tool_calling_enabled": True,
                "enabled": True,
                "extra_headers": {},
                "payload": {},
            }
        ]
    }
    resp = await client.patch("/api/settings/providers", json=payload, headers={"Idempotency-Key": "patch-providers-1"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["providers"]) == 1
    assert data["providers"][0]["provider"] == "openrouter"


@pytest.mark.asyncio
async def test_patch_providers_upsert(client, db_session):
    """Patching same provider twice should update (not duplicate)."""
    payload = {
        "providers": [
            {
                "provider": "openrouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key_env": "OPENROUTER_API_KEY",
                "default_model": "openai/gpt-4o",
                "enabled": True,
                "extra_headers": {},
                "payload": {},
            }
        ]
    }
    await client.patch("/api/settings/providers", json=payload)
    # Update default_model
    payload["providers"][0]["default_model"] = "openai/gpt-4o-mini"
    resp = await client.patch("/api/settings/providers", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["providers"]) == 1
    assert data["providers"][0]["default_model"] == "openai/gpt-4o-mini"


@pytest.mark.asyncio
async def test_get_llm_config_exposes_provider_routing_and_route_catalog(client, db_session):
    provider_payload = {
        "providers": [
            {
                "provider": "openrouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key_env": "OPENROUTER_API_KEY",
                "default_model": "openai/gpt-4o",
                "fallback_model": "openai/gpt-4o-mini",
                "json_mode_required": True,
                "tool_calling_enabled": True,
                "enabled": True,
                "extra_headers": {},
                "payload": {},
            }
        ]
    }
    routing_payload = {
        "entries": [
            {
                "job_type": "report_agent",
                "preferred_provider": "openai-codex",
                "preferred_model": "gpt-5.4",
                "fallback_provider": "openrouter",
                "fallback_model": "openai/gpt-4o-mini",
                "temperature": 0.2,
                "top_p": 1.0,
                "max_tokens": 8192,
                "max_concurrency": 2,
                "requests_per_minute": 20,
                "tokens_per_minute": 200000,
                "timeout_seconds": 300,
                "retry_policy": "exponential_backoff",
                "payload": {},
            }
        ]
    }
    await client.patch("/api/settings/providers", json=provider_payload)
    await client.patch("/api/settings/model-routing", json=routing_payload)

    resp = await client.get("/api/settings/llm")

    assert resp.status_code == 200
    data = resp.json()
    assert data["runtime_defaults"]["default_provider"]
    assert data["provider_catalog"][0]["provider"] == "openrouter"
    assert data["providers"][0]["provider"] == "openrouter"
    assert data["model_routing"][0]["job_type"] == "report_agent"
    report_effective = next(
        entry for entry in data["effective_model_routing"] if entry["route"] == "report_agent"
    )
    assert report_effective["preferred_provider"] == "openai-codex"
    assert report_effective["matched_route"] == "report_agent"
    assert any(route["route"] == "cohort_agent" for route in data["known_routes"])
    assert data["api"]["model_routing"] == "/api/settings/model-routing"


@pytest.mark.asyncio
async def test_patch_providers_rebuilds_active_registry(client, db_session):
    providers.clear_registry()
    stale_provider = AsyncMock()
    rebuilt_provider = AsyncMock()
    providers.register_provider("stale", stale_provider)

    async def _fake_initialize(settings):
        providers.register_provider("rebuilt", rebuilt_provider)

    payload = {
        "providers": [
            {
                "provider": "openrouter",
                "base_url": "https://openrouter.ai/api/v1",
                "api_key_env": "OPENROUTER_API_KEY",
                "default_model": "openai/gpt-4o-mini",
                "fallback_model": "openai/gpt-4o-mini",
                "json_mode_required": True,
                "tool_calling_enabled": True,
                "enabled": True,
                "extra_headers": {},
                "payload": {},
            }
        ]
    }

    with patch(
        "backend.app.providers.initialize_providers_from_settings",
        new=AsyncMock(side_effect=_fake_initialize),
    ):
        resp = await client.patch("/api/settings/providers", json=payload)

    assert resp.status_code == 200
    with pytest.raises(KeyError):
        providers.get_provider("stale")
    assert providers.get_provider("rebuilt") is rebuilt_provider


# ---------------------------------------------------------------------------
# GET/PATCH /api/settings/model-routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_model_routing_empty(client):
    resp = await client.get("/api/settings/model-routing")
    assert resp.status_code == 200
    data = resp.json()
    assert data["entries"] == []
    assert any(entry["route"] == "report_agent" for entry in data["effective_entries"])
    effective = {entry["route"]: entry for entry in data["effective_entries"]}
    assert effective["cohort_agent"]["preferred_provider"] == "openrouter"
    assert effective["cohort_agent"]["preferred_model"] == "deepseek/deepseek-v4-flash"
    assert effective["hero_agent"]["preferred_model"] == "deepseek/deepseek-v4-flash"
    assert effective["event_summary"]["preferred_model"] == "deepseek/deepseek-v4-flash"
    for route in ("initializer_agent", "god_agent", "endpoint_ledger", "report_agent"):
        assert effective[route]["preferred_provider"] == "openai-codex"
        assert effective[route]["preferred_model"] == "gpt-5.4"
    assert {route["route"] for route in data["known_routes"]} >= {
        "initializer_agent",
        "god_agent",
        "cohort_agent",
        "hero_agent",
        "event_summary",
        "report_agent",
    }


@pytest.mark.asyncio
async def test_patch_model_routing(client, db_session):
    payload = {
        "entries": [
            {
                "job_type": "god_agent_review",
                "preferred_provider": "openrouter",
                "preferred_model": "openai/gpt-4o",
                "temperature": 0.7,
                "top_p": 1.0,
                "max_tokens": 4096,
                "max_concurrency": 4,
                "requests_per_minute": 60,
                "tokens_per_minute": 150000,
                "timeout_seconds": 120,
                "retry_policy": "exponential_backoff",
                "payload": {},
            }
        ]
    }
    resp = await client.patch("/api/settings/model-routing", json=payload, headers={"Idempotency-Key": "patch-routing-1"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["entries"]) == 1
    assert data["entries"][0]["job_type"] == "god_agent_review"
    god_effective = next(entry for entry in data["effective_entries"] if entry["route"] == "god_agent")
    assert god_effective["matched_route"] == "god_agent_review"
    assert any(route["route"] == "report_agent" for route in data["known_routes"])


# ---------------------------------------------------------------------------
# GET/PATCH /api/settings/rate-limits
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_rate_limits_empty(client):
    resp = await client.get("/api/settings/rate-limits")
    assert resp.status_code == 200
    assert resp.json()["rate_limits"] == []


@pytest.mark.asyncio
async def test_patch_rate_limits(client, db_session):
    payload = {
        "rate_limits": [
            {
                "provider": "openrouter",
                "enabled": True,
                "rpm_limit": 1200,
                "tpm_limit": 10000000,
                "max_concurrency": 40,
                "burst_multiplier": 1.2,
                "retry_policy": "exponential_backoff",
                "jitter": True,
                "branch_reserved_capacity_pct": 20.0,
                "healthcheck_enabled": True,
                "payload": {},
            }
        ]
    }
    resp = await client.patch("/api/settings/rate-limits", json=payload, headers={"Idempotency-Key": "patch-rl-1"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["rate_limits"]) == 1
    assert data["rate_limits"][0]["provider"] == "openrouter"
    assert data["rate_limits"][0]["rpm_limit"] == 1200


# ---------------------------------------------------------------------------
# GET/PATCH /api/settings/branch-policy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_branch_policy_ok(client, db_session):
    await _seed_branch_policy(db_session)
    resp = await client.get("/api/settings/branch-policy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["policy_id"] == "default"
    assert data["max_active_universes"] == 50
    assert data["max_depth"] == 8


@pytest.mark.asyncio
async def test_patch_branch_policy(client, db_session):
    await _seed_branch_policy(db_session)
    resp = await client.patch(
        "/api/settings/branch-policy",
        json={"max_active_universes": 25, "max_depth": 5},
        headers={"Idempotency-Key": "patch-bp-1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["max_active_universes"] == 25
    assert data["max_depth"] == 5
    # Other fields unchanged
    assert data["max_total_branches"] == 500


# ---------------------------------------------------------------------------
# POST /api/settings/providers/test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_test_provider_not_registered(client):
    resp = await client.post("/api/settings/providers/test", json={"provider": "nonexistent_provider"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "not registered" in data["error"]


@pytest.mark.asyncio
async def test_test_provider_registered(client):
    """Mock a registered provider and test the healthcheck."""
    mock_provider = AsyncMock()
    mock_provider.healthcheck = AsyncMock(return_value={"ok": True, "latency_ms": 42})

    with patch("backend.app.providers._PROVIDER_REGISTRY", {"mock_provider": mock_provider}):
        resp = await client.post("/api/settings/providers/test", json={"provider": "mock_provider", "model": "test/model"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["provider"] == "mock_provider"


@pytest.mark.asyncio
async def test_test_provider_returns_pydantic_health_details(client):
    mock_provider = AsyncMock()
    mock_provider.healthcheck = AsyncMock(
        return_value=ProviderHealth(
            provider="openai-codex",
            ok=True,
            latency_ms=0,
            details={"auth_source": "~/.worldfork/openai-codex-auth.json"},
        )
    )

    with patch("backend.app.providers._PROVIDER_REGISTRY", {"openai-codex": mock_provider}):
        resp = await client.post("/api/settings/providers/test", json={"provider": "openai-codex"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["provider"] == "openai-codex"
    assert data["details"]["auth_source"] == "~/.worldfork/openai-codex-auth.json"
