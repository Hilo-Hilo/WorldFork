"""Integrations API router — B5-B full implementation.

Provides:
  GET  /api/integrations/zep
  PATCH /api/integrations/zep
  POST /api/integrations/zep/test
  POST /api/integrations/zep/sync
  GET  /api/integrations/zep/mappings
  PATCH /api/integrations/zep/mappings
  GET  /api/integrations/zep/status
  POST /api/integrations/webhooks/test
  POST /api/integrations/webhooks/replay
"""
from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.db import get_session
from backend.app.core.ids import new_id
from backend.app.models.settings import ZepSettingModel
from backend.app.schemas.api import (
    PatchZepMappingsRequest,
    PatchZepRequest,
    WebhookReplayRequest,
    WebhookTestRequest,
    WebhookTestResponse,
    ZepMappingItem,
    ZepMappingsResponse,
    ZepSettingResponse,
    ZepStatusResponse,
    ZepSyncResponse,
    ZepTestResponse,
)

router = APIRouter(prefix="/api/integrations", tags=["integrations"])
webhooks_router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

_SESSION = Annotated[AsyncSession, Depends(get_session)]
logger = logging.getLogger(__name__)

# Module-level imports for testability (can be patched in tests)
try:
    from backend.app.memory.factory import get_memory, reload_memory_provider
except ImportError:  # pragma: no cover
    get_memory = None  # type: ignore[assignment]
    reload_memory_provider = None  # type: ignore[assignment]

try:
    from backend.app.integrations.zep import zep_status_summary
except ImportError:  # pragma: no cover
    async def zep_status_summary() -> dict:  # type: ignore[misc]
        return {"enabled": False, "mode": "unknown", "degraded": True}


def _zep_runtime_enabled(*, api_key_env: str = "ZEP_API_KEY", desired_enabled: bool | None = None) -> bool:
    from backend.app.core.config import settings

    enabled = settings.zep_enabled if desired_enabled is None else desired_enabled
    return bool(enabled and (settings.zep_api_key or os.environ.get(api_key_env)))


