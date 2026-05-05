"""
Settings ORM models — singleton and per-job-type configuration tables.
Tables: settings_provider, settings_model_routing, settings_rate_limit,
        settings_branch_policy, settings_global
"""
from __future__ import annotations

from sqlalchemy import Boolean, Float, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base, TimestampMixin


class ProviderSettingModel(Base, TimestampMixin):
    """One row per LLM provider."""

    __tablename__ = "settings_provider"

    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    base_url: Mapped[str] = mapped_column(String, nullable=False)
    api_key_env: Mapped[str] = mapped_column(String(128), nullable=False)
    default_model: Mapped[str] = mapped_column(String(128), nullable=False)
    fallback_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    json_mode_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    tool_calling_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extra_headers: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Full payload for round-trip fidelity
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class ModelRoutingEntryModel(Base, TimestampMixin):
    """One row per job_type; controls which model/provider handles each job."""

    __tablename__ = "settings_model_routing"

    job_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    preferred_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    preferred_model: Mapped[str] = mapped_column(String(128), nullable=False)
    fallback_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fallback_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    temperature: Mapped[float] = mapped_column(Float, nullable=False)
    top_p: Mapped[float] = mapped_column(Float, nullable=False)
    max_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    requests_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    tokens_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    daily_budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Full payload
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class RateLimitSettingModel(Base, TimestampMixin):
    """Per-provider rate limit configuration."""

    __tablename__ = "settings_rate_limit"

    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rpm_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    tpm_limit: Mapped[int] = mapped_column(Integer, nullable=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    burst_multiplier: Mapped[float] = mapped_column(Float, nullable=False)
    retry_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    jitter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    daily_budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    branch_reserved_capacity_pct: Mapped[float] = mapped_column(Float, nullable=False)
    healthcheck_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class BranchPolicySettingModel(Base, TimestampMixin):
    """Singleton branch policy (one row, PK = 'default')."""

    __tablename__ = "settings_branch_policy"

    policy_id: Mapped[str] = mapped_column(String(64), primary_key=True, default="default")
    max_active_universes: Mapped[int] = mapped_column(Integer, nullable=False)
    max_total_branches: Mapped[int] = mapped_column(Integer, nullable=False)
    max_depth: Mapped[int] = mapped_column(Integer, nullable=False)
    max_branches_per_tick: Mapped[int] = mapped_column(Integer, nullable=False)
    branch_cooldown_ticks: Mapped[int] = mapped_column(Integer, nullable=False)
    min_divergence_score: Mapped[float] = mapped_column(Float, nullable=False)
    auto_prune_low_value: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class GlobalSettingModel(Base, TimestampMixin):
    """Singleton global settings (one row, PK = 'default')."""

    __tablename__ = "settings_global"

    setting_id: Mapped[str] = mapped_column(String(64), primary_key=True, default="default")
    default_tick_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    default_max_ticks: Mapped[int] = mapped_column(Integer, nullable=False, default=48)
    default_max_schedule_horizon_ticks: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    log_level: Mapped[str] = mapped_column(String(16), nullable=False, default="INFO")
    display_timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    theme: Mapped[str] = mapped_column(String(16), nullable=False, default="system")
    enable_oasis_adapter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    branching_defaults: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Full payload for all fields including representation_mode_thresholds etc.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
