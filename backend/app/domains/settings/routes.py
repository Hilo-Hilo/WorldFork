from __future__ import annotations

import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.introspection import table_has_columns
from backend.app.core.config import settings
from backend.app.core.db import get_session
from backend.app.models.settings import (
    BranchPolicySettingModel,
    GlobalSettingModel,
    ModelRoutingEntryModel,
    ProviderSettingModel,
    RateLimitSettingModel,
)
from app.llm.routing import audited_route_catalog
from backend.app.schemas.api import (
    BranchPolicyResponse,
    LLMConfigResponse,
    PatchBranchPolicyRequest,
    PatchProvidersRequest,
    PatchRateLimitsRequest,
    PatchRoutingRequest,
    PatchSettingsRequest,
    ProvidersResponse,
    RateLimitsResponse,
    RoutingResponse,
    SettingsResponse,
    TestProviderRequest,
    TestProviderResponse,
)
import backend.app.providers as providers

router = APIRouter(prefix="/settings", tags=["settings"])
DbSession = Annotated[AsyncSession, Depends(get_session)]


def _row_dict(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


async def _singleton_or_none(
    session: AsyncSession,
    model: type[Any],
    key_field: str,
    key: str = "default",
) -> Any | None:
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


def _runtime_llm_defaults() -> dict[str, Any]:
    return {
        "default_provider": settings.default_llm_provider,
        "default_model": settings.default_model,
        "fallback_model": settings.fallback_model,
        "agent_models": {
            "initializer_agent": settings.initializer_agent_model,
            "god_agent": settings.god_agent_model,
            "cohort_agent": settings.cohort_agent_model,
            "hero_agent": settings.hero_agent_model,
            "event_summary": settings.event_summary_model,
            "report_agent": settings.report_agent_model,
        },
        "provider_defaults": {
            "openrouter": {
                "base_url": settings.openrouter_base_url,
                "chat_completions_url": settings.openrouter_chat_completions_url,
                "api_key_env": "OPENROUTER_API_KEY",
            },
            "openai-codex": {
                "enabled": settings.openai_codex_enabled,
                "base_url": settings.openai_codex_base_url,
                "api_key_env": "OPENAI_CODEX_OAUTH_TOKEN",
                "auth_file": settings.openai_codex_auth_file,
                "default_model": settings.openai_codex_default_model,
                "fallback_model": settings.openai_codex_fallback_model,
            },
        },
    }


def _default_tick_duration_minutes() -> int:
    value = str(getattr(settings, "default_tick_duration", "")).strip().lower()
    parts = value.split()
    if len(parts) == 2 and parts[0].isdigit():
        amount = int(parts[0])
        unit = parts[1].rstrip("s")
        if unit == "minute":
            return amount
        if unit == "hour":
            return amount * 60
        if unit == "day":
            return amount * 24 * 60
    return 24 * 60


def _runtime_settings_payload() -> dict[str, Any]:
    return {
        "setting_id": "default",
        "default_tick_duration_minutes": _default_tick_duration_minutes(),
        "default_max_ticks": settings.default_max_ticks,
        "default_max_schedule_horizon_ticks": 5,
        "log_level": settings.log_level,
        "display_timezone": "UTC",
        "theme": "system",
        "enable_oasis_adapter": False,
        "branching_defaults": {
            "max_branch_depth": settings.default_max_branch_depth,
            "max_active_multiverses": settings.default_max_active_multiverses,
            "max_branches_per_tick": settings.default_max_branches_per_tick,
            "branch_score_threshold": settings.branch_score_threshold,
        },
        "payload": {"source": "runtime_defaults"},
    }


def _row_source(row: dict[str, Any]) -> str:
    payload = row.get("payload")
    if isinstance(payload, dict) and payload.get("source") == "seed_default":
        return "seed_default"
    return "settings_model_routing"


def _is_seed_default_row(row: dict[str, Any] | None) -> bool:
    payload = row.get("payload") if row else None
    return isinstance(payload, dict) and payload.get("source") == "seed_default"


def _default_model_for_route(route_info: dict[str, Any]) -> str:
    setting_name = route_info.get("fallback_model_setting") or "default_model"
    value = getattr(settings, str(setting_name), None)
    return str(value or settings.default_model)


def _effective_routing_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows_by_job_type = {str(row["job_type"]): row for row in entries}
    effective: list[dict[str, Any]] = []
    for route_info in audited_route_catalog():
        route = str(route_info["route"])
        row = rows_by_job_type.get(route)
        matched_route = route if row is not None else None
        for alias in route_info.get("aliases", []):
            alias_row = rows_by_job_type.get(str(alias))
            if row is None and alias_row is not None:
                row = alias_row
                matched_route = str(alias)
                break
            if row is not None and alias_row is not None and _is_seed_default_row(row):
                row = alias_row
                matched_route = str(alias)
                break
        if row is None:
            effective.append(
                {
                    "route": route,
                    "route_kind": route_info["route_kind"],
                    "job_type": route,
                    "matched_route": None,
                    "preferred_provider": settings.default_llm_provider,
                    "preferred_model": _default_model_for_route(route_info),
                    "fallback_provider": settings.default_llm_provider,
                    "fallback_model": settings.fallback_model,
                    "source": "runtime_defaults",
                    "payload": {},
                }
            )
            continue
        effective.append(
            {
                "route": route,
                "route_kind": route_info["route_kind"],
                "job_type": str(row["job_type"]),
                "matched_route": matched_route,
                "preferred_provider": row["preferred_provider"],
                "preferred_model": row["preferred_model"],
                "fallback_provider": row.get("fallback_provider"),
                "fallback_model": row.get("fallback_model"),
                "temperature": row.get("temperature"),
                "top_p": row.get("top_p"),
                "max_tokens": row.get("max_tokens"),
                "max_concurrency": row.get("max_concurrency"),
                "requests_per_minute": row.get("requests_per_minute"),
                "tokens_per_minute": row.get("tokens_per_minute"),
                "timeout_seconds": row.get("timeout_seconds"),
                "retry_policy": row.get("retry_policy"),
                "daily_budget_usd": row.get("daily_budget_usd"),
                "source": _row_source(row),
                "payload": row.get("payload") or {},
            }
        )
    return effective


def _provider_payload(row: dict[str, Any] | None) -> dict[str, Any]:
    payload = row.get("payload") if row else None
    return payload if isinstance(payload, dict) else {}


def _provider_api_shape(row: dict[str, Any] | None, default: str) -> str:
    payload = _provider_payload(row)
    return str(payload.get("provider_api") or payload.get("api_shape") or payload.get("api") or default)


def _provider_catalog(providers_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows_by_provider = {str(row["provider"]): row for row in providers_rows}
    catalog: list[dict[str, Any]] = []
    openrouter_row = rows_by_provider.pop("openrouter", None)
    openrouter_key_env = (
        str(openrouter_row.get("api_key_env"))
        if openrouter_row is not None
        else "OPENROUTER_API_KEY"
    )
    catalog.append(
        {
            "provider": "openrouter",
            "api_shape": _provider_api_shape(openrouter_row, "openai-compatible"),
            "source": "settings_provider" if openrouter_row else "runtime_defaults",
            "supported": True,
            "enabled": bool(openrouter_row["enabled"]) if openrouter_row else True,
            "configured": bool(settings.openrouter_api_key or os.environ.get(openrouter_key_env)),
            "base_url": openrouter_row["base_url"] if openrouter_row else settings.openrouter_base_url,
            "api_key_env": openrouter_key_env,
            "default_model": (
                openrouter_row["default_model"] if openrouter_row else settings.default_model
            ),
            "fallback_model": (
                openrouter_row.get("fallback_model") if openrouter_row else settings.fallback_model
            ),
            "payload": _provider_payload(openrouter_row),
        }
    )
    codex_row = rows_by_provider.pop("openai-codex", None)
    codex_key_env = (
        str(codex_row.get("api_key_env")) if codex_row is not None else "OPENAI_CODEX_OAUTH_TOKEN"
    )
    catalog.append(
        {
            "provider": "openai-codex",
            "api_shape": _provider_api_shape(codex_row, "openai-codex-responses"),
            "source": "settings_provider" if codex_row else "runtime_defaults",
            "supported": True,
            "enabled": bool(codex_row["enabled"]) if codex_row else bool(settings.openai_codex_enabled),
            "configured": bool(
                settings.openai_codex_oauth_token
                or os.environ.get(codex_key_env)
                or settings.openai_codex_auth_file
            ),
            "base_url": codex_row["base_url"] if codex_row else settings.openai_codex_base_url,
            "api_key_env": codex_key_env,
            "default_model": (
                codex_row["default_model"] if codex_row else settings.openai_codex_default_model
            ),
            "fallback_model": (
                codex_row.get("fallback_model")
                if codex_row
                else settings.openai_codex_fallback_model
            ),
            "payload": _provider_payload(codex_row),
        }
    )
    for row in rows_by_provider.values():
        catalog.append(
            {
                "provider": row["provider"],
                "api_shape": _provider_api_shape(row, "openai-compatible"),
                "source": "settings_provider",
                "supported": True,
                "enabled": bool(row["enabled"]),
                "configured": bool(os.environ.get(str(row["api_key_env"]))),
                "base_url": row["base_url"],
                "api_key_env": row["api_key_env"],
                "default_model": row["default_model"],
                "fallback_model": row.get("fallback_model"),
                "payload": _provider_payload(row),
            }
        )
    return catalog


def _effective_rate_limits(rate_limit_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows_by_provider = {str(row["provider"]): row for row in rate_limit_rows}
    openrouter_row = rows_by_provider.pop("openrouter", None)
    defaults = {
        "provider": "openrouter",
        "enabled": True,
        "rpm_limit": 600,
        "tpm_limit": 1_000_000,
        "max_concurrency": 8,
        "burst_multiplier": 1.0,
        "retry_policy": "exponential_backoff",
        "jitter": False,
        "daily_budget_usd": None,
        "branch_reserved_capacity_pct": 20.0,
        "healthcheck_enabled": True,
        "payload": {},
        "source": "runtime_defaults",
    }
    effective = [dict(openrouter_row, source="settings_rate_limit") if openrouter_row else defaults]
    effective.extend(dict(row, source="settings_rate_limit") for row in rows_by_provider.values())
    return effective


async def _model_routing_payload(session: AsyncSession) -> dict[str, Any]:
    entries = await _list_rows(session, ModelRoutingEntryModel)
    return {
        "entries": entries,
        "effective_entries": _effective_routing_entries(entries),
        "known_routes": audited_route_catalog(),
    }


async def _llm_config_payload(session: AsyncSession) -> dict[str, Any]:
    routing = await _model_routing_payload(session)
    providers_rows = await _list_rows(session, ProviderSettingModel)
    rate_limit_rows = await _list_rows(session, RateLimitSettingModel)
    return {
        "runtime_defaults": _runtime_llm_defaults(),
        "provider_catalog": _provider_catalog(providers_rows),
        "providers": providers_rows,
        "model_routing": routing["entries"],
        "effective_model_routing": routing["effective_entries"],
        "known_routes": routing["known_routes"],
        "rate_limits": rate_limit_rows,
        "effective_rate_limits": _effective_rate_limits(rate_limit_rows),
        "api": {
            "providers": "/api/settings/providers",
            "model_routing": "/api/settings/model-routing",
            "rate_limits": "/api/settings/rate-limits",
        },
    }


@router.get("", response_model=SettingsResponse)
async def get_settings(session: DbSession):
    row = await _singleton_or_none(session, GlobalSettingModel, "setting_id")
    if row is None:
        return _runtime_settings_payload()
    return _row_dict(row)


@router.patch("", response_model=SettingsResponse)
async def patch_settings(payload: PatchSettingsRequest, session: DbSession):
    row = await _singleton_or_none(session, GlobalSettingModel, "setting_id")
    if row is None:
        row = GlobalSettingModel(setting_id="default", payload={})
        session.add(row)
    _apply_patch(row, payload.model_dump(exclude_none=True))
    await session.commit()
    await session.refresh(row)
    return _row_dict(row)


@router.get("/providers", response_model=ProvidersResponse)
async def get_providers(session: DbSession):
    return {"providers": await _list_rows(session, ProviderSettingModel)}


@router.patch("/providers", response_model=ProvidersResponse)
async def patch_providers(payload: PatchProvidersRequest, session: DbSession):
    for item_model in payload.providers:
        item = item_model.model_dump()
        row = await session.get(ProviderSettingModel, item["provider"])
        if row is None:
            row = ProviderSettingModel(provider=item["provider"], payload={})
            session.add(row)
        _apply_patch(row, item)
    await session.commit()
    await providers.rebuild_registry_from_settings(settings)
    return {"providers": await _list_rows(session, ProviderSettingModel)}


@router.post("/providers/test", response_model=TestProviderResponse)
async def test_provider(payload: TestProviderRequest):
    name = payload.provider
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


@router.get("/llm", response_model=LLMConfigResponse)
async def get_llm_config(session: DbSession):
    return await _llm_config_payload(session)


@router.get("/model-routing", response_model=RoutingResponse)
async def get_model_routing(session: DbSession):
    return await _model_routing_payload(session)


@router.patch("/model-routing", response_model=RoutingResponse)
async def patch_model_routing(payload: PatchRoutingRequest, session: DbSession):
    for item_model in payload.entries:
        item = item_model.model_dump()
        row = await session.get(ModelRoutingEntryModel, item["job_type"])
        if row is None:
            row = ModelRoutingEntryModel(job_type=item["job_type"], payload={})
            session.add(row)
        _apply_patch(row, item)
    await session.commit()
    return await _model_routing_payload(session)


@router.get("/rate-limits", response_model=RateLimitsResponse)
async def get_rate_limits(session: DbSession):
    return {"rate_limits": await _list_rows(session, RateLimitSettingModel)}


@router.patch("/rate-limits", response_model=RateLimitsResponse)
async def patch_rate_limits(payload: PatchRateLimitsRequest, session: DbSession):
    for item_model in payload.rate_limits:
        item = item_model.model_dump()
        row = await session.get(RateLimitSettingModel, item["provider"])
        if row is None:
            row = RateLimitSettingModel(provider=item["provider"], payload={})
            session.add(row)
        _apply_patch(row, item)
    await session.commit()
    return {"rate_limits": await _list_rows(session, RateLimitSettingModel)}


@router.get("/branch-policy", response_model=BranchPolicyResponse)
async def get_branch_policy(session: DbSession):
    row = await _singleton_or_none(session, BranchPolicySettingModel, "policy_id")
    if row is None:
        raise HTTPException(status_code=404, detail="branch policy not found")
    return _row_dict(row)


@router.patch("/branch-policy", response_model=BranchPolicyResponse)
async def patch_branch_policy(payload: PatchBranchPolicyRequest, session: DbSession):
    row = await _singleton_or_none(session, BranchPolicySettingModel, "policy_id")
    if row is None:
        row = BranchPolicySettingModel(policy_id="default", payload={})
        session.add(row)
    _apply_patch(row, payload.model_dump(exclude_none=True))
    await session.commit()
    await session.refresh(row)
    return _row_dict(row)
