from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models
from app.llm.routing import AUDITED_LLM_ROUTES, AuditedLLMRoute, resolve_audited_llm_route

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_PRICING_CACHE: dict[str, Any] = {"expires_at": 0.0, "payload": None}
_PRICING_TTL_SECONDS = 3600

OPENAI_CODEX_OPENROUTER_PREFIX = "openai/"
TICK_TERMINAL_STATUSES = {"final", "succeeded", "completed"}


class CostEstimateRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    remaining_ticks: int | None = Field(default=None, ge=0)
    max_ticks: int | None = Field(default=None, ge=0)
    branch_threshold: float | None = Field(default=None, ge=0, le=1)
    max_parallel_cohort_decisions: int | None = Field(default=None, ge=1)
    assumed_cohorts: int | None = Field(default=None, ge=0)
    assumed_heroes: int | None = Field(default=None, ge=0)
    assumed_multiverses: int | None = Field(default=None, ge=1)
    scenario_tokens: int | None = Field(default=None, ge=0)
    llm_model_config: dict[str, Any] = Field(default_factory=dict, alias="model_config")
    simulation_config: dict[str, Any] = Field(default_factory=dict)
    include_agent_types: list[str] = Field(default_factory=list)
    exclude_agent_types: list[str] = Field(default_factory=list)
    include_non_openrouter: bool = True
    include_reports: bool = True


@dataclass(frozen=True)
class AgentEstimateSpec:
    input_tokens: int
    output_tokens: int
    duration_seconds: float
    context_growth_tokens_per_tick: int = 0


DEFAULT_AGENT_SPECS: dict[str, AgentEstimateSpec] = {
    "initializer_chunk_extractor": AgentEstimateSpec(64_000, 900, 35),
    "initializer_agent": AgentEstimateSpec(18_000, 3_400, 90),
    "cohort_agent": AgentEstimateSpec(8_000, 700, 45, 450),
    "hero_agent": AgentEstimateSpec(7_000, 700, 45, 400),
    "event_summary": AgentEstimateSpec(6_000, 800, 25, 250),
    "god_agent": AgentEstimateSpec(14_000, 1_400, 75, 700),
    "endpoint_ledger": AgentEstimateSpec(10_000, 1_600, 60, 450),
    "predicate_extractor": AgentEstimateSpec(4_000, 500, 20),
    "predicate_resolver": AgentEstimateSpec(6_000, 600, 20),
    "single_report_agent": AgentEstimateSpec(22_000, 2_400, 90, 900),
    "final_report_agent": AgentEstimateSpec(35_000, 8_192, 300, 1_200),
    "report_agent": AgentEstimateSpec(35_000, 8_192, 150, 1_200),
}
REPORT_SINGLE_REPORT_PARALLELISM = 3


def openrouter_pricing_table() -> dict[str, Any]:
    now = time.time()
    if _PRICING_CACHE["payload"] is not None and float(_PRICING_CACHE["expires_at"]) > now:
        return _PRICING_CACHE["payload"]
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(OPENROUTER_MODELS_URL)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc), "models": {}}
    models: dict[str, dict[str, float]] = {}
    for item in payload.get("data") or []:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
        if not model_id:
            continue
        models[model_id] = {
            key: _float_or_zero(pricing.get(key))
            for key in (
                "prompt",
                "completion",
                "request",
                "internal_reasoning",
                "input_cache_read",
                "input_cache_write",
            )
        }
    result = {"status": "ok", "models": models, "source": OPENROUTER_MODELS_URL}
    _PRICING_CACHE["payload"] = result
    _PRICING_CACHE["expires_at"] = now + _PRICING_TTL_SECONDS
    return result


def summarize_tick_cost(
    db: Session,
    *,
    tick: models.TickSnapshot,
    include_calls: bool = False,
    include_non_openrouter: bool = True,
) -> dict[str, Any]:
    return summarize_calls(
        _calls_for_tick(db, tick),
        include_calls=include_calls,
        include_non_openrouter=include_non_openrouter,
    )


def summarize_big_bang_cost(
    db: Session,
    *,
    big_bang: models.BigBang,
    include_calls: bool = False,
    include_non_openrouter: bool = True,
) -> dict[str, Any]:
    calls = db.scalars(
        select(models.LLMCall)
        .where(models.LLMCall.big_bang_id == big_bang.id)
        .order_by(models.LLMCall.created_at.asc())
    ).all()
    return summarize_calls(calls, include_calls=include_calls, include_non_openrouter=include_non_openrouter)


