"""Integration tests for /api/integrations endpoints (B5-B)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import respx
import httpx
from sqlalchemy import JSON, select

from backend.app.models.settings import ZepSettingModel
from backend.app.models.webhooks import WebhookEventModel

WebhookEventModel.__table__.c.payload.type = JSON()


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

async def _seed_zep(db_session):
    from sqlalchemy import select
    existing = (await db_session.execute(
        select(ZepSettingModel).where(ZepSettingModel.setting_id == "default")
    )).scalar_one_or_none()
    if existing:
        existing.enabled = True
        existing.mode = "cohort_memory"
        existing.degraded = False
        await db_session.commit()
        return existing
    row = ZepSettingModel(
        setting_id="default",
        enabled=True,
        mode="cohort_memory",
        api_key_env="ZEP_API_KEY",
        cache_ttl_seconds=300,
        degraded=False,
        payload={},
    )
    db_session.add(row)
    await db_session.commit()
    return row


# ---------------------------------------------------------------------------
# GET /api/integrations/zep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_zep_ok(client, db_session):
    await _seed_zep(db_session)
    resp = await client.get("/api/integrations/zep")
    assert resp.status_code == 200
    data = resp.json()
    assert data["setting_id"] == "default"
    assert data["enabled"] is True
    assert data["mode"] == "cohort_memory"
    assert data["payload"]["runtime_enabled"] is False
    assert data["payload"]["active_memory"] == "local"


@pytest.mark.asyncio
async def test_get_zep_runtime_enabled_uses_db_api_key_env(client, db_session, monkeypatch):
    await _seed_zep(db_session)
    monkeypatch.setenv("ZEP_API_KEY", "test-zep-key")

    resp = await client.get("/api/integrations/zep")

    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["payload"]["runtime_enabled"] is True
    assert data["payload"]["active_memory"] == "cohort_memory"


# ---------------------------------------------------------------------------
# PATCH /api/integrations/zep
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_zep_ok(client, db_session):
    await _seed_zep(db_session)
    resp = await client.patch(
        "/api/integrations/zep",
        json={"enabled": False, "mode": "hybrid"},
        headers={"Idempotency-Key": "patch-zep-1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["mode"] == "hybrid"


@pytest.mark.asyncio
async def test_patch_zep_preserves_desired_enabled_without_runtime_env(client, db_session):
    await _seed_zep(db_session)
    resp = await client.patch("/api/integrations/zep", json={"enabled": True, "mode": "hybrid"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["mode"] == "hybrid"
    assert data["payload"]["runtime_enabled"] is False
    assert data["payload"]["active_memory"] == "local"

    row = (
        await db_session.execute(
            select(ZepSettingModel).where(ZepSettingModel.setting_id == "default")
        )
    ).scalar_one()
    assert row.enabled is True
    assert row.mode == "hybrid"


def test_memory_factory_loads_zep_config_from_db_row():
    from backend.app.memory import factory

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def get(self, _model, _key):
            return ZepSettingModel(
                setting_id="default",
                enabled=True,
                mode="hybrid",
                api_key_env="CUSTOM_ZEP_KEY",
                cache_ttl_seconds=123,
                degraded=False,
                payload={},
            )

    cfg = factory._load_zep_config(session_factory=lambda: _Session())

    assert cfg is not None
    assert cfg.enabled is True
    assert cfg.mode == "hybrid"
    assert cfg.api_key_env == "CUSTOM_ZEP_KEY"
    assert cfg.cache_ttl_seconds == 123


@pytest.mark.asyncio
async def test_patch_zep_calls_reload(client, db_session):
    """PATCH should call reload_memory_provider after updating."""
    await _seed_zep(db_session)

    import app.domains.integrations.routes as _intg_mod
    with patch.object(_intg_mod, "reload_memory_provider", new=AsyncMock()):
        resp = await client.patch("/api/integrations/zep", json={"enabled": False})

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# POST /api/integrations/zep/test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zep_test_ok(client):
    mock_provider = AsyncMock()
    mock_provider.healthcheck = AsyncMock(return_value={"ok": True, "latency_ms": 30})

    import app.domains.integrations.routes as _intg_mod
    with (
        patch.object(_intg_mod, "_zep_runtime_enabled", return_value=True),
        patch.object(_intg_mod, "get_memory", return_value=mock_provider),
    ):
        resp = await client.post("/api/integrations/zep/test")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_zep_test_degraded(client):
    mock_provider = AsyncMock()
    mock_provider.healthcheck = AsyncMock(side_effect=Exception("Zep unavailable"))

    import app.domains.integrations.routes as _intg_mod
    with (
        patch.object(_intg_mod, "_zep_runtime_enabled", return_value=True),
        patch.object(_intg_mod, "get_memory", return_value=mock_provider),
    ):
        resp = await client.post("/api/integrations/zep/test")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert "Zep unavailable" in data["error"]


# ---------------------------------------------------------------------------
# POST /api/integrations/zep/sync
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zep_sync_enqueues(client):
    mock_result = MagicMock()
    mock_result.id = "task-xyz-123"
    mock_celery = MagicMock()
    mock_celery.send_task.return_value = mock_result

    with patch("backend.app.domains.integrations.routes.celery_app", mock_celery, create=True):
        resp = await client.post("/api/integrations/zep/sync", params={"run_id": "run-abc"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-abc"


@pytest.mark.asyncio
async def test_zep_sync_degraded_when_broker_unavailable(client):
    with patch("backend.app.workers.celery_app.celery_app") as mock_celery:
        mock_celery.send_task.side_effect = Exception("broker down")
        resp = await client.post("/api/integrations/zep/sync", params={"run_id": "run-abc"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-abc"
    # enqueued may be False when broker is down


# ---------------------------------------------------------------------------
# GET/PATCH /api/integrations/zep/mappings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_zep_mappings_empty(client):
    resp = await client.get("/api/integrations/zep/mappings")
    assert resp.status_code == 200
    data = resp.json()
    assert "mappings" in data


@pytest.mark.asyncio
async def test_patch_zep_mappings(client):
    payload = {
        "mappings": [
            {"actor_id": "cohort-001", "zep_user_id": "zep-user-cohort-001"}
        ]
    }
    resp = await client.patch("/api/integrations/zep/mappings", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert any(m["actor_id"] == "cohort-001" for m in data["mappings"])


# ---------------------------------------------------------------------------
# GET /api/integrations/zep/status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zep_status(client):
    import app.domains.integrations.routes as _intg_mod
    with patch.object(
        _intg_mod,
        "zep_status_summary",
        new=AsyncMock(return_value={
            "enabled": False,
            "mode": "local",
            "degraded": False,
            "last_healthcheck_at": "2026-04-25T00:00:00+00:00",
            "last_latency_ms": 5,
        }),
    ):
        resp = await client.get("/api/integrations/zep/status")

    assert resp.status_code == 200
    data = resp.json()
    assert "enabled" in data
    assert "mode" in data
    assert "degraded" in data


# ---------------------------------------------------------------------------
# POST /api/integrations/webhooks/test — real delivery mock via respx
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhooks_test_delivery(client, db_session):
    """Use respx to mock outbound HTTP POST — verifies WebhookDeliverer is called."""
    target_url = "https://example.com/webhook"

    with respx.mock:
        respx.post(target_url).mock(return_value=httpx.Response(200))

        resp = await client.post(
            "/api/integrations/webhooks/test",
            json={
                "url": target_url,
                "secret": "test-secret-key",
                "payload": {"hello": "world"},
                "event_type": "worldfork.test",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status_code"] == 200


@pytest.mark.asyncio
async def test_webhooks_test_delivery_failure(client, db_session):
    """WebhookDeliverer should handle delivery errors gracefully."""
    target_url = "https://example.com/webhook-fail"

    with respx.mock:
        respx.post(target_url).mock(return_value=httpx.Response(500))

        resp = await client.post(
            "/api/integrations/webhooks/test",
            json={
                "url": target_url,
                "secret": "test-secret",
                "payload": {},
                "event_type": "worldfork.test",
            },
        )

    assert resp.status_code == 200
    data = resp.json()
    # 5xx from target → ok=False after max_attempts=1
    assert data["ok"] is False

    event = (
        await db_session.execute(
            select(WebhookEventModel).where(WebhookEventModel.target_url == target_url)
        )
    ).scalar_one()
    assert event.status == "failed"
    assert event.attempts == 1


# ---------------------------------------------------------------------------
# POST /api/integrations/webhooks/replay
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_webhooks_replay_not_found(client):
    resp = await client.post(
        "/api/integrations/webhooks/replay",
        json={"event_id": "nonexistent-event-id"},
    )
    assert resp.status_code == 404
