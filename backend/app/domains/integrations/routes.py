"""Integrations API router.

Provides:
  POST /api/integrations/webhooks/test
  POST /api/integrations/webhooks/replay
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.db import get_session
from backend.app.core.ids import new_id
from backend.app.schemas.api import (
    WebhookReplayRequest,
    WebhookTestRequest,
    WebhookTestResponse,
)

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
webhooks_router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

logger = logging.getLogger(__name__)
_SESSION = Annotated[AsyncSession, Depends(get_session)]


# ---------------------------------------------------------------------------
# Webhooks — fleshed-out implementations
# ---------------------------------------------------------------------------


@webhooks_router.post("/test", response_model=WebhookTestResponse, summary="Send a signed test webhook event")
@router.post("/webhooks/test", response_model=WebhookTestResponse, summary="Send a signed test webhook event")
async def webhooks_test(
    body: WebhookTestRequest,
    session: _SESSION,
) -> WebhookTestResponse:
    from backend.app.integrations.webhooks import WebhookDeliverer, WebhookSigner

    event = {
        "type": body.event_type,
        "data": body.payload,
        "created_at": datetime.now(UTC).isoformat(),
    }

    signer = WebhookSigner(body.secret)
    deliverer = WebhookDeliverer(signer, timeout=10.0, max_attempts=1)

    # Persist record
    try:
        import json as _json

        from backend.app.models.webhooks import WebhookEventModel
        payload_bytes = _json.dumps(event, separators=(",", ":")).encode("utf-8")
        sig, ts = signer.sign(payload_bytes)
        event_record = WebhookEventModel(
            id=new_id("wh"),
            run_id=None,
            event_type=body.event_type,
            payload=event,
            signature=f"t={ts},v1={sig}",
            target_url=body.url,
            status="pending",
            attempts=0,
            created_at=datetime.now(UTC),
        )
        session.add(event_record)
        await session.commit()
    except Exception as exc:
        logger.warning("Could not persist webhook event record: %s", exc)
        event_record = None

    try:
        result = await deliverer.deliver(url=body.url, event=event)
        delivered_ok = result["status_code"] < 400

        # Update record
        if event_record:
            try:
                event_record.status = "delivered" if delivered_ok else "failed"
                event_record.attempts = result.get("attempts", 1)
                event_record.last_delivered_at = datetime.now(UTC)
                await session.commit()
            except Exception:
                pass

        return WebhookTestResponse(
            ok=delivered_ok,
            status_code=result.get("status_code"),
            latency_ms=result.get("latency_ms"),
            attempts=result.get("attempts"),
            delivered_at=result.get("delivered_at"),
            error=None if delivered_ok else f"HTTP {result['status_code']}",
        )
    except Exception as exc:
        if event_record:
            try:
                event_record.status = "failed"
                event_record.error = str(exc)[:500]
                event_record.attempts = max(int(event_record.attempts or 0), 0) + 1
                await session.commit()
            except Exception:
                pass
        return WebhookTestResponse(ok=False, attempts=1, error=str(exc))


@webhooks_router.post("/replay", response_model=WebhookTestResponse, summary="Replay a stored webhook event")
@router.post("/webhooks/replay", response_model=WebhookTestResponse, summary="Replay a stored webhook event")
async def webhooks_replay(
    body: WebhookReplayRequest,
    session: _SESSION,
) -> WebhookTestResponse:
    try:
        from backend.app.models.webhooks import WebhookEventModel
        result = await session.execute(
            select(WebhookEventModel).where(WebhookEventModel.id == body.event_id)
        )
        event_record = result.scalar_one_or_none()
    except Exception:
        event_record = None

    if event_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Webhook event {body.event_id!r} not found",
        )

    event_id = event_record.id
    target_url = body.target_url or event_record.target_url
    event_payload = event_record.payload
    previous_attempts = int(event_record.attempts or 0)
    await session.rollback()

    # Re-deliver using a generic secret from config (signing is advisory on replay)
    from backend.app.core.config import settings as app_settings
    from backend.app.integrations.webhooks import WebhookDeliverer, WebhookSigner
    secret = getattr(app_settings, "webhook_secret", "worldfork-replay-secret")

    signer = WebhookSigner(secret)
    deliverer = WebhookDeliverer(signer, timeout=10.0, max_attempts=1)

    try:
        result_data = await deliverer.deliver(url=target_url, event=event_payload)
        delivered_ok = result_data["status_code"] < 400
        event_record = await session.get(WebhookEventModel, event_id)
        if event_record is not None:
            event_record.attempts = previous_attempts + 1
            event_record.status = "delivered" if delivered_ok else "failed"
            event_record.last_delivered_at = datetime.now(UTC)
            await session.commit()
        return WebhookTestResponse(
            ok=delivered_ok,
            status_code=result_data.get("status_code"),
            latency_ms=result_data.get("latency_ms"),
            attempts=result_data.get("attempts"),
            delivered_at=result_data.get("delivered_at"),
        )
    except Exception as exc:
        event_record = await session.get(WebhookEventModel, event_id)
        if event_record is not None:
            event_record.status = "failed"
            event_record.error = str(exc)[:500]
            event_record.attempts = previous_attempts + 1
            await session.commit()
        return WebhookTestResponse(ok=False, error=str(exc))