def summarize_report_version_cost(
    db: Session,
    *,
    report_version: models.ReportVersion,
    include_calls: bool = False,
    include_non_openrouter: bool = True,
) -> dict[str, Any]:
    metadata = report_version.generation_metadata or {}
    call_id = metadata.get("llm_call_id")
    calls: list[models.LLMCall] = []
    if call_id:
        try:
            call = db.get(models.LLMCall, UUID(str(call_id)))
        except ValueError:
            call = None
        if call is not None:
            calls.append(call)
    if not calls:
        calls = db.scalars(
            select(models.LLMCall)
            .where(
                models.LLMCall.big_bang_id == _report_big_bang_id(db, report_version),
                models.LLMCall.purpose.like("report_agent%"),
                models.LLMCall.created_at <= report_version.created_at,
            )
            .order_by(models.LLMCall.created_at.desc())
            .limit(1)
        ).all()
    return summarize_calls(calls, include_calls=include_calls, include_non_openrouter=include_non_openrouter)


def summarize_calls(
    calls: list[models.LLMCall],
    *,
    include_calls: bool = False,
    include_non_openrouter: bool = True,
) -> dict[str, Any]:
    pricing = openrouter_pricing_table()
    by_agent: dict[str, dict[str, Any]] = {}
    by_model: dict[str, dict[str, Any]] = {}
    tokens = _empty_token_totals()
    actual_openrouter_usd = 0.0
    estimated_non_openrouter_usd = 0.0
    estimated_openrouter_missing_usd = 0.0
    call_rows: list[dict[str, Any]] = []
    durations: list[float] = []

    for call in calls:
        usage = _usage(call)
        prompt_tokens = _int(usage.get("prompt_tokens"))
        completion_tokens = _int(usage.get("completion_tokens"))
        total_tokens = _int(usage.get("total_tokens")) or prompt_tokens + completion_tokens
        details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
        completion_details = (
            usage.get("completion_tokens_details")
            if isinstance(usage.get("completion_tokens_details"), dict)
            else {}
        )
        cached_tokens = _int(details.get("cached_tokens"))
        cache_write_tokens = _int(details.get("cache_write_tokens"))
        reasoning_tokens = _int(completion_details.get("reasoning_tokens"))
        tokens["prompt_tokens"] += prompt_tokens
        tokens["completion_tokens"] += completion_tokens
        tokens["total_tokens"] += total_tokens
        tokens["cached_tokens"] += cached_tokens
        tokens["cache_write_tokens"] += cache_write_tokens
        tokens["reasoning_tokens"] += reasoning_tokens

        provider = str(call.provider or "")
        model = str(call.model or "")
        agent = agent_type_for_call(call)
        model_key = f"{provider}/{model}"
        actual_cost = _reported_cost_usd(usage)
        estimated_cost = _estimate_usage_usd(
            pricing,
            model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            cache_write_tokens=cache_write_tokens,
            reasoning_tokens=reasoning_tokens,
        )
        if provider == "openrouter" and actual_cost is not None:
            actual_openrouter_usd += actual_cost
            cost_for_breakdown = actual_cost
            cost_kind = "actual_openrouter_usd"
        elif provider == "openrouter" and estimated_cost is not None:
            estimated_openrouter_missing_usd += estimated_cost
            cost_for_breakdown = estimated_cost
            cost_kind = "estimated_usd"
        elif include_non_openrouter and estimated_cost is not None:
            estimated_non_openrouter_usd += estimated_cost
            cost_for_breakdown = estimated_cost
            cost_kind = "estimated_usd"
        else:
            cost_for_breakdown = None
            cost_kind = "unknown_usd"

        _add_breakdown(by_agent, agent, total_tokens, cost_for_breakdown, cost_kind)
        _add_breakdown(by_model, model_key, total_tokens, cost_for_breakdown, cost_kind)
        duration = _duration_seconds(call)
        if duration is not None:
            durations.append(duration)
        if include_calls:
            call_rows.append(
                {
                    "id": str(call.id),
                    "purpose": call.purpose,
                    "agent_type": agent,
                    "provider": provider,
                    "model": model,
                    "status": call.status,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                    "actual_openrouter_usd": actual_cost if provider == "openrouter" else None,
                    "estimated_usd": estimated_cost,
                    "duration_seconds": duration,
                }
            )

    estimated_total = actual_openrouter_usd + estimated_openrouter_missing_usd + estimated_non_openrouter_usd
    result: dict[str, Any] = {
        "currency": "USD",
        "pricing": {
            "provider": "openrouter",
            "status": pricing.get("status"),
            "source": pricing.get("source"),
        },
        "call_count": len(calls),
        "tokens": tokens,
        "actual": {"openrouter_usd": round(actual_openrouter_usd, 10)},
        "estimated": {
            "openrouter_missing_usage_usd": round(estimated_openrouter_missing_usd, 10),
            "non_openrouter_usd": round(estimated_non_openrouter_usd, 10),
            "including_non_openrouter_usd": round(estimated_total, 10),
        },
        "time_actual": {
            "total_llm_duration_seconds": round(sum(durations), 4),
            "max_llm_duration_seconds": max(durations) if durations else None,
        },
        "by_agent": by_agent,
        "by_model": by_model,
    }
    if include_calls:
        result["calls"] = call_rows
    return result


