"""Provider package — singleton registry, ``call_with_policy`` orchestrator.

This module is the single entrypoint every LLM call in WorldFork goes
through. It enforces:

* per-provider rate limits / budget / concurrency
* retry + fallback policy
* call persistence to the ledger + (later) DB
* invalid-JSON safe no-op output
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
from datetime import UTC, datetime
from time import perf_counter
from typing import TYPE_CHECKING

from backend.app.core.ids import new_id
from backend.app.llm.local_provider_policy import (
    OPENAI_COMPATIBLE_API_SHAPES,
    normalize_api_shape,
    normalize_provider_name,
    should_allow_no_auth_openai_provider,
)
from backend.app.providers.base import BaseProvider, LLMProvider
from backend.app.providers.errors import (
    BudgetExceededError,
    FallbackExhaustedError,
    InvalidJSONError,
    ProviderError,
    ProviderTimeoutError,
    RateLimitError,
)
from backend.app.providers.rate_limits import ProviderRateLimiter, RedisTokenBucket
from backend.app.providers.routing import RoutingTable
from backend.app.schemas.jobs import JobType
from backend.app.schemas.llm import LLMResult, ModelConfig, PromptPacket

if TYPE_CHECKING:
    from backend.app.storage.ledger import Ledger

__all__ = [
    "LLMProvider",
    "BaseProvider",
    "ProviderError",
    "RateLimitError",
    "ProviderTimeoutError",
    "InvalidJSONError",
    "BudgetExceededError",
    "FallbackExhaustedError",
    "RedisTokenBucket",
    "ProviderRateLimiter",
    "RoutingTable",
    "register_provider",
    "get_provider",
    "clear_registry",
    "rebuild_registry_from_settings",
    "call_with_policy",
    "initialize_providers_from_settings",
    "prompt_token_estimate",
    "estimate_call_cost",
    "safe_noop_result",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Singleton registry
# ---------------------------------------------------------------------------

_PROVIDER_REGISTRY: dict[str, LLMProvider] = {}
# Track which event loop the registry was built for. Celery workers run each task
# in a fresh asyncio.run() loop; cached redis.asyncio clients bound to a stale loop
# blow up with "Future attached to a different loop". `ensure_providers_in_loop`
# rebuilds the registry whenever the running loop differs from `_REGISTRY_LOOP_ID`.
_REGISTRY_LOOP_ID: int | None = None


def register_provider(name: str, provider: LLMProvider) -> None:
    """Register *provider* under *name* (overwriting any prior entry)."""
    _PROVIDER_REGISTRY[name] = provider


def get_provider(name: str) -> LLMProvider:
    """Return the registered provider for *name* or raise :class:`KeyError`."""
    try:
        return _PROVIDER_REGISTRY[name]
    except KeyError as exc:
        raise KeyError(
            f"No LLM provider registered for {name!r}. "
            f"Registered: {sorted(_PROVIDER_REGISTRY.keys())}"
        ) from exc


def clear_registry() -> None:
    """Test helper — drop every registered provider."""
    _PROVIDER_REGISTRY.clear()
    global _REGISTRY_LOOP_ID
    _REGISTRY_LOOP_ID = None


async def rebuild_registry_from_settings(settings) -> None:  # type: ignore[no-untyped-def]
    """Drop cached providers and rebuild them from the latest settings."""
    _PROVIDER_REGISTRY.clear()
    global _REGISTRY_LOOP_ID
    _REGISTRY_LOOP_ID = None
    from backend.app.core.redis_client import reset_redis_pool
    reset_redis_pool()
    await initialize_providers_from_settings(settings)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _REGISTRY_LOOP_ID = id(loop)


async def ensure_providers_in_loop(settings) -> None:  # type: ignore[no-untyped-def]
    """Rebuild the provider registry if the running loop has changed.

    Each Celery task uses its own asyncio.run() loop; a redis.asyncio client cached
    in a previous loop will fail. Call this at the top of every async task body so
    OpenRouter (etc.) is wired against the current loop's event-loop primitives.
    """
    import asyncio
    global _REGISTRY_LOOP_ID
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # not inside a loop — nothing to bind
        return
    loop_id = id(loop)
    if _REGISTRY_LOOP_ID == loop_id and _PROVIDER_REGISTRY:
        return
    _PROVIDER_REGISTRY.clear()
    # Drop cached redis.asyncio client; the new loop must own its own client.
    from backend.app.core.redis_client import reset_redis_pool
    reset_redis_pool()
    await initialize_providers_from_settings(settings)
    _REGISTRY_LOOP_ID = loop_id


# ---------------------------------------------------------------------------
# Cost / token estimation
# ---------------------------------------------------------------------------

# Coarse $/1K-token table for the OpenRouter-routed defaults. When unknown we
# return 0.0 — fail-open so a missing entry never blocks a call.
_PRICE_PER_1K: dict[str, float] = {
    "google/gemini-3.1-flash-lite-preview": 0.00001,
    "deepseek/deepseek-v3.2": 0.00026,
    "deepseek/deepseek-v4-pro": 0.00174,
    "deepseek/deepseek-v4-flash": 0.00001,
    "gpt-5.4": 0.0,
    "openai/gpt-5.4": 0.0,
    "openai/gpt-4o-mini": 0.00015,
    "anthropic/claude-3-5-sonnet": 0.003,
    "anthropic/claude-3-5-haiku": 0.0008,
}


def prompt_token_estimate(prompt: PromptPacket) -> int:
    """Estimate the token count for a prompt — char/4 heuristic."""
    body = json.dumps(prompt.model_dump(mode="json"), default=str)
    return max(1, len(body) // 4)


def estimate_call_cost(cfg: ModelConfig, est_tokens: int) -> float:
    """Best-effort dollar cost estimate for one call (fail-open)."""
    rate = _PRICE_PER_1K.get(cfg.model, 0.0)
    return (est_tokens / 1000.0) * rate


def _jitter(base: float = 0.25) -> float:
    return random.uniform(0.0, base)


# ---------------------------------------------------------------------------
# Safe no-op result for InvalidJSONError fallback
# ---------------------------------------------------------------------------

def _safe_noop_payload(actor_kind: str) -> dict:
    """Build a minimal cohort/hero/god output that the engine treats as a no-op."""
    if actor_kind == "god":
        return {
            "decision": "continue",
            "branch_delta": None,
            "marked_key_events": [],
            "tick_summary": "[safe no-op: invalid JSON from provider]",
            "rationale": {"reason": "invalid_json_safe_noop"},
        }
    # cohort / hero share the §10.5 shape — emit a single stay_silent action.
    base: dict = {
        "public_actions": [],
        "event_actions": [],
        "social_actions": [{"tool_id": "stay_silent", "args": {}}],
        "self_ratings": {
            "emotions": {},
            "issue_stance": {},
            "perceived_majority": {},
            "willingness_to_speak": 0.0,
        },
        "decision_rationale": {
            "main_factors": ["safe_noop_invalid_json"],
            "uncertainty": "high",
        },
    }
    if actor_kind == "cohort":
        base["split_merge_proposals"] = []
    return base


def safe_noop_result(
    *,
    provider: str,
    model: str,
    actor_kind: str,
    error_message: str,
) -> LLMResult:
    """Build a synthetic LLMResult that downstream code can apply without harm."""
    return LLMResult(
        call_id=new_id("llm-noop"),
        provider=provider,
        model_used=model,
        prompt_tokens=0,
        completion_tokens=0,
        total_tokens=0,
        cost_usd=0.0,
        latency_ms=0,
        parsed_json=_safe_noop_payload(actor_kind),
        tool_calls=[],
        raw_response={"error": error_message[:500], "safe_noop": True},
        created_at=datetime.now(UTC),
        repaired_once=True,
    )


# ---------------------------------------------------------------------------
# Persistence helper
# ---------------------------------------------------------------------------

async def _persist_call(
    ledger: Ledger | None,
    run_id: str,
    universe_id: str | None,
    tick: int | None,
    result: LLMResult,
    prompt: PromptPacket,
    job_type: JobType,
) -> None:
    """Best-effort persist; never raise into the orchestrator."""
    if ledger is not None:
        try:
            BaseProvider._persist_call(ledger, run_id, universe_id, tick, result, prompt)
        except Exception as exc:  # pragma: no cover — paranoid
            logger.warning("ledger persist failed for call %s: %s", result.call_id, exc)

    try:
        from backend.app.core.db import SessionLocal
        from backend.app.models.llm_calls import LLMCallModel

        prompt_dump = prompt.model_dump(mode="json")
        prompt_hash = hashlib.sha256(
            json.dumps(prompt_dump, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        if universe_id is not None and tick is not None:
            tick_prefix = f"universes/{universe_id}/ticks/tick_{tick:03d}"
            prompt_packet_path = f"{tick_prefix}/visible_packets/{prompt.actor_id}.json"
            response_path = f"{tick_prefix}/llm_calls/{result.call_id}.json"
            parsed_path = f"{tick_prefix}/parsed_decisions.json"
        else:
            prompt_packet_path = f"runs/{run_id}/llm_calls/{result.call_id}/prompt.json"
            response_path = f"runs/{run_id}/llm_calls/{result.call_id}/response.json"
            parsed_path = None

        async with SessionLocal() as session:
            existing = await session.get(LLMCallModel, result.call_id)
            if existing is None:
                session.add(
                    LLMCallModel.from_schema(
                        result,
                        job_type=job_type,
                        prompt_packet_path=prompt_packet_path,
                        prompt_hash=prompt_hash,
                        response_path=response_path,
                        parsed_path=parsed_path,
                        run_id=run_id,
                        universe_id=universe_id,
                        tick=tick,
                        status="succeeded",
                    )
                )
            await session.commit()
    except Exception as exc:  # pragma: no cover — logging mirror must not block sim
        logger.debug("llm_calls DB persist skipped for %s: %s", result.call_id, exc)


# ---------------------------------------------------------------------------
# call_with_policy — the §16.7 backoff/fallback orchestrator
# ---------------------------------------------------------------------------

async def call_with_policy(
    *,
    job_type: JobType,
    prompt: PromptPacket,
    routing: RoutingTable,
    limiter: ProviderRateLimiter,
    ledger: Ledger | None = None,
    run_id: str,
    universe_id: str | None = None,
    tick: int | None = None,
    is_branch_job: bool = False,
    is_p0: bool = False,
    max_attempts: int = 5,
) -> LLMResult:
    """Execute a structured LLM call with full §16.7 backoff/fallback policy.

    Order of operations per attempt:

    1. estimate cost; raise :class:`BudgetExceededError` immediately if breached
    2. enter the provider gate (RPM, TPM, concurrency)
    3. call ``provider.generate_structured``
    4. record actuals + persist to ledger
    5. on RateLimitError: backoff (Retry-After or exponential) + jitter; retry
    6. on ProviderTimeoutError: one retry, then fallback model
    7. on InvalidJSONError: provider has already done one repair; return a
       safe no-op so the simulation can proceed
    8. on any other ProviderError: bounded backoff, then fallback
    9. once both primary and fallback exhaust ``max_attempts``, raise
       :class:`FallbackExhaustedError`
    """
    primary, fallback = routing.route(job_type)
    last_exc: Exception | None = None

    for cfg in (primary, fallback):
        if cfg is None:
            continue
        try:
            provider = get_provider(cfg.provider)
        except KeyError as exc:
            last_exc = exc
            continue
        backoff = 1.0
        attempts = 0
        while attempts < max_attempts:
            attempts += 1
            est_tokens = max(prompt_token_estimate(prompt), 256)
            projected_cost = estimate_call_cost(cfg, est_tokens)
            try:
                await limiter.check_daily_budget(projected_cost)
            except BudgetExceededError:
                # Hard halt — never retry, never fall back.
                raise

            try:
                async with limiter.gate(
                    est_tokens, is_branch_job=is_branch_job, is_p0=is_p0
                ) as ticket:
                    t0 = perf_counter()
                    result = await asyncio.wait_for(
                        provider.generate_structured(prompt, cfg),
                        timeout=cfg.timeout_seconds,
                    )
                    _ = perf_counter() - t0
                # Outside the gate so refunds happen ASAP.
                try:
                    await asyncio.wait_for(
                        limiter.record_actual_usage(
                            ticket, result.total_tokens, result.cost_usd or 0.0
                        ),
                        timeout=10.0,
                    )
                except Exception as exc:
                    logger.warning("provider usage accounting skipped: %s", exc)
                try:
                    await asyncio.wait_for(
                        _persist_call(ledger, run_id, universe_id, tick, result, prompt, job_type),
                        timeout=30.0,
                    )
                except Exception as exc:
                    logger.warning("provider call persistence skipped: %s", exc)
                return result

            except asyncio.TimeoutError:
                last_exc = ProviderTimeoutError(
                    f"provider call exceeded {cfg.timeout_seconds}s timeout"
                )
                if attempts >= 2:
                    logger.warning(
                        "provider %s timed out twice on %s; trying fallback",
                        cfg.provider, cfg.model,
                    )
                    break
                continue

            except RateLimitError as exc:
                last_exc = exc
                wait = (exc.retry_after if exc.retry_after else backoff) + _jitter()
                logger.info(
                    "rate-limited on %s; sleeping %.2fs (attempt %d/%d)",
                    cfg.model, wait, attempts, max_attempts,
                )
                await asyncio.sleep(wait)
                backoff = min(60.0, backoff * 2)
                continue

            except ProviderTimeoutError as exc:
                last_exc = exc
                # Per §16.7: timeout → retry once, then fallback.
                if attempts >= 2:
                    logger.warning(
                        "provider %s timed out twice on %s; trying fallback",
                        cfg.provider, cfg.model,
                    )
                    break
                continue

            except InvalidJSONError as exc:
                # The provider already attempted one repair internally; per §16.7
                # we now emit a safe no-op so the simulation continues.
                last_exc = exc
                logger.warning(
                    "invalid JSON after one repair on %s; emitting safe no-op",
                    cfg.model,
                )
                result = safe_noop_result(
                    provider=cfg.provider,
                    model=cfg.model,
                    actor_kind=prompt.actor_kind,
                    error_message=str(last_exc),
                )
                await _persist_call(
                    ledger, run_id, universe_id, tick, result, prompt, job_type
                )
                return result

            except BudgetExceededError:
                raise

            except ProviderError as exc:
                last_exc = exc
                if backoff > 30:
                    logger.warning(
                        "provider %s repeated errors on %s; trying fallback",
                        cfg.provider, cfg.model,
                    )
                    break
                wait = backoff + _jitter()
                logger.info(
                    "provider error on %s: %s — sleeping %.2fs",
                    cfg.model, exc, wait,
                )
                await asyncio.sleep(wait)
                backoff *= 2
                continue

    # Both primary + fallback exhausted.
    if isinstance(last_exc, InvalidJSONError):
        # Build a safe no-op LLMResult so the engine carries on.
        result = safe_noop_result(
            provider=primary.provider,
            model=primary.model,
            actor_kind=prompt.actor_kind,
            error_message=str(last_exc),
        )
        await _persist_call(ledger, run_id, universe_id, tick, result, prompt, job_type)
        return result

    msg = (
        f"call_with_policy exhausted all attempts for job_type={job_type} "
        f"(primary={primary.model}, fallback={fallback.model if fallback else None}); "
        f"last error: {type(last_exc).__name__ if last_exc else 'none'}: {last_exc}"
    )
    raise FallbackExhaustedError(msg)


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

async def initialize_providers_from_settings(settings) -> None:  # type: ignore[no-untyped-def]
    """Register provider instances based on environment configuration.

    Called from FastAPI lifespan startup. Provider adapters own their upstream
    call shape and auth token resolution; orchestration only sees the registry.
    """
    from backend.app.providers.openai_codex import (
        OPENAI_CODEX_OAUTH_ENV,
        OPENAI_CODEX_RESPONSES_BASE_URL,
        OpenAICodexProvider,
    )
    from backend.app.providers.openrouter import OpenRouterProvider

    openrouter_base_url = settings.openrouter_base_url
    openrouter_api_key_env = "OPENROUTER_API_KEY"
    openrouter_default_model = settings.default_model
    openrouter_fallback_model = settings.fallback_model
    openrouter_enabled = True
    codex_base_url = getattr(
        settings, "openai_codex_base_url", OPENAI_CODEX_RESPONSES_BASE_URL
    )
    codex_api_key_env = OPENAI_CODEX_OAUTH_ENV
    codex_default_model = getattr(settings, "openai_codex_default_model", "gpt-5.4")
    codex_fallback_model = getattr(settings, "openai_codex_fallback_model", None)
    codex_env_enabled = bool(getattr(settings, "openai_codex_enabled", False))
    codex_enabled = codex_env_enabled
    provider_rows_by_name = {}

    try:
        from backend.app.core.db import SessionLocal
        from backend.app.models.settings import ProviderSettingModel
        from sqlalchemy import select

        async with SessionLocal() as session:
            result = await session.execute(select(ProviderSettingModel))
            provider_rows_by_name = {row.provider: row for row in result.scalars().all()}
            row = provider_rows_by_name.pop("openrouter", None)
            if row is not None:
                openrouter_base_url = row.base_url or openrouter_base_url
                openrouter_api_key_env = row.api_key_env or openrouter_api_key_env
                openrouter_default_model = row.default_model or openrouter_default_model
                openrouter_fallback_model = row.fallback_model or openrouter_fallback_model
                openrouter_enabled = bool(row.enabled)
            row = provider_rows_by_name.pop("openai-codex", None)
            if row is not None:
                codex_base_url = row.base_url or codex_base_url
                codex_api_key_env = row.api_key_env or codex_api_key_env
                codex_default_model = row.default_model or codex_default_model
                codex_fallback_model = row.fallback_model or codex_fallback_model
                codex_enabled = bool(row.enabled) or codex_env_enabled
    except Exception:
        # DB settings are optional at boot; migrations may not have run yet.
        pass

    import os
    openrouter_api_key = os.environ.get(openrouter_api_key_env) or getattr(
        settings, "openrouter_api_key", ""
    )

    if openrouter_enabled and openrouter_api_key:
        openrouter_provider = OpenRouterProvider(
            api_key=openrouter_api_key,
            base_url=openrouter_base_url,
            default_model=openrouter_default_model,
            fallback_model=openrouter_fallback_model,
            http_referer=settings.openrouter_http_referer,
            x_title=settings.openrouter_title,
        )
        register_provider("openrouter", openrouter_provider)
        logger.info("registered OpenRouter provider (model=%s)", openrouter_default_model)

    if codex_enabled:
        codex_provider = OpenAICodexProvider(
            oauth_token=getattr(settings, "openai_codex_oauth_token", None),
            oauth_token_env=codex_api_key_env,
            codex_auth_file=getattr(settings, "openai_codex_auth_file", None),
            base_url=codex_base_url,
            default_model=codex_default_model,
            fallback_model=codex_fallback_model,
        )
        if codex_provider.has_oauth_token():
            register_provider("openai-codex", codex_provider)
            logger.info("registered OpenAI Codex provider (model=%s)", codex_default_model)
        else:
            logger.warning(
                "OpenAI Codex provider enabled but no OAuth token was found "
                "(env=%s or Codex CLI auth file)",
                codex_api_key_env,
            )

    for row in provider_rows_by_name.values():
        _register_generic_provider_row(row, settings)


def _register_generic_provider_row(row, settings) -> None:  # type: ignore[no-untyped-def]
    if not bool(row.enabled):
        return
    payload = row.payload if isinstance(row.payload, dict) else {}
    api_shape = normalize_api_shape(
        str(payload.get("provider_api") or payload.get("api_shape") or payload.get("api") or "openai-compatible")
    )
    provider_name = normalize_provider_name(row.provider)
    if provider_name == "anthropic" or api_shape in {"anthropic", "anthropic-direct"}:
        logger.warning(
            "direct Anthropic provider rows are not registered in this build; "
            "route Anthropic models through OpenRouter instead"
        )
        return
    if provider_name == "ollama" or api_shape in {"ollama", "ollama-openai"}:
        from backend.app.providers.ollama import OllamaProvider

        ollama_provider = OllamaProvider(
            base_url=row.base_url,
            default_model=row.default_model,
            fallback_model=row.fallback_model,
        )
        ollama_provider.name = row.provider
        register_provider(row.provider, ollama_provider)
        logger.info("registered Ollama-compatible provider %s", row.provider)
        return
    if api_shape not in OPENAI_COMPATIBLE_API_SHAPES:
        logger.warning(
            "unsupported provider api_shape=%s for provider %s; not registering",
            api_shape,
            row.provider,
        )
        return
    import os

    api_key = os.environ.get(row.api_key_env)
    if not api_key and (
        should_allow_no_auth_openai_provider(
            provider=row.provider,
            api_key_env=row.api_key_env,
            api_shape=api_shape,
            base_url=row.base_url,
        )
    ):
        api_key = str(payload.get("api_key") or payload.get("dummy_api_key") or "local")
    if not api_key:
        logger.warning(
            "provider %s enabled but missing API key env %s",
            row.provider,
            row.api_key_env,
        )
        return
    provider: LLMProvider
    if row.provider == "openai":
        from backend.app.providers.openai import OpenAIProvider

        provider = OpenAIProvider(
            api_key=api_key,
            base_url=row.base_url,
            default_model=row.default_model,
            fallback_model=row.fallback_model,
            http_referer=settings.openrouter_http_referer,
            x_title=settings.openrouter_title,
        )
    else:
        from backend.app.providers.openrouter import OpenRouterProvider

        provider = OpenRouterProvider(
            api_key=api_key,
            base_url=row.base_url,
            default_model=row.default_model,
            fallback_model=row.fallback_model,
            http_referer=settings.openrouter_http_referer,
            x_title=settings.openrouter_title,
        )
        provider.name = row.provider
    register_provider(row.provider, provider)
    logger.info("registered OpenAI-compatible provider %s", row.provider)
