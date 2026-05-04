"""
LLM-layer schemas: PromptPacket, ModelConfig, LLMResult, EmbeddingConfig,
EmbeddingResult, ProviderHealth, CohortDecisionOutput, HeroDecisionOutput,
GodReviewOutput.
Import-free of backend.app.models.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.branching import BranchDelta
from backend.app.schemas.common import Clock

# ---------------------------------------------------------------------------
# PromptPacket
# ---------------------------------------------------------------------------

class PromptPacket(BaseModel):
    """
    Full prompt context packet sent to an LLM agent.
    Uses extra="ignore" — callers may attach extra metadata keys.
    """

    model_config = ConfigDict(extra="ignore")

    system: str
    clock: Clock
    actor_id: str
    actor_kind: Literal["cohort", "hero", "god"]
    archetype: dict[str, Any] | None = None
    state: dict[str, Any] = Field(default_factory=dict)
    sot_excerpt: dict[str, Any] = Field(default_factory=dict)
    visible_feed: list[dict[str, Any]] = Field(default_factory=list)
    visible_events: list[dict[str, Any]] = Field(default_factory=list)
    own_queued_events: list[dict[str, Any]] = Field(default_factory=list)
    own_recent_actions: list[dict[str, Any]] = Field(default_factory=list)
    retrieved_memory: dict[str, Any] | None = None
    allowed_tools: list[str] = Field(default_factory=list)
    output_schema_id: str  # e.g. "cohort_decision_schema"
    temperature: float = Field(..., ge=0.0, le=2.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# ModelConfig
# ---------------------------------------------------------------------------

class ModelConfig(BaseModel):
    """Provider + model configuration for a single LLM call."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    fallback_model: str | None = None
    temperature: float = Field(..., ge=0.0, le=2.0)
    top_p: float = Field(..., ge=0.0, le=1.0)
    max_tokens: int = Field(..., gt=0)
    response_format: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    timeout_seconds: int = Field(..., gt=0)
    retry_policy: str


# ---------------------------------------------------------------------------
# LLMResult
# ---------------------------------------------------------------------------

class LLMResult(BaseModel):
    """
    Record of a completed LLM call — persisted to llm_calls table and ledger.
    Uses extra="ignore" because raw_response may carry arbitrary provider fields.
    """

    model_config = ConfigDict(extra="ignore")

    call_id: str
    provider: str
    model_used: str
    prompt_tokens: int = Field(..., ge=0)
    completion_tokens: int = Field(..., ge=0)
    total_tokens: int = Field(..., ge=0)
    cost_usd: float | None = None
    latency_ms: int = Field(..., ge=0)
    parsed_json: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    raw_response: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    repaired_once: bool = False


# ---------------------------------------------------------------------------
# EmbeddingConfig
# ---------------------------------------------------------------------------

class EmbeddingConfig(BaseModel):
    """Configuration for embedding requests."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    dimensions: int | None = None


# ---------------------------------------------------------------------------
# EmbeddingResult
# ---------------------------------------------------------------------------

class EmbeddingResult(BaseModel):
    """Result of an embedding batch request."""

    model_config = ConfigDict(extra="forbid")

    vectors: list[list[float]]
    model_used: str
    prompt_tokens: int = Field(..., ge=0)
    cost_usd: float | None = None


# ---------------------------------------------------------------------------
# ProviderHealth
# ---------------------------------------------------------------------------

class ProviderHealth(BaseModel):
    """Healthcheck result for a provider."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    ok: bool
    latency_ms: int | None = None
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# CohortDecisionOutput
# ---------------------------------------------------------------------------

class _DecisionRationale(BaseModel):
    """Structured rationale fragment from an LLM decision."""

    model_config = ConfigDict(extra="ignore")

    main_factors: list[str] = Field(default_factory=list)
    uncertainty: Literal["low", "medium", "high"]


class CohortDecisionOutput(BaseModel):
    """
    Parsed output contract for cohort agent decisions.
    Uses extra="ignore" to tolerate minor schema drift between prompt versions.
    """

    model_config = ConfigDict(extra="ignore")

    public_actions: list[dict[str, Any]] = Field(default_factory=list)
    event_actions: list[dict[str, Any]] = Field(default_factory=list)
    social_actions: list[dict[str, Any]] = Field(default_factory=list)
    self_ratings: dict[str, Any] = Field(default_factory=dict)
    split_merge_proposals: list[dict[str, Any]] = Field(default_factory=list)
    decision_rationale: _DecisionRationale


# ---------------------------------------------------------------------------
# HeroDecisionOutput
# ---------------------------------------------------------------------------

class HeroDecisionOutput(BaseModel):
    """
    Parsed output contract for hero agent decisions.
    Mirrors structure with extra="ignore" for version tolerance.
    """

    model_config = ConfigDict(extra="ignore")

    public_actions: list[dict[str, Any]] = Field(default_factory=list)
    event_actions: list[dict[str, Any]] = Field(default_factory=list)
    social_actions: list[dict[str, Any]] = Field(default_factory=list)
    self_ratings: dict[str, Any] = Field(default_factory=dict)
    decision_rationale: _DecisionRationale


# ---------------------------------------------------------------------------
# GodReviewOutput
# ---------------------------------------------------------------------------

class GodReviewOutput(BaseModel):
    """
    Parsed output contract for God-agent review decisions.
    Uses extra="ignore" to tolerate evolving prompt contract versions.
    """

    model_config = ConfigDict(extra="ignore")

    decision: Literal[
        "continue",
        "freeze",
        "kill",
        "spawn_candidate",
        "spawn_active",
        "complete_universe",
    ]
    branch_delta: BranchDelta | None = None
    marked_key_events: list[str] = Field(default_factory=list)
    tick_summary: str
    rationale: dict[str, Any] = Field(default_factory=dict)