def estimate_pre_big_bang_cost(db: Session, *, request: CostEstimateRequest | None = None) -> dict[str, Any]:
    request = request or CostEstimateRequest()
    return _estimate_cost(db, big_bang=None, request=request)


def estimate_big_bang_cost(
    db: Session,
    *,
    big_bang: models.BigBang,
    request: CostEstimateRequest | None = None,
) -> dict[str, Any]:
    request = request or CostEstimateRequest()
    return _estimate_cost(db, big_bang=big_bang, request=request)


def agent_type_for_call(call: models.LLMCall) -> str:
    purpose = str(call.purpose or "").lower()
    meta = call.meta if isinstance(call.meta, dict) else {}
    request_meta = meta.get("request_metadata") if isinstance(meta.get("request_metadata"), dict) else {}
    hinted = request_meta.get("agent_type") or request_meta.get("route") or meta.get("agent_type")
    if hinted:
        hinted_text = str(hinted).lower()
        if hinted_text == "hero":
            return "hero_agent"
        if hinted_text == "cohort":
            return "cohort_agent"
        if hinted_text in DEFAULT_AGENT_SPECS:
            return hinted_text
    if "initializer_chunk" in purpose:
        return "initializer_chunk_extractor"
    if "initializer" in purpose:
        return "initializer_agent"
    if "god_review" in purpose or "god_agent" in purpose:
        return "god_agent"
    if "endpoint_ledger" in purpose:
        return "endpoint_ledger"
    if "predicate_extraction" in purpose:
        return "predicate_extractor"
    if "predicate_resolution" in purpose:
        return "predicate_resolver"
    if "report_agent" in purpose or purpose.startswith("report_"):
        return "report_agent"
    if "event_summary" in purpose:
        return "event_summary"
    if "hero" in purpose:
        return "hero_agent"
    if "cohort" in purpose or "agent_" in purpose:
        return "cohort_agent"
    return "unknown"


