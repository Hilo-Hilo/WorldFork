"""Per-job-type routing table — runtime model routing.

Loads :class:`ModelRoutingEntry` rows from the database (or falls back to
hardcoded runtime defaults) and exposes ``route(job_type)``
which returns ``(preferred ModelConfig, fallback ModelConfig | None)``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, cast

from backend.app.schemas.jobs import JobType
from backend.app.schemas.llm import ModelConfig
from backend.app.schemas.settings import ModelRoutingEntry
from backend.app.core.config import settings

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from sqlalchemy.ext.asyncio import AsyncSession
    from backend.app.providers.rate_limits import ProviderRateLimiter


# ---------------------------------------------------------------------------
# Defaults — generalized across job types.
# ---------------------------------------------------------------------------

_MODEL_SETTING_BY_JOB_TYPE = {
    "initialize_big_bang": "initializer_agent_model",
    "god_agent_review": "god_agent_model",
    "aggregate_run_results": "report_agent_model",
    "evaluate_endpoint_ledger": "god_agent_model",
    "force_deviation": "god_agent_model",
}


def _default_entry(job_type: JobType) -> ModelRoutingEntry:
    """Return a sane default :class:`ModelRoutingEntry` for *job_type*."""
    model_setting = _MODEL_SETTING_BY_JOB_TYPE.get(job_type)
    model = str(
        getattr(settings, model_setting, None) if model_setting is not None else settings.default_model
    )
    model = model or settings.default_model
    provider = _default_provider_for_model(model, model_setting)
    return ModelRoutingEntry(
        job_type=job_type,
        preferred_provider=provider,
        preferred_model=model,
        fallback_provider=_default_fallback_provider(provider),
        fallback_model=_default_fallback_model(model),
        temperature=0.6,
        top_p=0.95,
        max_tokens=2048,
        max_concurrency=4,
        requests_per_minute=60,
        tokens_per_minute=150_000,
        timeout_seconds=120,
        retry_policy="exponential_backoff",
        daily_budget_usd=None,
    )


def _default_provider_for_model(model: str, model_setting: str | None) -> str:
    if model_setting is not None and model == settings.openai_codex_default_model:
        return "openai-codex"
    return str(settings.default_llm_provider)


def _default_fallback_provider(provider: str) -> str:
    return str(settings.default_llm_provider or provider)


def _default_fallback_model(model: str) -> str:
    return str(settings.fallback_model or model)


_ALL_JOB_TYPES: tuple[JobType, ...] = (
    "initialize_big_bang",
    "simulate_universe_tick",
    "actor_deliberation_call",
    "execute_due_events",
    "social_propagation",
    "sociology_update",
    "god_agent_review",
    "branch_universe",
    "build_review_index",
    "export_run",
    "apply_tick_results",
    "aggregate_run_results",
    "evaluate_endpoint_ledger",
    "force_deviation",
)


# ---------------------------------------------------------------------------
# RoutingTable
# ---------------------------------------------------------------------------

class RoutingTable:
    """In-memory routing table keyed by job type."""

    def __init__(self, entries: dict[JobType, ModelRoutingEntry]) -> None:
        self._entries: dict[JobType, ModelRoutingEntry] = dict(entries)

    # ------------------------------------------------------------------
    # Resolve
    # ------------------------------------------------------------------

    def route(self, job_type: JobType) -> tuple[ModelConfig, ModelConfig | None]:
        """Return ``(preferred ModelConfig, fallback ModelConfig | None)``."""
        entry = self._entries.get(job_type)
        if entry is None:
            entry = _default_entry(job_type)

        native_fallback_model = (
            entry.fallback_model
            if entry.fallback_model and entry.fallback_provider == entry.preferred_provider
            else None
        )
        preferred = ModelConfig(
            provider=entry.preferred_provider,
            model=entry.preferred_model,
            fallback_model=native_fallback_model,
            temperature=entry.temperature,
            top_p=entry.top_p,
            max_tokens=entry.max_tokens,
            response_format=None,
            tools=None,
            timeout_seconds=entry.timeout_seconds,
            retry_policy=entry.retry_policy,
        )

        fallback: ModelConfig | None = None
        if entry.fallback_model and entry.fallback_provider:
            fallback = ModelConfig(
                provider=entry.fallback_provider,
                model=entry.fallback_model,
                fallback_model=None,
                temperature=entry.temperature,
                top_p=entry.top_p,
                max_tokens=entry.max_tokens,
                response_format=None,
                tools=None,
                timeout_seconds=entry.timeout_seconds,
                retry_policy=entry.retry_policy,
            )

        return preferred, fallback

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def defaults(cls) -> RoutingTable:
        """Return a routing table populated with default runtime routing for every job type."""
        entries = {jt: _default_entry(jt) for jt in _ALL_JOB_TYPES}
        return cls(entries)

    @classmethod
    async def from_db(cls, session: AsyncSession) -> RoutingTable:
        """Load routing entries from the ``settings_model_routing`` table.

        Falls back to :meth:`defaults` if the table is empty or unavailable.
        """
        try:
            from sqlalchemy import text  # local import to keep this module import-light
        except Exception:
            return cls.defaults()

        try:
            result = await session.execute(
                text(
                    "SELECT job_type, preferred_provider, preferred_model, "
                    "fallback_provider, fallback_model, temperature, top_p, "
                    "max_tokens, max_concurrency, requests_per_minute, "
                    "tokens_per_minute, timeout_seconds, retry_policy, "
                    "daily_budget_usd, payload FROM settings_model_routing"
                )
            )
            rows = result.mappings().all()
        except Exception:
            return cls.defaults()

        if not rows:
            return cls.defaults()

        entries: dict[JobType, ModelRoutingEntry] = {}
        for row in rows:
            row_dict = dict(row)
            row_dict.pop("payload", None)
            try:
                entry = ModelRoutingEntry(**row_dict)
            except Exception:
                continue
            if entry.job_type not in _ALL_JOB_TYPES:
                continue
            entries[cast(JobType, entry.job_type)] = entry
        # Backfill missing job types with defaults so route() never returns None for known jobs.
        for jt in _ALL_JOB_TYPES:
            entries.setdefault(jt, _default_entry(jt))
        return cls(entries)


async def build_provider_rate_limiter(
    session: AsyncSession,
    redis: aioredis.Redis,
    *,
    provider: str = "openrouter",
) -> ProviderRateLimiter:
    """Build a provider limiter, preferring enabled settings_rate_limit rows."""
    from backend.app.providers.rate_limits import ProviderRateLimiter

    defaults = {
        "rpm_limit": 600,
        "tpm_limit": 1_000_000,
        "max_concurrency": 8,
        "daily_budget_usd": None,
        "burst_multiplier": 1.0,
        "branch_reserved_capacity_pct": 20.0,
        "jitter": False,
    }
    try:
        from backend.app.models.settings import RateLimitSettingModel

        row = await session.get(RateLimitSettingModel, provider)
    except Exception:
        row = None

    if row is not None and bool(row.enabled):
        return ProviderRateLimiter(
            redis,
            provider=provider,
            rpm_limit=row.rpm_limit,
            tpm_limit=row.tpm_limit,
            max_concurrency=row.max_concurrency,
            daily_budget_usd=row.daily_budget_usd,
            burst_multiplier=row.burst_multiplier,
            branch_reserved_capacity_pct=row.branch_reserved_capacity_pct,
            jitter=row.jitter,
        )
    return ProviderRateLimiter(redis, provider=provider, **defaults)
