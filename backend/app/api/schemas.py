from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class BigBangCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=240)
    description: str | None = None
    scenario_text: str | None = None
    scenario_input: dict[str, Any] = Field(default_factory=dict)
    simulation_config: dict[str, Any] = Field(default_factory=dict)
    llm_model_config: dict[str, Any] = Field(default_factory=dict, alias="model_config")
    branch_policy: dict[str, Any] = Field(default_factory=dict)
    actors: list[dict[str, Any]] = Field(default_factory=list)
    cohorts: list[dict[str, Any]] = Field(default_factory=list)
    heroes: list[dict[str, Any]] = Field(default_factory=list)
    use_initializer_agent: bool = True
    initializer_prompt: str | None = None


class BigBangPatch(BaseModel):
    name: str | None = Field(default=None, max_length=240)
    description: str | None = None
    status: str | None = None


class BigBangOut(ORMModel):
    id: UUID
    name: str
    description: str | None
    scenario_input: dict[str, Any]
    status: str
    current_config_version: int
    source_snapshot_id: UUID | None
    created_at: datetime
    updated_at: datetime

    @field_validator("scenario_input", mode="before")
    @classmethod
    def sanitize_scenario_input(cls, value):
        return sanitize_public_payload(value)


class MultiverseOut(ORMModel):
    id: UUID
    big_bang_id: UUID
    parent_multiverse_id: UUID | None
    fork_tick_index: int | None
    ui_label: str
    version: int = 1
    depth: int
    status: str
    branch_reason: str | None
    branch_probability: float = 1.0
    path_probability: float = 1.0
    state: dict[str, Any]
    report_status: str
    ended_at: datetime | None = None
    continued_from_report_version_id: UUID | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("state", mode="before")
    @classmethod
    def sanitize_state(cls, value):
        return sanitize_public_payload(value)


class TickSnapshotOut(ORMModel):
    id: UUID
    big_bang_id: UUID
    multiverse_id: UUID
    tick_index: int
    ui_label: str
    status: str
    provisional_bundle: dict[str, Any]
    final_bundle: dict[str, Any]
    summary: str | None
    artifact_id: UUID | None
    created_at: datetime
    updated_at: datetime

    @field_validator("provisional_bundle", "final_bundle", mode="before")
    @classmethod
    def sanitize_bundle(cls, value):
        return sanitize_public_payload(value)


class GraphSnapshotOut(ORMModel):
    id: UUID
    big_bang_id: UUID
    multiverse_id: UUID
    tick_index: int
    layer: str
    graph: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class GraphEdgeOut(ORMModel):
    id: UUID
    big_bang_id: UUID
    multiverse_id: UUID
    tick_index: int
    source_actor_id: UUID | None
    target_actor_id: UUID | None
    layer: str
    weight: float
    payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SociologySignalOut(ORMModel):
    id: UUID
    big_bang_id: UUID
    multiverse_id: UUID
    tick_index: int
    actor_id: UUID | None
    model: str
    signal: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class SociologyPromptInfluenceOut(ORMModel):
    id: UUID
    big_bang_id: UUID
    multiverse_id: UUID
    actor_id: UUID | None
    tick_index: int
    applies_to_tick_index: int
    influence: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ToolCallOut(ORMModel):
    id: UUID
    big_bang_id: UUID
    multiverse_id: UUID | None
    tick_snapshot_id: UUID | None
    god_review_id: UUID | None
    tool_name: str
    arguments: dict[str, Any]
    status: str
    result: dict[str, Any]
    error: str | None
    idempotency_key: str
    created_at: datetime
    updated_at: datetime


class GodAgentReviewOut(ORMModel):
    id: UUID
    big_bang_id: UUID
    multiverse_id: UUID
    tick_snapshot_id: UUID | None
    decision: str
    rationale: str
    confidence: float
    input_summary: dict[str, Any]
    output: dict[str, Any]
    artifact_id: UUID | None
    created_at: datetime
    updated_at: datetime


class GodRegenerateSummaryOut(BaseModel):
    god_review_id: UUID
    status: str
    message: str


class ReportOut(ORMModel):
    id: UUID
    big_bang_id: UUID
    multiverse_id: UUID | None
    report_type: str
    status: str
    current_version: int
    created_at: datetime
    updated_at: datetime