def _estimate_cost(db: Session, *, big_bang: models.BigBang | None, request: CostEstimateRequest) -> dict[str, Any]:
    pricing = openrouter_pricing_table()
    settings = get_settings()
    observed = _observed_agent_averages(db, big_bang.id if big_bang is not None else None)
    max_parallel = request.max_parallel_cohort_decisions or int(
        getattr(settings, "max_parallel_cohort_decisions", 1) or 1
    )
    max_parallel = max(1, max_parallel)

    config = _latest_config(db, big_bang) if big_bang is not None else None
    max_ticks = _coalesce_int(
        request.max_ticks,
        request.simulation_config.get("max_ticks"),
        (config.simulation_config or {}).get("max_ticks") if config is not None else None,
        12,
    )
    latest_tick = _latest_tick_index(db, big_bang.id) if big_bang is not None else 0
    remaining_ticks = request.remaining_ticks
    if remaining_ticks is None:
        remaining_ticks = max(0, max_ticks - latest_tick)

    branch_threshold = _coalesce_float(
        request.branch_threshold,
        request.simulation_config.get("branch_threshold"),
        (config.branch_policy or {}).get("branch_threshold") if config is not None else None,
        0.5,
    )
    active_multiverses = _active_multiverse_count(db, big_bang.id) if big_bang is not None else 0
    multiverse_count = _coalesce_int(request.assumed_multiverses, active_multiverses, 1)
    cohort_count = _coalesce_int(
        request.assumed_cohorts,
        _actor_count(db, big_bang.id, hero=False) if big_bang is not None else None,
        3,
    )
    hero_count = _coalesce_int(
        request.assumed_heroes,
        _actor_count(db, big_bang.id, hero=True) if big_bang is not None else None,
        1,
    )
    timeline_ticks = _estimate_timeline_ticks(multiverse_count, remaining_ticks, branch_threshold)
    scenario_chunks = max(0, math.ceil((request.scenario_tokens or 0) / 64_000))

    call_counts = {
        "initializer_chunk_extractor": scenario_chunks if big_bang is None else 0,
        "initializer_agent": 1 if big_bang is None else 0,
        "cohort_agent": timeline_ticks * cohort_count,
        "hero_agent": timeline_ticks * hero_count,
        "event_summary": timeline_ticks,
        "god_agent": timeline_ticks,
        "endpoint_ledger": 0,
        "predicate_extractor": 1 if request.include_reports else 0,
        "predicate_resolver": max(1, multiverse_count) if request.include_reports else 0,
        "single_report_agent": max(1, multiverse_count) if request.include_reports else 0,
        "final_report_agent": 1 if request.include_reports else 0,
    }
    include = {item for item in request.include_agent_types if item}
    exclude = {item for item in request.exclude_agent_types if item}
    if include:
        call_counts = {key: value for key, value in call_counts.items() if key in include}
    if exclude:
        call_counts = {key: value for key, value in call_counts.items() if key not in exclude}

    by_agent: dict[str, dict[str, Any]] = {}
    openrouter_only_usd = 0.0
    including_non_openrouter_usd = 0.0
    unknown_models: list[dict[str, Any]] = []
    for agent, calls in call_counts.items():
        if calls <= 0:
            continue
        route = _route_for_agent(agent)
        provider, model = _provider_model_for_agent(db, request, route, agent)
        spec = _agent_spec(agent, observed)
        avg_tick = max(latest_tick + 1, 1)
        input_tokens = spec.input_tokens + int(spec.context_growth_tokens_per_tick * avg_tick)
        output_tokens = spec.output_tokens
        cost = _estimate_usage_usd(
            pricing,
            model,
            prompt_tokens=input_tokens * calls,
            completion_tokens=output_tokens * calls,
        )
        is_openrouter = provider == "openrouter"
        if cost is None:
            unknown_models.append({"agent_type": agent, "provider": provider, "model": model})
            cost = 0.0
        if is_openrouter:
            openrouter_only_usd += cost
        if is_openrouter or request.include_non_openrouter:
            including_non_openrouter_usd += cost
        by_agent[agent] = {
            "provider": provider,
            "model": model,
            "estimated_calls": calls,
            "estimated_prompt_tokens": input_tokens * calls,
            "estimated_completion_tokens": output_tokens * calls,
            "estimated_total_tokens": (input_tokens + output_tokens) * calls,
            "estimated_usd": round(cost, 10),
            "pricing_source": "openrouter_models_api" if cost else None,
        }

    per_timeline_tick_seconds = (
        math.ceil(cohort_count / max_parallel) * _agent_spec("cohort_agent", observed).duration_seconds
        + hero_count * _agent_spec("hero_agent", observed).duration_seconds
        + _agent_spec("event_summary", observed).duration_seconds
        + _agent_spec("god_agent", observed).duration_seconds
    )
    wall_seconds = timeline_ticks * per_timeline_tick_seconds
    if big_bang is None:
        wall_seconds += scenario_chunks * _agent_spec("initializer_chunk_extractor", observed).duration_seconds
        wall_seconds += _agent_spec("initializer_agent", observed).duration_seconds
    if request.include_reports:
        single_report_batches = math.ceil(max(1, multiverse_count) / REPORT_SINGLE_REPORT_PARALLELISM)
        wall_seconds += _agent_spec("predicate_extractor", observed).duration_seconds
        wall_seconds += max(1, multiverse_count) * _agent_spec("predicate_resolver", observed).duration_seconds
        wall_seconds += single_report_batches * _agent_spec("single_report_agent", observed).duration_seconds
        wall_seconds += _agent_spec("final_report_agent", observed).duration_seconds

    return {
        "currency": "USD",
        "scope": "post_big_bang" if big_bang is not None else "pre_big_bang",
        "pricing": {
            "provider": "openrouter",
            "status": pricing.get("status"),
            "source": pricing.get("source"),
        },
        "estimated": {
            "openrouter_only_usd": round(openrouter_only_usd, 10),
            "including_non_openrouter_usd": round(including_non_openrouter_usd, 10),
            "unknown_model_count": len(unknown_models),
        },
        "time_estimate": {
            "estimated_wall_seconds": round(wall_seconds, 4),
            "estimated_wall_minutes": round(wall_seconds / 60.0, 4),
            "parallelism": {
                "cohort_agent": max_parallel,
                "hero_agent": 1,
                "single_report_agent": REPORT_SINGLE_REPORT_PARALLELISM,
            },
            "timeline_ticks_estimated": timeline_ticks,
        },
        "assumptions": {
            "max_ticks": max_ticks,
            "remaining_ticks": remaining_ticks,
            "latest_tick_index": latest_tick,
            "branch_threshold": branch_threshold,
            "active_or_assumed_multiverse_count": multiverse_count,
            "cohort_count": cohort_count,
            "hero_count": hero_count,
            "scenario_chunks_64k": scenario_chunks,
            "context_growth_mode": "linear_per_future_tick",
        },
        "by_agent": by_agent,
        "unknown_models": unknown_models,
    }


