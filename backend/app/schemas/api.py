"""Central Pydantic v2 request/response schemas for the B5-A/B5-B REST API.

Covers:
  - Runs
  - Universes
  - Multiverse
  - Settings — B5-B
  - Jobs — B5-B
  - Logs — B5-B

Import-free of backend.app.models (models import schemas; never the reverse).
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

NonEmptyStr = Annotated[str, Field(min_length=1)]


def _validate_http_url(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.startswith(("http://", "https://")):
        raise ValueError("URL must start with http:// or https://")
    return value

# ---------------------------------------------------------------------------
# ── Runs ──────────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(..., min_length=1, max_length=512)
    scenario_text: str = Field(..., min_length=1)
    time_horizon_label: str = Field(default="6 months")
    tick_duration_minutes: int = Field(default=1440, gt=0)
    max_ticks: int = Field(default=180, gt=0)
    max_schedule_horizon_ticks: int = Field(default=10, gt=0)
    uploaded_doc_ids: list[str] = Field(default_factory=list)
    provider_snapshot_id: str | None = None


class CreateRunResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    root_universe_id: str
    status: str
    job_id: str | None = None
    enqueued: bool = False
    degraded: bool = False
    error: str | None = None


class RunListItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str
    display_name: str
    status: str
    scenario_text: str
    time_horizon_label: str
    tick_duration_minutes: int
    max_ticks: int
    created_at: datetime
    updated_at: datetime
    favorite: bool = False
    archived: bool = False
    root_universe_id: str
    active_universe_count: int = 0
    total_universe_count: int = 0


class RunListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RunListItem]
    total: int
    limit: int
    offset: int


class RunDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str
    display_name: str
    description: str | None = None
    status: str
    scenario_text: str
    time_horizon_label: str
    tick_duration_minutes: int
    max_ticks: int
    max_schedule_horizon_ticks: int
    root_universe_id: str
    run_folder_path: str | None = None
    source_of_truth_version: str | None = None
    provider_snapshot_id: str | None = None
    created_at: datetime
    updated_at: datetime
    active_universe_count: int = 0
    total_universe_count: int = 0
    latest_metrics: dict[str, Any] = Field(default_factory=dict)
    safe_edit_metadata: dict[str, Any] = Field(default_factory=dict)


class PatchRunRequest(BaseModel):
    """Only safe-edit fields are accepted; anything else raises 422."""

    model_config = ConfigDict(extra="forbid")

    display_name: NonEmptyStr | None = None
    description: str | None = None
    tags: list[str] | None = None
    favorite: bool | None = None
    archived: bool | None = None


class RunResultsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str
    status: str
    generated_at: datetime | None = None
    provider: str | None = None
    model_used: str | None = None
    summary: str | None = None
    classifications: dict[str, Any] = Field(default_factory=dict)
    branch_clusters: list[dict[str, Any]] = Field(default_factory=list)
    universe_outcomes: list[dict[str, Any]] = Field(default_factory=list)
    timeline_highlights: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifact_path: str | None = None
    error: str | None = None
    job_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RunResultsRegenerateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    job_id: str
    status: str
    enqueued: bool = True


# ---------------------------------------------------------------------------
# ── Universes ─────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


class UniverseDetail(BaseModel):
    model_config = ConfigDict(extra="ignore")

    universe_id: str
    big_bang_id: str
    parent_universe_id: str | None = None
    child_universe_ids: list[str] = Field(default_factory=list)
    branch_from_tick: int = 0
    branch_depth: int = 0
    lineage_path: list[str] = Field(default_factory=list)
    status: str
    branch_reason: str = ""
    current_tick: int = 0
    latest_metrics: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    frozen_at: datetime | None = None
    killed_at: datetime | None = None
    completed_at: datetime | None = None
    # Derived / aggregated
    active_cohort_count: int = 0
    branch_summary: dict[str, Any] = Field(default_factory=dict)


class BranchPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # BranchDelta discriminated union payload; API handlers validate it through
    # backend.app.schemas.branching before previewing or committing a branch.
    delta: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class BranchPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Branch policy result from the database-backed evaluator.
    approved: bool = True
    downgraded: bool = False
    rejection_reason: str | None = None
    policy_checks: list[dict[str, Any]] = Field(default_factory=list)
    note: str = "Branch policy evaluated."


class BranchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delta: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class BranchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_universe_id: str
    job_id: str
    note: str = "Branch committed."


class StepRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Optional override — defaults to current_tick + 1 on the server.
    tick: int | None = Field(default=None, ge=0)


class LineageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lineage_path: list[str]
    parent: str | None
    depth: int
    branch_from_tick: int | None


class DescendantsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Recursive nested tree node.
    universe_id: str
    status: str
    depth: int
    current_tick: int
    children: list[DescendantsResponse] = Field(default_factory=list)


DescendantsResponse.model_rebuild()


class TickArtifactResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    universe_id: str
    tick: int
    parsed_decisions: list[dict[str, Any]] = Field(default_factory=list)
    social_posts: list[dict[str, Any]] = Field(default_factory=list)
    state_after: dict[str, Any] = Field(default_factory=dict)
    god_decision: dict[str, Any] | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    prompt_summary: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    emotion_trends: list[dict[str, float]] = Field(default_factory=list)


class TickTraceActor(BaseModel):
    model_config = ConfigDict(extra="ignore")

    actor_id: str
    actor_kind: str
    call_id: str | None = None
    provider: str | None = None
    model_used: str | None = None
    job_type: str | None = None
    prompt_packet: dict[str, Any] | None = None
    visible_feed: list[dict[str, Any]] = Field(default_factory=list)
    visible_events: list[dict[str, Any]] = Field(default_factory=list)
    retrieved_memory: dict[str, Any] | None = None
    raw_response: dict[str, Any] | str | None = None
    parsed_json: dict[str, Any] | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    rationale: dict[str, Any] | str | None = None
    self_ratings: dict[str, Any] = Field(default_factory=dict)
    state_before: dict[str, Any] | None = None
    state_after: dict[str, Any] | None = None
    state_delta: dict[str, Any] = Field(default_factory=dict)


class TickTraceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    universe_id: str
    tick: int
    include_raw: bool = False
    actors: list[TickTraceActor] = Field(default_factory=list)
    state_before: dict[str, Any] = Field(default_factory=dict)
    state_after: dict[str, Any] = Field(default_factory=dict)
    god_decision: dict[str, Any] | None = None
    redactions_applied: bool = True
    missing_artifacts: list[str] = Field(default_factory=list)


class ForceDeviationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tick: int = Field(..., ge=0)
    mode: str = Field(pattern="^(god_prompt|structured_delta)$")
    prompt: str | None = None
    delta: dict[str, Any] | None = None
    reason: str = ""
    auto_start: bool = True


class ForceDeviationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    parent_universe_id: str
    child_universe_id: str | None = None
    tick: int
    mode: str
    job_id: str
    status: str
    enqueued: bool = False
    generated_delta: dict[str, Any] | None = None
    audit_artifact_path: str | None = None
    note: str = "Forced deviation committed."


# ---------------------------------------------------------------------------
# ── Multiverse ────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


class MultiverseTreeNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    universe_id: str
    parent_universe_id: str | None = None
    depth: int
    branch_from_tick: int | None = None
    status: str
    current_tick: int
    latest_metrics: dict[str, Any] = Field(default_factory=dict)
    branch_reason: str = ""
    branch_delta: dict[str, Any] = Field(default_factory=dict)
    lineage_path: list[str] = Field(default_factory=list)
    descendant_count: int = 0
    created_at: datetime | None = None


class MultiverseEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str
    target: str
    branch_tick: int | None = None


class MultiverseTreeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    big_bang_id: str
    max_ticks: int = 0
    nodes: list[MultiverseTreeNode]
    edges: list[MultiverseEdge]


class MultiverseDagResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    big_bang_id: str
    nodes: list[MultiverseTreeNode]
    edges: list[MultiverseEdge]


class MultiverseMetricsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    big_bang_id: str
    active_universes: int
    total_branches: int
    max_depth: int
    candidate_branches: int
    branch_budget_pct: float = 0.0
    branch_budget_used: int = 0
    branch_budget_limit: int = 0
    active_branches_per_tick: float = 0.0


class PruneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_value: float = Field(default=0.1, ge=0.0, le=1.0)
    dry_run: bool = True


class PruneResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dry_run: bool
    pruned_universe_ids: list[str]
    pruned_count: int


class FocusBranchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    universe_id: NonEmptyStr


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    universe_ids: list[NonEmptyStr] = Field(..., min_length=2)
    aspect: NonEmptyStr = "metrics"


class CompareResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    aspect: str
    comparison: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# ── Source-of-Truth bundle response ─────────────────────────────────────────
# ---------------------------------------------------------------------------


class SoTBundleResponse(BaseModel):
    """Source-of-truth bundle response shape."""

    model_config = ConfigDict(extra="forbid")

    version: str
    emotions: dict[str, Any]
    behavior_axes: dict[str, Any]
    ideology_axes: dict[str, Any]
    expression_scale: dict[str, Any]
    issue_stance_axes: dict[str, Any]
    event_types: dict[str, Any]
    social_action_tools: dict[str, Any]
    channel_types: dict[str, Any]
    actor_types: dict[str, Any]
    sociology_parameters: dict[str, Any]


# ---------------------------------------------------------------------------
# ── Settings (B5-B) ───────────────────────────────────────────────────
# ---------------------------------------------------------------------------


class SettingsResponse(BaseModel):
    setting_id: str
    default_tick_duration_minutes: int
    default_max_ticks: int
    default_max_schedule_horizon_ticks: int
    log_level: str
    display_timezone: str
    theme: str
    enable_oasis_adapter: bool
    branching_defaults: dict[str, Any]
    payload: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PatchSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    default_tick_duration_minutes: int | None = Field(default=None, gt=0)
    default_max_ticks: int | None = Field(default=None, gt=0)
    default_max_schedule_horizon_ticks: int | None = Field(default=None, gt=0)
    log_level: NonEmptyStr | None = None
    display_timezone: NonEmptyStr | None = None
    theme: Literal["light", "dark", "system"] | None = None
    enable_oasis_adapter: bool | None = None
    branching_defaults: dict[str, Any] | None = None
    payload: dict[str, Any] | None = None


class ProviderSettingResponse(BaseModel):
    provider: str
    base_url: str
    api_key_env: str
    default_model: str
    fallback_model: NonEmptyStr | None = None
    json_mode_required: bool
    tool_calling_enabled: bool
    enabled: bool
    extra_headers: dict[str, Any]
    payload: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProvidersResponse(BaseModel):
    providers: list[ProviderSettingResponse]


class ProviderSettingIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: str = Field(..., min_length=1)
    base_url: str = Field(..., min_length=1)
    api_key_env: str = Field(..., min_length=1)
    default_model: str = Field(..., min_length=1)
    fallback_model: NonEmptyStr | None = None
    json_mode_required: bool = True
    tool_calling_enabled: bool = True
    enabled: bool = True
    extra_headers: dict[str, StrictStr] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)


class PatchProvidersRequest(BaseModel):
    providers: list[ProviderSettingIn]


class ProviderCatalogEntry(BaseModel):
    provider: str
    api_shape: str
    source: str
    supported: bool = True
    enabled: bool
    configured: bool = False
    base_url: str | None = None
    api_key_env: str | None = None
    default_model: str | None = None
    fallback_model: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RouteCatalogEntry(BaseModel):
    route: str
    route_kind: str = "audited_llm"
    label: str
    description: str
    fallback_model_setting: str
    direct_override: bool = True


class RoutingEntryResponse(BaseModel):
    job_type: str
    preferred_provider: str
    preferred_model: str
    fallback_provider: NonEmptyStr | None = None
    fallback_model: NonEmptyStr | None = None
    temperature: float
    top_p: float
    max_tokens: int
    max_concurrency: int
    requests_per_minute: int
    tokens_per_minute: int
    timeout_seconds: int
    retry_policy: str
    daily_budget_usd: float | None = None
    payload: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EffectiveRoutingEntry(BaseModel):
    route: str
    route_kind: str = "audited_llm"
    job_type: str
    matched_route: str | None = None
    preferred_provider: str
    preferred_model: str
    fallback_provider: NonEmptyStr | None = None
    fallback_model: NonEmptyStr | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    max_concurrency: int | None = None
    requests_per_minute: int | None = None
    tokens_per_minute: int | None = None
    timeout_seconds: int | None = None
    retry_policy: str | None = None
    daily_budget_usd: float | None = None
    source: str
    payload: dict[str, Any] = Field(default_factory=dict)


class RoutingResponse(BaseModel):
    entries: list[RoutingEntryResponse]
    effective_entries: list[EffectiveRoutingEntry] = Field(default_factory=list)
    known_routes: list[RouteCatalogEntry] = Field(default_factory=list)


class RoutingEntryIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    job_type: str
    preferred_provider: str = Field(..., min_length=1)
    preferred_model: str = Field(..., min_length=1)
    fallback_provider: NonEmptyStr | None = None
    fallback_model: NonEmptyStr | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    max_tokens: int = Field(default=4096, gt=0)
    max_concurrency: int = Field(default=4, ge=1)
    requests_per_minute: int = Field(default=60, ge=1)
    tokens_per_minute: int = Field(default=100000, ge=1)
    timeout_seconds: int = Field(default=120, gt=0)
    retry_policy: Literal["exponential_backoff", "linear", "none"] = "exponential_backoff"
    daily_budget_usd: float | None = Field(default=None, ge=0.0)
    payload: dict[str, Any] = Field(default_factory=dict)


class PatchRoutingRequest(BaseModel):
    entries: list[RoutingEntryIn]


class RateLimitResponse(BaseModel):
    provider: NonEmptyStr
    enabled: bool
    rpm_limit: int
    tpm_limit: int
    max_concurrency: int
    burst_multiplier: float
    retry_policy: str
    jitter: bool
    daily_budget_usd: float | None = None
    branch_reserved_capacity_pct: float
    healthcheck_enabled: bool
    payload: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RateLimitsResponse(BaseModel):
    rate_limits: list[RateLimitResponse]


class LLMConfigResponse(BaseModel):
    runtime_defaults: dict[str, Any]
    provider_catalog: list[ProviderCatalogEntry]
    providers: list[ProviderSettingResponse]
    model_routing: list[RoutingEntryResponse]
    effective_model_routing: list[EffectiveRoutingEntry]
    known_routes: list[RouteCatalogEntry]
    rate_limits: list[RateLimitResponse]
    effective_rate_limits: list[dict[str, Any]]
    api: dict[str, str]


class RateLimitIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    provider: NonEmptyStr
    enabled: bool = True
    rpm_limit: int = Field(..., ge=1)
    tpm_limit: int = Field(..., ge=1)
    max_concurrency: int = Field(..., ge=1)
    burst_multiplier: float = Field(default=1.2, ge=1.0)
    retry_policy: Literal["exponential_backoff", "linear", "none"] = "exponential_backoff"
    jitter: bool = True
    daily_budget_usd: float | None = Field(default=None, ge=0.0)
    branch_reserved_capacity_pct: float = Field(default=20.0, ge=0.0, le=100.0)
    healthcheck_enabled: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)


class PatchRateLimitsRequest(BaseModel):
    rate_limits: list[RateLimitIn]


class BranchPolicyResponse(BaseModel):
    policy_id: str
    max_active_universes: int
    max_total_branches: int
    max_depth: int
    max_branches_per_tick: int
    branch_cooldown_ticks: int
    min_divergence_score: float
    auto_prune_low_value: bool
    payload: dict[str, Any]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PatchBranchPolicyRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    max_active_universes: int | None = Field(default=None, ge=1, le=10_000)
    max_total_branches: int | None = Field(default=None, ge=1, le=100_000)
    max_depth: int | None = Field(default=None, ge=1, le=50)
    max_branches_per_tick: int | None = Field(default=None, ge=1, le=100)
    branch_cooldown_ticks: int | None = Field(default=None, ge=0)
    min_divergence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    auto_prune_low_value: bool | None = None
    payload: dict[str, Any] | None = None


class TestProviderRequest(BaseModel):
    provider: NonEmptyStr
    model: NonEmptyStr | None = None


class TestProviderResponse(BaseModel):
    ok: bool
    latency_ms: int | None = None
    provider: str
    model: str | None = None
    error: str | None = None
    details: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# ── Jobs (B5-B) ───────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


class QueueInfo(BaseModel):
    name: str
    active_task_count: int
    reserved_count: int
    scheduled_count: int
    paused: bool = False


class QueuesResponse(BaseModel):
    queues: list[QueueInfo]
    degraded: bool = False
    error: str | None = None


class WorkerInfo(BaseModel):
    hostname: str
    pool: str | None = None
    processed: int | None = None
    active: int | None = None


class WorkersResponse(BaseModel):
    workers: list[WorkerInfo]
    degraded: bool = False
    error: str | None = None


class JobInfo(BaseModel):
    job_id: str
    job_type: str
    priority: str
    run_id: str
    universe_id: str | None = None
    tick: int | None = None
    attempt_number: int
    status: str
    idempotency_key: str
    payload: dict[str, Any]
    enqueued_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    result_summary: dict[str, Any] | None = None
    artifact_path: str | None = None
    created_at: datetime | None = None


class JobsListResponse(BaseModel):
    jobs: list[JobInfo]
    total: int
    limit: int
    offset: int


class RetryRequest(BaseModel):
    queue: NonEmptyStr | None = None


class RetryResponse(BaseModel):
    new_task_id: str
    job_id: str
    attempt_number: int


class CancelResponse(BaseModel):
    job_id: str
    status: str


class QueuePauseResponse(BaseModel):
    queue: str
    paused: bool


# ---------------------------------------------------------------------------
# ── Logs (B5-B) ───────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


class RequestLogItem(BaseModel):
    call_id: str
    provider: str
    model_used: str
    job_type: str
    run_id: str
    universe_id: str | None = None
    tick: int | None = None
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float | None = None
    latency_ms: int
    status: str
    error: str | None = None
    repaired_once: bool
    created_at: datetime


class WebhookLogItem(BaseModel):
    id: str
    run_id: str | None = None
    event_type: str
    target_url: str
    status: str
    attempts: int
    last_delivered_at: datetime | None = None
    error: str | None = None
    created_at: datetime


class ErrorLogItem(BaseModel):
    source: str  # "job" | "llm_call"
    id: str
    job_type: str | None = None
    provider: str | None = None
    run_id: str | None = None
    status: str
    error: str | None = None
    created_at: datetime | None = None


class AuditLogItem(BaseModel):
    id: str
    actor: str | None = None
    action: str
    resource: str
    timestamp: datetime
    details: dict[str, Any]


class TraceResponse(BaseModel):
    trace_id: str
    llm_calls: list[RequestLogItem]
    jobs: list[JobInfo]


# ---------------------------------------------------------------------------
# ── Webhooks (B5-B) ─────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------


class WebhookTestRequest(BaseModel):
    url: NonEmptyStr
    secret: NonEmptyStr
    payload: dict[str, Any] = Field(default_factory=dict)
    event_type: NonEmptyStr = "worldfork.test"

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _validate_http_url(value) or value


class WebhookTestResponse(BaseModel):
    ok: bool
    status_code: int | None = None
    latency_ms: int | None = None
    attempts: int | None = None
    delivered_at: str | None = None
    error: str | None = None


class WebhookReplayRequest(BaseModel):
    event_id: NonEmptyStr
    target_url: NonEmptyStr | None = None

    @field_validator("target_url")
    @classmethod
    def validate_target_url(cls, value: str | None) -> str | None:
        return _validate_http_url(value)
