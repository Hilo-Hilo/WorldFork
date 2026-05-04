"""Integration tests for integration endpoints."""
from __future__ import annotations

import pytest
import respx
import httpx
from sqlalchemy import JSON, select

from backend.app.models.webhooks import WebhookEventModel

WebhookEventModel.__table__.c.payload.type = JSON()


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