def _calls_for_tick(db: Session, tick: models.TickSnapshot) -> list[models.LLMCall]:
    calls = db.scalars(
        select(models.LLMCall)
        .where(models.LLMCall.big_bang_id == tick.big_bang_id)
        .order_by(models.LLMCall.created_at.asc())
    ).all()
    marker = f"_tick_{tick.tick_index}"
    god_prefix = f"god_review_{tick.multiverse_id}_tick_{tick.tick_index}"
    start = tick.created_at
    end = tick.updated_at
    return [
        call
        for call in calls
        if marker in call.purpose
        or call.purpose.startswith(f"event_summary_{tick.multiverse_id}_tick_{tick.tick_index}")
        or call.purpose.startswith(god_prefix)
        or (start is not None and end is not None and start <= call.created_at <= end)
    ]


def _usage(call: models.LLMCall) -> dict[str, Any]:
    meta = call.meta if isinstance(call.meta, dict) else {}
    usage = meta.get("usage")
    return usage if isinstance(usage, dict) else {}


def _reported_cost_usd(usage: dict[str, Any]) -> float | None:
    for key in ("cost", "total_cost", "cost_usd"):
        value = usage.get(key)
        if value is not None:
            return _float_or_none(value)
    return None


def _estimate_usage_usd(
    pricing: dict[str, Any],
    model: str,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
    reasoning_tokens: int = 0,
) -> float | None:
    price = _pricing_for_model(pricing, model)
    if price is None:
        return None
    billable_prompt = max(0, prompt_tokens - cached_tokens - cache_write_tokens)
    total = (
        billable_prompt * _float_or_zero(price.get("prompt"))
        + completion_tokens * _float_or_zero(price.get("completion"))
        + cached_tokens * _float_or_zero(price.get("input_cache_read"))
        + cache_write_tokens * _float_or_zero(price.get("input_cache_write"))
        + reasoning_tokens * _float_or_zero(price.get("internal_reasoning"))
        + _float_or_zero(price.get("request"))
    )
    return total


def _pricing_for_model(pricing: dict[str, Any], model: str) -> dict[str, Any] | None:
    models = pricing.get("models") if isinstance(pricing.get("models"), dict) else {}
    candidates = [model]
    if "/" not in model:
        candidates.append(f"{OPENAI_CODEX_OPENROUTER_PREFIX}{model}")
    for candidate in candidates:
        value = models.get(candidate)
        if isinstance(value, dict):
            return value
    return None


def _add_breakdown(
    target: dict[str, dict[str, Any]],
    key: str,
    total_tokens: int,
    cost: float | None,
    cost_kind: str,
) -> None:
    row = target.setdefault(
        key,
        {
            "calls": 0,
            "total_tokens": 0,
            "actual_openrouter_usd": 0.0,
            "estimated_usd": 0.0,
            "unknown_usd_calls": 0,
        },
    )
    row["calls"] += 1
    row["total_tokens"] += total_tokens
    if cost is None:
        row["unknown_usd_calls"] += 1
    elif cost_kind == "actual_openrouter_usd":
        row["actual_openrouter_usd"] = round(float(row["actual_openrouter_usd"]) + cost, 10)
    else:
        row["estimated_usd"] = round(float(row["estimated_usd"]) + cost, 10)


def _empty_token_totals() -> dict[str, int]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
    }


def _duration_seconds(call: models.LLMCall) -> float | None:
    if call.created_at is None or call.updated_at is None:
        return None
    return round(max(0.0, (call.updated_at - call.created_at).total_seconds()), 4)


def _report_big_bang_id(db: Session, report_version: models.ReportVersion):
    report = db.get(models.Report, report_version.report_id)
    return report.big_bang_id if report is not None else None


