from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.legacy.compat_db import table_has_columns
from backend.app.core.config import settings
from backend.app.core.db import get_session
from backend.app.models.settings import (
    BranchPolicySettingModel,
    GlobalSettingModel,
    ModelRoutingEntryModel,
    ProviderSettingModel,
    RateLimitSettingModel,
)
from app.domains.settings.routes import _model_routing_payload, _runtime_settings_payload
import backend.app.providers as providers

router = APIRouter(prefix="/api/settings", tags=["settings-legacy"])
DbSession = Annotated[AsyncSession, Depends(get_session)]


def _row_dict(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


async def _singleton_or_none(session: AsyncSession, model: type[Any], key_field: str, key: str = "default") -> Any | None:
    if not await table_has_columns(session, model.__tablename__, [key_field]):
        return None
    return (
        await session.execute(select(model).where(getattr(model, key_field) == key))
    ).scalar_one_or_none()


async def _list_rows(session: AsyncSession, model: type[Any]) -> list[dict[str, Any]]:
    primary_keys = [column.name for column in model.__table__.primary_key.columns]
    if not await table_has_columns(session, model.__tablename__, primary_keys):
        return []
    result = await session.execute(select(model))
    return [_row_dict(row) for row in result.scalars().all()]


def _apply_patch(row: Any, patch: dict[str, Any]) -> Any:
    for key, value in patch.items():
        if hasattr(row, key):
            setattr(row, key, value)
    return row


@router.get("")
async def get_settings(session: DbSession):
    row = await _singleton_or_none(session, GlobalSettingModel, "setting_id")
    if row is None:
        return _runtime_settings_payload()
    return _row_dict(row)


@router.patch("")
async def patch_settings(payload: dict[str, Any], session: DbSession):
    row = await _singleton_or_none(session, GlobalSettingModel, "setting_id")
    if row is None:
        row = GlobalSettingModel(setting_id="default", payload={})
        session.add(row)
    _apply_patch(row, payload)
    await session.commit()
    await session.refresh(row)
    return _row_dict(row)


@router.get("/providers")
async def get_providers(session: DbSession):
    return {"providers": await _list_rows(session, ProviderSettingModel)}


@router.patch("/providers")
async def patch_providers(payload: dict[str, Any], session: DbSession):
    for item in payload.get("providers", []):
        row = await session.get(ProviderSettingModel, item["provider"])
        if row is None:
            row = ProviderSettingModel(provider=item["provider"], payload={})
            session.add(row)
        _apply_patch(row, item)
    await session.commit()
    await providers.rebuild_registry_from_settings(settings)
    return {"providers": await _list_rows(session, ProviderSettingModel)}


@router.post("/providers/test")
async def test_provider(payload: dict[str, Any]):
    name = payload.get("provider")
    provider = providers._PROVIDER_REGISTRY.get(name)
    if provider is None:
        return {"ok": False, "provider": name, "error": f"provider {name!r} not registered"}
    result = await provider.healthcheck()
    if isinstance(result, dict):
        data = result
    elif hasattr(result, "model_dump"):
        data = result.model_dump(mode="json")
    else:
        data = {"ok": bool(getattr(result, "ok", False))}
    data.setdefault("provider", name)
    return data


@router.get("/model-routing")
async def get_model_routing(session: DbSession):
    return await _model_routing_payload(session)


@router.patch("/model-routing")
async def patch_model_routing(payload: dict[str, Any], session: DbSession):
    for item in payload.get("entries", []):
        row = await session.get(ModelRoutingEntryModel, item["job_type"])
        if row is None:
            row = ModelRoutingEntryModel(job_type=item["job_type"], payload={})
            session.add(row)
        _apply_patch(row, item)
    await session.commit()
    return await _model_routing_payload(session)


@router.get("/rate-limits")
async def get_rate_limits(session: DbSession):
    return {"rate_limits": await _list_rows(session, RateLimitSettingModel)}


@router.patch("/rate-limits")
async def patch_rate_limits(payload: dict[str, Any], session: DbSession):
    for item in payload.get("rate_limits", []):
        row = await session.get(RateLimitSettingModel, item["provider"])
        if row is None:
            row = RateLimitSettingModel(provider=item["provider"], payload={})
            session.add(row)
        _apply_patch(row, item)
    await session.commit()
    return {"rate_limits": await _list_rows(session, RateLimitSettingModel)}


@router.get("/branch-policy")
async def get_branch_policy(session: DbSession):
    row = await _singleton_or_none(session, BranchPolicySettingModel, "policy_id")
    if row is None:
        raise HTTPException(status_code=404, detail="branch policy not found")
    return _row_dict(row)


@router.patch("/branch-policy")
async def patch_branch_policy(payload: dict[str, Any], session: DbSession):
    row = await _singleton_or_none(session, BranchPolicySettingModel, "policy_id")
    if row is None:
        row = BranchPolicySettingModel(policy_id="default", payload={})
        session.add(row)
    _apply_patch(row, payload)
    await session.commit()
    await session.refresh(row)
    return _row_dict(row)