class ReportVersionOut(ORMModel):
    id: UUID
    report_id: UUID
    version: int
    title: str
    summary: str | None
    source_multiverse_version: int | None = None
    source_big_bang_config_version: int | None = None
    source_tick_snapshot_id: UUID | None = None
    source_tick_index: int | None = None
    source_multiverse_ids: list[Any] | None = None
    content: dict[str, Any] | None = None
    generation_metadata: dict[str, Any] | None = None
    model: str | None = None
    markdown_artifact_id: UUID | None
    pdf_artifact_id: UUID | None
    created_at: datetime
    updated_at: datetime


class ReportVersionPatch(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    summary: str | None = None
    content: dict[str, Any] | None = None
    generation_metadata: dict[str, Any] | None = None


class EndpointLedgerEntryOut(ORMModel):
    id: UUID
    ledger_version_id: UUID
    endpoint_key: str
    label: str
    description: str | None
    status: str
    realized: bool | None = None
    realization_criteria: list[Any] = Field(default_factory=list)
    authority_refs: list[Any]
    evidence_refs: list[Any]
    negative_evidence_refs: list[Any] = Field(default_factory=list)
    blockers: list[Any]
    status_basis: str | None = None
    contradiction_notes: str | None
    rationale: str | None
    last_observed_tick_index: int | None
    meta: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class EndpointLedgerVersionOut(ORMModel):
    id: UUID
    big_bang_id: UUID
    multiverse_id: UUID | None
    scope: str
    version: int
    status: str
    source_type: str
    source_tick_snapshot_id: UUID | None
    source_report_version_id: UUID | None
    parent_ledger_version_id: UUID | None
    created_by: str
    summary: str | None
    model: str | None
    llm_call_id: UUID | None
    payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class EndpointLedgerDetailOut(BaseModel):
    ledger: EndpointLedgerVersionOut
    entries: list[EndpointLedgerEntryOut]


class EndpointLedgerEvaluateRequest(BaseModel):
    idempotency_key: str | None = None
    run_inline: bool = False
    candidate_endpoint: dict[str, Any] | None = None


class EndpointLedgerEvaluateOut(BaseModel):
    job_id: UUID | None = None
    status: str
    ledger_version_id: UUID | None = None


class TimelineAdjudicationEntryOut(ORMModel):
    id: UUID
    adjudication_version_id: UUID
    big_bang_id: UUID
    multiverse_id: UUID
    ui_label: str | None = None
    viability_status: str
    include_in_final: bool
    prune_reason: str | None = None
    original_path_probability: float
    effective_path_probability: float
    mass_disposition: str
    endpoint_key: str | None = None
    endpoint_status: str | None = None
    evidence_summary: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class TimelineAdjudicationVersionOut(ORMModel):
    id: UUID
    big_bang_id: UUID
    version: int
    status: str
    source_type: str
    source_report_version_id: UUID | None = None
    parent_adjudication_version_id: UUID | None = None
    created_by: str
    summary: str | None = None
    payload: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class TimelineAdjudicationDetailOut(BaseModel):
    adjudication: TimelineAdjudicationVersionOut
    entries: list[TimelineAdjudicationEntryOut]


class EndpointPathMassRow(BaseModel):
    endpoint_key: str | None = None
    label: str | None = None
    status: str | None = None
    realized: bool | None = None
    path_mass: float = 0.0
    status_path_masses: dict[str, float] = Field(default_factory=dict)


class EndpointPathMassPlotOut(BaseModel):
    big_bang_id: UUID
    ledger_version_id: UUID | None = None
    aggregation: str
    path_probability_mass: float = 0.0
    excluded_path_probability_mass: float = 0.0
    endpoint_path_mass_distribution: list[EndpointPathMassRow] = Field(default_factory=list)
    plot_distribution: dict[str, Any] = Field(default_factory=dict)
    path_probability_distribution: list[dict[str, Any]] = Field(default_factory=list)


class TimelineAdjudicationRequest(BaseModel):
    source_type: str = "posthoc_adjudication"
    summary: str | None = None


class ReportRenderRequest(BaseModel):
    format: str = Field(default="markdown", pattern="^(markdown|pdf)$")
    force: bool = False


class ReportRenderOut(BaseModel):
    report_version_id: UUID
    format: str
    artifact_id: UUID | None = None
    content_type: str | None
    path: str | None = None
    persisted: bool = False
    render_mode: str = "ephemeral"


class RunUntilCompleteOut(BaseModel):
    ticks_run: int
    multiverse_count: int
    report_versions: list[str]
    final_report_version_id: str


class MultiverseLineageEdgeOut(ORMModel):
    id: UUID
    big_bang_id: UUID
    parent_multiverse_id: UUID
    child_multiverse_id: UUID
    fork_tick_index: int
    reason: str | None
    branch_probability: float = 1.0
    parent_path_probability: float = 1.0
    child_path_probability: float = 1.0
    probability_basis: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class TickLineageRefOut(ORMModel):
    id: UUID
    child_multiverse_id: UUID
    source_multiverse_id: UUID
    source_tick_snapshot_id: UUID
    inherited_tick_index: int
    inherited_ui_label: str
    created_at: datetime
    updated_at: datetime


class MultiverseLineageOut(BaseModel):
    multiverse: MultiverseOut
    edges: list[MultiverseLineageEdgeOut]
    inherited_ticks: list[TickLineageRefOut]


class SimulateTickRequest(BaseModel):
    idempotency_key: str | None = None
    force: bool = False


class MultiverseContinueRequest(BaseModel):
    max_ticks: int = Field(gt=0)
    reason: str | None = None
    continued_from_report_version_id: UUID | None = None


class SimulateTicksRequest(BaseModel):
    count: int = Field(default=1, ge=1, le=100)


class RunUntilCompleteRequest(BaseModel):
    max_total_ticks: int = Field(default=24, ge=1, le=500)


class ToolCallRequest(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class ReportRequest(BaseModel):
    title: str | None = None
    summary: str | None = None


class JobCreate(BaseModel):
    job_type: str
    big_bang_id: UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class GenericOut(BaseModel):
    data: Any


class WorkspaceState(BaseModel):
    big_bang: BigBangOut
    multiverses: list[MultiverseOut]
    latest_ticks: list[TickSnapshotOut]
    activity: list[dict[str, Any]]


RAW_TEXT_KEYS = {"scenario_text", "prompt", "premise", "raw_text", "source_text", "plain_text"}
RAW_CORPUS_ID_KEYS = {"raw_text_artifact_id", "simulation_brief_artifact_id"}


def sanitize_public_payload(value):
    if isinstance(value, dict):
        return _sanitize_public_dict(value)
    if isinstance(value, list):
        return [sanitize_public_payload(item) for item in value]
    return value


def _sanitize_public_dict(value: dict) -> dict:
    sanitized = {}
    for key, item in value.items():
        key_text = str(key)
        normalized = key_text.lower()
        if normalized in RAW_CORPUS_ID_KEYS:
            continue
        if normalized in RAW_TEXT_KEYS or _is_absolute_path_field(normalized, item):
            sanitized[f"{key_text}_present"] = bool(item)
            if isinstance(item, str):
                sanitized[f"{key_text}_char_count"] = len(item)
            continue
        if normalized == "plain_text_corpus" and isinstance(item, dict):
            sanitized[key] = _sanitize_plain_text_corpus(item)
            continue
        if normalized == "simulation_brief" and isinstance(item, dict):
            sanitized[key] = _sanitize_simulation_brief(item)
            continue
        sanitized[key] = sanitize_public_payload(item)
    return sanitized


def _sanitize_plain_text_corpus(value: dict) -> dict:
    sanitized = _sanitize_public_dict(value)
    for collection_key in ("chunk_artifacts", "chunk_summaries"):
        if isinstance(sanitized.get(collection_key), list):
            sanitized[collection_key] = [
                {key: val for key, val in item.items() if key != "artifact_id"}
                if isinstance(item, dict)
                else item
                for item in sanitized[collection_key]
            ]
    return sanitized


def _sanitize_simulation_brief(value: dict) -> dict:
    sanitized = _sanitize_public_dict(value)
    if "text" in sanitized:
        text = sanitized.pop("text")
        sanitized["text_present"] = bool(text)
        if isinstance(text, str):
            sanitized["text_char_count"] = len(text)
    return sanitized


def _is_absolute_path_field(key: str, value) -> bool:
    return key in {"path", "artifact_path"} and isinstance(value, str) and value.startswith("/")