def _latest_config(db: Session, big_bang: models.BigBang | None) -> models.BigBangConfig | None:
    if big_bang is None:
        return None
    return db.scalar(
        select(models.BigBangConfig)
        .where(models.BigBangConfig.big_bang_id == big_bang.id)
        .order_by(models.BigBangConfig.version.desc())
        .limit(1)
    )


def _latest_tick_index(db: Session, big_bang_id) -> int:
    value = db.scalar(select(func.max(models.TickSnapshot.tick_index)).where(models.TickSnapshot.big_bang_id == big_bang_id))
    return int(value or 0)


def _active_multiverse_count(db: Session, big_bang_id) -> int:
    value = db.scalar(
        select(func.count(models.Multiverse.id)).where(
            models.Multiverse.big_bang_id == big_bang_id,
            models.Multiverse.status.in_(["active", "candidate"]),
        )
    )
    return int(value or 0)


def _actor_count(db: Session, big_bang_id, *, hero: bool) -> int:
    stmt = select(func.count(models.Actor.id)).where(models.Actor.big_bang_id == big_bang_id, models.Actor.status == "active")
    if hero:
        stmt = stmt.where(models.Actor.actor_type == "hero")
    else:
        stmt = stmt.where(models.Actor.actor_type != "hero")
    return int(db.scalar(stmt) or 0)


def _estimate_timeline_ticks(multiverse_count: int, remaining_ticks: int, branch_threshold: float) -> int:
    if remaining_ticks <= 0:
        return 0
    if branch_threshold < 0.35:
        growth = 1.35
    elif branch_threshold < 0.65:
        growth = 1.15
    else:
        growth = 1.0
    total = 0
    active = max(1.0, float(multiverse_count))
    for _ in range(remaining_ticks):
        total += max(1, round(active))
        active *= growth
    return total


def _observed_agent_averages(db: Session, big_bang_id) -> dict[str, AgentEstimateSpec]:
    if big_bang_id is None:
        return {}
    calls = db.scalars(select(models.LLMCall).where(models.LLMCall.big_bang_id == big_bang_id)).all()
    buckets: dict[str, list[tuple[int, int, float]]] = {}
    for call in calls:
        usage = _usage(call)
        duration = _duration_seconds(call)
        if duration is None:
            continue
        buckets.setdefault(agent_type_for_call(call), []).append(
            (
                _int(usage.get("prompt_tokens")),
                _int(usage.get("completion_tokens")),
                duration,
            )
        )
    observed: dict[str, AgentEstimateSpec] = {}
    for agent, rows in buckets.items():
        if not rows:
            continue
        observed[agent] = AgentEstimateSpec(
            input_tokens=max(1, round(sum(row[0] for row in rows) / len(rows))),
            output_tokens=max(1, round(sum(row[1] for row in rows) / len(rows))),
            duration_seconds=max(1.0, sum(row[2] for row in rows) / len(rows)),
            context_growth_tokens_per_tick=DEFAULT_AGENT_SPECS.get(agent, AgentEstimateSpec(4_000, 500, 30)).context_growth_tokens_per_tick,
        )
    return observed


def _agent_spec(agent: str, observed: dict[str, AgentEstimateSpec]) -> AgentEstimateSpec:
    return observed.get(agent) or DEFAULT_AGENT_SPECS.get(agent) or AgentEstimateSpec(4_000, 500, 30)


def _route_for_agent(agent: str) -> AuditedLLMRoute | None:
    if agent == "god_agent":
        return AuditedLLMRoute.GOD_AGENT
    if agent in {str(route.route) for route in AUDITED_LLM_ROUTES}:
        return AuditedLLMRoute(agent)
    return None


def _provider_model_for_agent(
    db: Session,
    request: CostEstimateRequest,
    route: AuditedLLMRoute | None,
    agent: str,
) -> tuple[str, str]:
    model_key = f"{agent}_model"
    provider_key = f"{agent}_provider"
    provider = request.llm_model_config.get(provider_key)
    model = request.llm_model_config.get(model_key)
    if model:
        return str(provider or ("openai-codex" if str(model).startswith("gpt-") else "openrouter")), str(model)
    resolved = resolve_audited_llm_route(db, route=route)
    return resolved.primary.provider, resolved.primary.model


def _coalesce_int(*values: Any) -> int:
    for value in values:
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _coalesce_float(*values: Any) -> float:
    for value in values:
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _float_or_zero(value: Any) -> float:
    return _float_or_none(value) or 0.0
