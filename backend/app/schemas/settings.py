"""
Settings schemas: ProviderConfig, ModelRoutingEntry, RateLimitConfig,
GlobalSettings.
Import-free of backend.app.models.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from backend.app.schemas.jobs import ModelRoutingJobType

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

# ---------------------------------------------------------------------------
# ProviderConfig
# ---------------------------------------------------------------------------

class ProviderConfig(BaseModel):
    """Configuration for one LLM provider."""

    model_config = ConfigDict(extra="forbid")

    provider: NonEmptyStr
    base_url: NonEmptyStr
    api_key_env: NonEmptyStr
    default_model: NonEmptyStr
    fallback_model: NonEmptyStr | None = None
    json_mode_required: bool = True
    tool_calling_enabled: bool = True
    enabled: bool = False
    extra_headers: dict[NonEmptyStr, NonEmptyStr] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# ModelRoutingEntry
# ---------------------------------------------------------------------------

class ModelRoutingEntry(BaseModel):
    """Per-job-type model routing policy."""

    model_config = ConfigDict(extra="forbid")

    job_type: ModelRoutingJobType
    preferred_provider: NonEmptyStr
    preferred_model: NonEmptyStr
    fallback_provider: NonEmptyStr | None = None
    fallback_model: NonEmptyStr | None = None
    temperature: float = Field(..., ge=0.0, le=2.0)
    top_p: float = Field(..., ge=0.0, le=1.0)
    max_tokens: int = Field(..., gt=0)
    max_concurrency: int = Field(..., ge=1)
    requests_per_minute: int = Field(..., ge=1)
    tokens_per_minute: int = Field(..., ge=1)
    timeout_seconds: int = Field(..., gt=0)
    retry_policy: Literal["exponential_backoff", "linear", "none"]
    daily_budget_usd: float | None = Field(default=None, ge=0.0)


# ---------------------------------------------------------------------------
# RateLimitConfig
# ---------------------------------------------------------------------------

class RateLimitConfig(BaseModel):
    """Per-provider rate limit configuration."""

    model_config = ConfigDict(extra="forbid")

    provider: NonEmptyStr
    enabled: bool = True
    rpm_limit: int = Field(..., ge=1)
    tpm_limit: int = Field(..., ge=1)
    max_concurrency: int = Field(..., ge=1)
    burst_multiplier: float = Field(..., ge=1.0)
    retry_policy: Literal["exponential_backoff", "linear", "none"]
    jitter: bool = True
    daily_budget_usd: float | None = Field(default=None, ge=0.0)
    branch_reserved_capacity_pct: float = Field(..., ge=0.0, le=100.0)
    healthcheck_enabled: bool = True


# ---------------------------------------------------------------------------
# GlobalSettings
# ---------------------------------------------------------------------------

class GlobalSettings(BaseModel):
    """
    Catch-all global preferences.
    Uses extra="ignore" because new keys may be added over time.
    """

    model_config = ConfigDict(extra="ignore")

    default_tick_duration_minutes: int = Field(default=120, gt=0)
    default_max_ticks: int = Field(default=48, gt=0)
    default_max_schedule_horizon_ticks: int = Field(default=5, gt=0)
    default_representation_mode_thresholds: dict[NonEmptyStr, Any] = Field(
        default_factory=lambda: {
            "micro": [2, 25],
            "small": [25, 250],
            "population": [250, 5000],
            "mass": [5000, None],
        }
    )
    enable_oasis_adapter: bool = False
    log_level: NonEmptyStr = "INFO"
    display_timezone: NonEmptyStr = "UTC"
    theme: Literal["light", "dark", "system"] = "system"
    ui_autoplay_interval_ms: int = Field(default=3000, ge=500)
    run_folder_root: NonEmptyStr = "runs"