def _zep_row_to_response(row: ZepSettingModel) -> ZepSettingResponse:
    runtime_enabled = _zep_runtime_enabled(
        api_key_env=row.api_key_env,
        desired_enabled=bool(row.enabled),
    )
    active_memory = row.mode if runtime_enabled else "local"
    payload = dict(row.payload or {})
    payload["runtime_enabled"] = runtime_enabled
    payload["active_memory"] = active_memory
    return ZepSettingResponse(
        setting_id=row.setting_id,
        enabled=bool(row.enabled),
        mode=row.mode,
        api_key_env=row.api_key_env,
        cache_ttl_seconds=row.cache_ttl_seconds,
        degraded=bool(row.degraded),
        payload=payload,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


# ---------------------------------------------------------------------------
# Zep settings
# ---------------------------------------------------------------------------


@router.get("/zep", response_model=ZepSettingResponse, summary="Get Zep memory settings")
async def get_zep(session: _SESSION) -> ZepSettingResponse:
    result = await session.execute(select(ZepSettingModel).where(ZepSettingModel.setting_id == "default"))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zep settings not seeded — run make seed")
    return _zep_row_to_response(row)


@router.patch("/zep", response_model=ZepSettingResponse, summary="Update Zep memory settings")
async def patch_zep(
    payload: PatchZepRequest,
    session: _SESSION,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ZepSettingResponse:
    result = await session.execute(select(ZepSettingModel).where(ZepSettingModel.setting_id == "default"))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Zep settings not seeded — run make seed")

    updates = payload.model_dump(exclude_none=True)
    for key, val in updates.items():
        setattr(row, key, val)
    await session.commit()
    await session.refresh(row)

    # Reload memory provider so changes take effect
    if reload_memory_provider is not None:
        try:
            await reload_memory_provider()
        except Exception as exc:
            logger.warning("reload_memory_provider failed: %s", exc)

    return _zep_row_to_response(row)


# ---------------------------------------------------------------------------
# Zep test
# ---------------------------------------------------------------------------


@router.post("/zep/test", response_model=ZepTestResponse, summary="Test Zep memory connectivity")
async def test_zep() -> ZepTestResponse:
    try:
        if not _zep_runtime_enabled():
            return ZepTestResponse(ok=False, latency_ms=0, error="Zep is disabled; local ledger memory is active.")
        if get_memory is None:
            return ZepTestResponse(ok=False, latency_ms=None, error="memory factory not available")
        provider = get_memory()
        t0 = time.monotonic()
        health = await provider.healthcheck()
        latency_ms = int((time.monotonic() - t0) * 1000)
        health_payload = health.model_dump() if hasattr(health, "model_dump") else dict(health)
        ok = bool(health_payload.get("ok", False))
        return ZepTestResponse(ok=ok, latency_ms=latency_ms, error=None if ok else str(health_payload.get("error", "healthcheck failed")))
    except Exception as exc:
        return ZepTestResponse(ok=False, latency_ms=None, error=str(exc))


# ---------------------------------------------------------------------------
# Zep sync
# ---------------------------------------------------------------------------


@router.post("/zep/sync", response_model=ZepSyncResponse, summary="Enqueue Zep memory sync for a run")
async def sync_zep(run_id: str = Query(...)) -> ZepSyncResponse:
    if not _zep_runtime_enabled():
        return ZepSyncResponse(enqueued=False, task_id=None, run_id=run_id)

    task_id: str | None = None
    try:
        from backend.app.workers.scheduler import enqueue, make_envelope

        envelope = make_envelope(
            job_type="sync_zep_memory",
            run_id=run_id,
            payload={"run_id": run_id},
        )
        task_id = await enqueue(envelope)
        return ZepSyncResponse(enqueued=True, task_id=task_id, run_id=run_id)
    except Exception as exc:
        logger.warning("Could not enqueue sync_zep_memory (broker unavailable): %s", exc)
        return ZepSyncResponse(enqueued=False, task_id=None, run_id=run_id)


# ---------------------------------------------------------------------------
# Zep mappings
# ---------------------------------------------------------------------------

# In-memory mapping store (advisory; B6-A will persist these to DB)
_ZEP_MAPPINGS: dict[str, ZepMappingItem] = {}


@router.get("/zep/mappings", response_model=ZepMappingsResponse, summary="List Zep actor→user_id mappings")
async def get_zep_mappings() -> ZepMappingsResponse:
    return ZepMappingsResponse(mappings=list(_ZEP_MAPPINGS.values()))


@router.patch("/zep/mappings", response_model=ZepMappingsResponse, summary="Update Zep actor mappings")
async def patch_zep_mappings(payload: PatchZepMappingsRequest) -> ZepMappingsResponse:
    for item in payload.mappings:
        existing = _ZEP_MAPPINGS.get(item.actor_id)
        if existing:
            existing.zep_user_id = item.zep_user_id
        else:
            _ZEP_MAPPINGS[item.actor_id] = ZepMappingItem(
                actor_id=item.actor_id,
                actor_kind="cohort",  # default; B6-A will refine
                zep_user_id=item.zep_user_id,
            )
    return ZepMappingsResponse(mappings=list(_ZEP_MAPPINGS.values()))


# ---------------------------------------------------------------------------
# Zep status
# ---------------------------------------------------------------------------


@router.get("/zep/status", response_model=ZepStatusResponse, summary="Zep memory integration status")
async def get_zep_status() -> ZepStatusResponse:
    summary = await zep_status_summary()
    return ZepStatusResponse(
        enabled=summary.get("enabled", False),
        mode=summary.get("mode", "unknown"),
        degraded=summary.get("degraded", True),
        last_healthcheck_at=summary.get("last_healthcheck_at"),
        last_latency_ms=summary.get("last_latency_ms"),
        error=summary.get("error"),
    )


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

    target_url = body.target_url or event_record.target_url

    # Re-deliver using a generic secret from config (signing is advisory on replay)
    from backend.app.core.config import settings as app_settings
    from backend.app.integrations.webhooks import WebhookDeliverer, WebhookSigner
    secret = getattr(app_settings, "webhook_secret", "worldfork-replay-secret")

    signer = WebhookSigner(secret)
    deliverer = WebhookDeliverer(signer, timeout=10.0, max_attempts=1)

    try:
        result_data = await deliverer.deliver(url=target_url, event=event_record.payload)
        delivered_ok = result_data["status_code"] < 400
        event_record.attempts = event_record.attempts + 1
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
        event_record.status = "failed"
        event_record.error = str(exc)[:500]
        event_record.attempts = event_record.attempts + 1
        await session.commit()
        return WebhookTestResponse(ok=False, error=str(exc))
