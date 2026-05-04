"""
Database seed script.

Inserts default rows for all settings tables.
Idempotent: uses upserts for managed singleton tables and insert-missing
behavior for user-editable routing rows.
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as pg_insert

from backend.app.core.config import settings
from backend.app.core.db import SyncSessionLocal
from backend.app.models.settings import (
    BranchPolicySettingModel,
    GlobalSettingModel,
    ModelRoutingEntryModel,
    ProviderSettingModel,
    RateLimitSettingModel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SOT_ROOT = Path(__file__).resolve().parents[3] / "source_of_truth"


def _load_sot() -> dict:
    path = _SOT_ROOT / "sociology_parameters.json"
    with path.open() as f:
        return json.load(f)


def _upsert(session, model, pk_col: str, rows: list[dict]) -> int:
    """Upsert rows into a table keyed by pk_col."""
    if not rows:
        return 0
    table = model.__table__
    stmt = pg_insert(table).values(rows)
    update_cols = {c.name: stmt.excluded[c.name] for c in table.c if c.name != pk_col}
    stmt = stmt.on_conflict_do_update(index_elements=[pk_col], set_=update_cols)
    session.execute(stmt)
    return len(rows)


def _insert_missing(session, model, pk_col: str, rows: list[dict]) -> int:
    """Insert rows keyed by pk_col without changing existing user-edited rows."""
    if not rows:
        return 0
    table = model.__table__
    stmt = pg_insert(table).values(rows)
    stmt = stmt.on_conflict_do_nothing(index_elements=[pk_col])
    session.execute(stmt)
    return len(rows)


# ---------------------------------------------------------------------------
# Seed functions
# ---------------------------------------------------------------------------

def _seed_global(session, sot: dict) -> None:
    branching = sot.get("branching_defaults", {})
    row = {
        "setting_id": "default",
        "default_tick_duration_minutes": 120,
        "default_max_ticks": 48,
        "default_max_schedule_horizon_ticks": 5,
        "log_level": settings.log_level,
        "display_timezone": "UTC",
        "theme": "system",
        "enable_oasis_adapter": False,
        "branching_defaults": branching,
        "payload": {
            "default_tick_duration_minutes": 120,
            "default_max_ticks": 48,
            "default_max_schedule_horizon_ticks": 5,
            "log_level": settings.log_level,
            "display_timezone": "UTC",
            "theme": "system",
            "enable_oasis_adapter": False,
            "run_folder_root": "runs",
            "default_representation_mode_thresholds": {
                "micro": [2, 25],
                "small": [25, 250],
                "population": [250, 5000],
                "mass": [5000, None],
            },
            "branching_defaults": branching,
        },
    }
    _upsert(session, GlobalSettingModel, "setting_id", [row])
    print("  [global] seeded 1 row")


def _seed_provider(session) -> None:
    rows: list[dict] = [
        {
            "provider": "openrouter",
            "base_url": settings.openrouter_base_url,
            "api_key_env": "OPENROUTER_API_KEY",
            "default_model": settings.default_model,
            "fallback_model": settings.fallback_model,
            "json_mode_required": True,
            "tool_calling_enabled": True,
            "enabled": True,
            "extra_headers": {
                "HTTP-Referer": settings.openrouter_http_referer,
                "X-Title": settings.openrouter_title,
            },
            "payload": {
                "provider": "openrouter",
                "base_url": settings.openrouter_base_url,
                "api_key_env": "OPENROUTER_API_KEY",
                "default_model": settings.default_model,
                "fallback_model": settings.fallback_model,
                "json_mode_required": True,
                "tool_calling_enabled": True,
                "enabled": True,
                "extra_headers": {
                    "HTTP-Referer": settings.openrouter_http_referer,
                    "X-Title": settings.openrouter_title,
                },
            },
        },
        {
            "provider": "openai-codex",
            "base_url": settings.openai_codex_base_url,
            "api_key_env": "OPENAI_CODEX_OAUTH_TOKEN",
            "default_model": settings.openai_codex_default_model,
            "fallback_model": settings.openai_codex_fallback_model,
            "json_mode_required": True,
            "tool_calling_enabled": False,
            "enabled": settings.openai_codex_enabled,
            "extra_headers": {},
            "payload": {
                "provider": "openai-codex",
                "api": "openai-codex-responses",
                "auth_mode": "oauth",
                "base_url": settings.openai_codex_base_url,
                "api_key_env": "OPENAI_CODEX_OAUTH_TOKEN",
                "auth_file": "~/.worldfork/openai-codex-auth.json",
                "codex_cli_auth_fallback": "~/.codex/auth.json",
                "default_model": settings.openai_codex_default_model,
                "fallback_model": settings.openai_codex_fallback_model,
                "json_mode_required": True,
                "tool_calling_enabled": False,
                "enabled": settings.openai_codex_enabled,
            },
        },
    ]
    _upsert(session, ProviderSettingModel, "provider", rows)
    print(f"  [provider] seeded {len(rows)} rows")


# Runtime model routing defaults.
_OPENROUTER_MODEL = settings.default_model
_MODEL_SETTING_BY_JOB_TYPE = {
    "initialize_big_bang": "initializer_agent_model",
    "initializer_chunk_extractor": "initializer_agent_model",
    "initializer_agent": "initializer_agent_model",
    "god_agent_review": "god_agent_model",
    "god_agent": "god_agent_model",
    "endpoint_ledger": "god_agent_model",
    "evaluate_endpoint_ledger": "god_agent_model",
    "aggregate_run_results": "report_agent_model",
    "report_agent": "report_agent_model",
    "event_summary": "event_summary_model",
    "force_deviation": "god_agent_model",
}

_ROUTING_DEFAULTS = [
    {
        "job_type": "initialize_big_bang",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.3,
        "top_p": 1.0,
        "max_tokens": 4096,
        "max_concurrency": 4,
        "requests_per_minute": 60,
        "tokens_per_minute": 200000,
        "timeout_seconds": 60,
        "retry_policy": "exponential_backoff",
        "daily_budget_usd": None,
    },
    {
        "job_type": "simulate_universe_tick",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.7,
        "top_p": 1.0,
        "max_tokens": 8192,
        "max_concurrency": 8,
        "requests_per_minute": 60,
        "tokens_per_minute": 400000,
        "timeout_seconds": 120,
        "retry_policy": "exponential_backoff",
        "daily_budget_usd": None,
    },
    {
        "job_type": "actor_deliberation_call",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.8,
        "top_p": 1.0,
        "max_tokens": 4096,
        "max_concurrency": 16,
        "requests_per_minute": 120,
        "tokens_per_minute": 400000,
        "timeout_seconds": 90,
        "retry_policy": "exponential_backoff",
        "daily_budget_usd": None,
    },
    {
        "job_type": "execute_due_events",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.5,
        "top_p": 1.0,
        "max_tokens": 4096,
        "max_concurrency": 8,
        "requests_per_minute": 60,
        "tokens_per_minute": 200000,
        "timeout_seconds": 60,
        "retry_policy": "exponential_backoff",
        "daily_budget_usd": None,
    },
    {
        "job_type": "social_propagation",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.6,
        "top_p": 1.0,
        "max_tokens": 2048,
        "max_concurrency": 16,
        "requests_per_minute": 200,
        "tokens_per_minute": 400000,
        "timeout_seconds": 45,
        "retry_policy": "exponential_backoff",
        "daily_budget_usd": None,
    },
    {
        "job_type": "sociology_update",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.4,
        "top_p": 1.0,
        "max_tokens": 4096,
        "max_concurrency": 4,
        "requests_per_minute": 30,
        "tokens_per_minute": 150000,
        "timeout_seconds": 90,
        "retry_policy": "exponential_backoff",
        "daily_budget_usd": None,
    },
    {
        "job_type": "god_agent_review",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.2,
        "top_p": 1.0,
        "max_tokens": 8192,
        "max_concurrency": 2,
        "requests_per_minute": 20,
        "tokens_per_minute": 200000,
        "timeout_seconds": 180,
        "retry_policy": "exponential_backoff",
        "daily_budget_usd": None,
    },
    {
        "job_type": "force_deviation",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.2,
        "top_p": 1.0,
        "max_tokens": 4096,
        "max_concurrency": 2,
        "requests_per_minute": 20,
        "tokens_per_minute": 200000,
        "timeout_seconds": 180,
        "retry_policy": "exponential_backoff",
        "daily_budget_usd": None,
    },
    {
        "job_type": "aggregate_run_results",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.25,
        "top_p": 1.0,
        "max_tokens": 8192,
        "max_concurrency": 2,
        "requests_per_minute": 20,
        "tokens_per_minute": 200000,
        "timeout_seconds": 180,
        "retry_policy": "exponential_backoff",
        "daily_budget_usd": None,
    },
    {
        "job_type": "branch_universe",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.3,
        "top_p": 1.0,
        "max_tokens": 4096,
        "max_concurrency": 4,
        "requests_per_minute": 20,
        "tokens_per_minute": 150000,
        "timeout_seconds": 60,
        "retry_policy": "exponential_backoff",
        "daily_budget_usd": None,
    },
    {
        "job_type": "evaluate_endpoint_ledger",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.15,
        "top_p": 1.0,
        "max_tokens": 4096,
        "max_concurrency": 2,
        "requests_per_minute": 20,
        "tokens_per_minute": 200000,
        "timeout_seconds": 180,
        "retry_policy": "exponential_backoff",
        "daily_budget_usd": None,
    },
    {
        "job_type": "build_review_index",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.1,
        "top_p": 1.0,
        "max_tokens": 2048,
        "max_concurrency": 4,
        "requests_per_minute": 30,
        "tokens_per_minute": 100000,
        "timeout_seconds": 60,
        "retry_policy": "linear",
        "daily_budget_usd": None,
    },
    {
        "job_type": "export_run",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.0,
        "top_p": 1.0,
        "max_tokens": 2048,
        "max_concurrency": 2,
        "requests_per_minute": 10,
        "tokens_per_minute": 50000,
        "timeout_seconds": 120,
        "retry_policy": "linear",
        "daily_budget_usd": None,
    },
    {
        "job_type": "apply_tick_results",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.0,
        "top_p": 1.0,
        "max_tokens": 2048,
        "max_concurrency": 8,
        "requests_per_minute": 60,
        "tokens_per_minute": 100000,
        "timeout_seconds": 60,
        "retry_policy": "exponential_backoff",
        "daily_budget_usd": None,
    },
    {
        "job_type": "initializer_agent",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.25,
        "top_p": 1.0,
        "max_tokens": 131072,
        "max_concurrency": 2,
        "requests_per_minute": 20,
        "tokens_per_minute": 1000000,
        "timeout_seconds": 1200,
        "retry_policy": "exponential_backoff",
        "daily_budget_usd": None,
        "payload": {"reasoning": {"effort": "low", "exclude": True}},
    },
    {
        "job_type": "initializer_chunk_extractor",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.15,
        "top_p": 1.0,
        "max_tokens": 32768,
        "max_concurrency": 4,
        "requests_per_minute": 60,
        "tokens_per_minute": 800000,
        "timeout_seconds": 600,
        "retry_policy": "exponential_backoff",
        "daily_budget_usd": None,
        "payload": {"reasoning": {"effort": "low", "exclude": True}},
    },
    {
        "job_type": "god_agent",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.2,
        "top_p": 1.0,
        "max_tokens": 8192,
        "max_concurrency": 2,
        "requests_per_minute": 20,
        "tokens_per_minute": 200000,
        "timeout_seconds": 180,
        "retry_policy": "exponential_backoff",
        "daily_budget_usd": None,
    },
    {
        "job_type": "cohort_agent",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.8,
        "top_p": 1.0,
        "max_tokens": 4096,
        "max_concurrency": 16,
        "requests_per_minute": 120,
        "tokens_per_minute": 400000,
        "timeout_seconds": 90,
        "retry_policy": "exponential_backoff",
        "daily_budget_usd": None,
    },
    {
        "job_type": "hero_agent",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.8,
        "top_p": 1.0,
        "max_tokens": 4096,
        "max_concurrency": 16,
        "requests_per_minute": 120,
        "tokens_per_minute": 400000,
        "timeout_seconds": 90,
        "retry_policy": "exponential_backoff",
        "daily_budget_usd": None,
    },
    {
        "job_type": "event_summary",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.5,
        "top_p": 1.0,
        "max_tokens": 4096,
        "max_concurrency": 8,
        "requests_per_minute": 60,
        "tokens_per_minute": 200000,
        "timeout_seconds": 60,
        "retry_policy": "exponential_backoff",
        "daily_budget_usd": None,
    },
    {
        "job_type": "endpoint_ledger",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.15,
        "top_p": 1.0,
        "max_tokens": 4096,
        "max_concurrency": 2,
        "requests_per_minute": 20,
        "tokens_per_minute": 200000,
        "timeout_seconds": 180,
        "retry_policy": "exponential_backoff",
        "daily_budget_usd": None,
    },
    {
        "job_type": "report_agent",
        "preferred_provider": "openrouter",
        "preferred_model": _OPENROUTER_MODEL,
        "fallback_provider": "openrouter",
        "fallback_model": _OPENROUTER_MODEL,
        "temperature": 0.25,
        "top_p": 1.0,
        "max_tokens": 8192,
        "max_concurrency": 2,
        "requests_per_minute": 20,
        "tokens_per_minute": 200000,
        "timeout_seconds": 300,
        "retry_policy": "exponential_backoff",
        "daily_budget_usd": None,
    },
]


def _routing_model_defaults(row: dict) -> dict:
    row = dict(row)
    model_setting = _MODEL_SETTING_BY_JOB_TYPE.get(row["job_type"])
    model = str(
        getattr(settings, model_setting, None) if model_setting is not None else settings.default_model
    )
    model = model or settings.default_model
    provider = str(settings.default_llm_provider)
    row["preferred_provider"] = provider
    row["preferred_model"] = model
    row["fallback_provider"] = provider
    row["fallback_model"] = model
    return row


def _seed_routing(session) -> None:
    rows = []
    for row in (_routing_model_defaults(r) for r in _ROUTING_DEFAULTS):
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        rows.append(dict(row, payload={**payload, "source": "seed_default"}))
    _insert_missing(session, ModelRoutingEntryModel, "job_type", rows)
    print(f"  [model_routing] seeded {len(rows)} rows")


def _seed_rate_limit(session) -> None:
    row = {
        "provider": "openrouter",
        "enabled": True,
        "rpm_limit": 200,
        "tpm_limit": 500000,
        "max_concurrency": 32,
        "burst_multiplier": 1.5,
        "retry_policy": "exponential_backoff",
        "jitter": True,
        "daily_budget_usd": None,
        "branch_reserved_capacity_pct": 20.0,
        "healthcheck_enabled": True,
        "payload": {},
    }
    _upsert(session, RateLimitSettingModel, "provider", [row])
    print("  [rate_limit] seeded 1 row")


def _seed_branch_policy(session, sot: dict) -> None:
    b = sot.get("branching_defaults", {})
    row = {
        "policy_id": "default",
        "max_active_universes": b.get("max_active_universes", 50),
        "max_total_branches": b.get("max_total_branches", 500),
        "max_depth": b.get("max_depth", 8),
        "max_branches_per_tick": b.get("max_branches_per_tick", 5),
        "branch_cooldown_ticks": b.get("branch_cooldown_ticks", 3),
        "min_divergence_score": b.get("min_divergence_score", 0.35),
        "auto_prune_low_value": b.get("auto_prune_low_value", True),
        "payload": b,
    }
    _upsert(session, BranchPolicySettingModel, "policy_id", [row])
    print("  [branch_policy] seeded 1 row")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print("seed: loading source_of_truth/sociology_parameters.json …")
    sot = _load_sot()

    with SyncSessionLocal() as session:
        _seed_global(session, sot)
        _seed_provider(session)
        _seed_routing(session)
        _seed_rate_limit(session)
        _seed_branch_policy(session, sot)
        session.commit()

    print("seed: done — all defaults inserted/updated.")


if __name__ == "__main__":
    main()
