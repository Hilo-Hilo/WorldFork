"""
Unit tests for backend.app.schemas.
Covers all validator cases specified in the B1-C deliverables.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import get_args

import pytest
from pydantic import ValidationError

from app.api.agent import AgentModelPatch
from app.llm.routing import AUDITED_LLM_ROUTES
from app.api.schemas import (
    BigBangCreate,
    BigBangPatch,
    EndpointLedgerEvaluateRequest,
    JobCreate,
    MultiverseContinueRequest,
    ReportVersionPatch,
    ReportRequest,
    SimulateTickRequest,
    TimelineAdjudicationRequest,
    ToolCallRequest,
    sanitize_public_payload,
)
from backend.app.schemas import (
    BigBangRun,
    BranchNode,
    BranchDelta,
    BranchPolicy,
    BranchPolicyResult,
    CohortState,
    EmbeddingConfig,
    Event,
    GlobalSettings,
    HeroArchetype,
    HeroState,
    JobEnvelope,
    LLMResult,
    ModelConfig,
    ModelRoutingEntry,
    PopulationArchetype,
    ProviderConfig,
    ProviderHealth,
    RateLimitConfig,
    ChildSplitSpec,
    MergeProposal,
    SocialPost,
    SplitProposal,
    Universe,
)
from backend.app.schemas.jobs import AuditedLLMRouteType, JobStatus
from backend.app.schemas.api import (
    BranchPreviewRequest,
    BranchRequest,
    CompareRequest,
    CreateRunRequest,
    FocusBranchRequest,
    ForceDeviationRequest,
    PatchBranchPolicyRequest,
    PatchProvidersRequest,
    PatchRateLimitsRequest,
    PatchRunRequest,
    PatchRoutingRequest,
    PatchSettingsRequest,
    RetryRequest,
    TestProviderRequest as ProviderTestRequest,
    WebhookReplayRequest,
    WebhookTestRequest,
)
from backend.app.schemas.branching import (
    ActorStateOverrideDelta,
    CounterfactualEventRewriteDelta,
    HeroDecisionOverrideDelta,
    ParameterShiftDelta,
)


@pytest.mark.parametrize(
    "field_name",
    [
        "run_folder_path",
        "source_of_truth_snapshot_path",
        "prompt_packet_path",
        "response_path",
        "parsed_path",
        "output_path",
        "report_pdf_path",
        "workspace_path",
        "config_path",
        "database_path",
    ],
)
def test_public_payload_sanitizer_redacts_absolute_path_fields(field_name):
    value = f"/Users/example/worldfork/{field_name}.json"

    sanitized = sanitize_public_payload({field_name: value})

    assert sanitized == {
        f"{field_name}_present": True,
        f"{field_name}_char_count": len(value),
    }


@pytest.mark.parametrize(
    "field_name",
    [
        "run_folder_path",
        "source_of_truth_snapshot_path",
        "prompt_packet_path",
        "response_path",
        "parsed_path",
        "output_path",
        "report_pdf_path",
        "workspace_path",
        "config_path",
        "database_path",
    ],
)
def test_public_payload_sanitizer_redacts_windows_absolute_path_fields(field_name):
    value = "C:\\Users\\example\\worldfork\\" + field_name + ".json"

    sanitized = sanitize_public_payload({field_name: value})

    assert sanitized == {
        f"{field_name}_present": True,
        f"{field_name}_char_count": len(value),
    }


@pytest.mark.parametrize(
    "field_name",
    [
        "artifact_id",
        "llm_call_id",
        "markdown_artifact_id",
        "pdf_artifact_id",
        "audit_artifact_id",
        "prompt_packet_artifact_id",
        "response_artifact_id",
        "parsed_artifact_id",
        "source_snapshot_artifact_id",
        "simulation_brief_llm_call_id",
    ],
)
def test_public_payload_sanitizer_drops_internal_reference_ids(field_name):
    sanitized = sanitize_public_payload({field_name: "4f0774fe-b2de-45dc-9918-1b837089a777"})

    assert sanitized == {}


@pytest.mark.parametrize(
    "field_name",
    [
        "artifactId",
        "llmCallId",
        "markdownArtifactId",
        "pdfArtifactId",
        "auditArtifactId",
        "promptPacketArtifactId",
        "responseArtifactId",
        "parsedArtifactId",
        "sourceSnapshotArtifactId",
        "simulationBriefLlmCallId",
    ],
)
def test_public_payload_sanitizer_drops_mixed_style_internal_reference_ids(field_name):
    sanitized = sanitize_public_payload({field_name: "4f0774fe-b2de-45dc-9918-1b837089a777"})

    assert sanitized == {}


@pytest.mark.parametrize(
    "field_name",
    [
        "api_key",
        "apikey",
        "secret",
        "password",
        "token",
        "authorization",
        "bearer",
        "client_secret",
        "OPENROUTER_API_KEY",
        "refresh_token",
    ],
)
def test_public_payload_sanitizer_redacts_secret_like_fields(field_name):
    sanitized = sanitize_public_payload({field_name: "super-secret-value"})

    assert sanitized == {field_name: "[REDACTED]"}


@pytest.mark.parametrize(
    "field_name",
    [
        "clientSecret",
        "accessToken",
        "refreshToken",
        "idToken",
        "authToken",
        "bearerToken",
        "secretKey",
        "privateKey",
        "openrouterApiKey",
        "api-key",
    ],
)
def test_public_payload_sanitizer_redacts_mixed_style_secret_fields(field_name):
    sanitized = sanitize_public_payload({field_name: "super-secret-value"})

    assert sanitized == {field_name: "[REDACTED]"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_big_bang(**overrides) -> dict:
    base = dict(
        big_bang_id="bb_001",
        display_name="Test Run",
        created_at=_NOW,
        updated_at=_NOW,
        created_by_user_id=None,
        scenario_text="Metro Region gig worker dispute",
        input_file_ids=[],
        status="draft",
        time_horizon_label="6 months",
        tick_duration_minutes=120,
        max_ticks=48,
        max_schedule_horizon_ticks=5,
        source_of_truth_version="1.0.0",
        source_of_truth_snapshot_path="/runs/BB_001/sot",
        provider_snapshot_id="snap_001",
        root_universe_id="u_000",
        run_folder_path="/runs/BB_001",
        safe_edit_metadata={},
    )
    base.update(overrides)
    return base


def _make_universe(**overrides) -> dict:
    base = dict(
        universe_id="u_001",
        big_bang_id="bb_001",
        parent_universe_id="u_000",
        child_universe_ids=[],
        branch_from_tick=3,
        branch_depth=1,
        lineage_path=["u_000", "u_001"],
        status="active",
        branch_reason="test",
        branch_delta=None,
        current_tick=3,
        latest_metrics={},
        created_at=_NOW,
        frozen_at=None,
        killed_at=None,
        completed_at=None,
    )
    base.update(overrides)
    return base


def _make_population_archetype(**overrides) -> dict:
    base = dict(
        archetype_id="arch_001",
        label="Gig workers",
        description="Drivers and delivery workers in the metro region.",
        population_total=1000,
    )
    base.update(overrides)
    return base


def _make_cohort(**overrides) -> dict:
    base = dict(
        cohort_id="c_001",
        universe_id="u_001",
        tick=0,
        archetype_id="arch_001",
        parent_cohort_id=None,
        child_cohort_ids=[],
        represented_population=500,
        population_share_of_archetype=0.5,
        issue_stance={"labor_rights": 0.7},
        expression_level=0.5,
        mobilization_mode="dormant",
        speech_mode="public",
        emotions={"anger": 5.0, "fear": 3.0},
        behavior_state={"stubbornness": 0.4},
        attention=0.6,
        fatigue=0.1,
        grievance=0.3,
        perceived_efficacy=0.5,
        perceived_majority={},
        fear_of_isolation=0.2,
        willingness_to_speak=0.6,
        identity_salience=0.5,
        visible_trust_summary={},
        exposure_summary={},
        dependency_summary={},
        memory_session_id=None,
        recent_post_ids=[],
        queued_event_ids=[],
        previous_action_ids=[],
        prompt_temperature=0.4,
        representation_mode="population",
        allowed_tools=[],
        is_active=True,
    )
    base.update(overrides)
    return base


def _make_hero_archetype(**overrides) -> dict:
    base = dict(
        hero_id="hero_001",
        label="Mayor",
        description="City mayor with agenda-setting power.",
        role="elected_official",
    )
    base.update(overrides)
    return base


def _make_hero_state(**overrides) -> dict:
    base = dict(
        hero_id="hero_001",
        universe_id="u_001",
        tick=0,
        current_emotions={"resolve": 5.0},
        current_issue_stances={"labor_rights": 0.2},
        attention=0.7,
        fatigue=0.2,
        perceived_pressure=0.5,
        current_strategy="listen_and_respond",
        queued_events=[],
        recent_posts=[],
        memory_session_id=None,
    )
    base.update(overrides)
    return base


def _make_social_post(**overrides) -> dict:
    base = dict(
        post_id="post_001",
        universe_id="u_001",
        platform="worldfork-social",
        tick_created=0,
        author_actor_id="actor_001",
        author_avatar_id=None,
        content="Council hearing draws a large crowd.",
        stance_signal={"labor_rights": 0.5},
        emotion_signal={"anger": 0.2},
        credibility_signal=0.8,
        visibility_scope="public",
        reach_score=0.4,
        repost_count=0,
        comment_count=0,
    )
    base.update(overrides)
    return base


def _make_job_envelope(**overrides) -> dict:
    base = dict(
        job_id="job_001",
        job_type="simulate_universe_tick",
        priority="p1",
        run_id="bb_001",
        universe_id="u_001",
        tick=0,
        attempt_number=0,
        idempotency_key="sim:bb_001:u_001:t0:a0",
        artifact_path=None,
        payload={},
        created_at=_NOW,
        enqueued_at=None,
    )
    base.update(overrides)
    return base


def _make_model_config(**overrides) -> dict:
    base = dict(
        provider="openrouter",
        model="deepseek/deepseek-v4-flash",
        fallback_model=None,
        temperature=0.4,
        top_p=1.0,
        max_tokens=4096,
        timeout_seconds=120,
        retry_policy="exponential_backoff",
    )
    base.update(overrides)
    return base


def _make_llm_result(**overrides) -> dict:
    base = dict(
        call_id="call_001",
        provider="openrouter",
        model_used="deepseek/deepseek-v4-flash",
        prompt_tokens=12,
        completion_tokens=8,
        total_tokens=20,
        cost_usd=None,
        latency_ms=100,
        parsed_json=None,
        tool_calls=[],
        raw_response={},
        created_at=_NOW,
    )
    base.update(overrides)
    return base


def _make_embedding_config(**overrides) -> dict:
    base = dict(
        provider="openai",
        model="text-embedding-3-small",
        dimensions=None,
    )
    base.update(overrides)
    return base


def _make_event(**overrides) -> dict:
    base = dict(
        event_id="evt_001",
        universe_id="u_001",
        created_tick=0,
        scheduled_tick=1,
        duration_ticks=None,
        event_type="policy",
        title="Council schedules hearing",
        description="A council hearing is scheduled.",
        created_by_actor_id="actor_001",
        participants=[],
        target_audience=[],
        visibility="public",
        preconditions=[],
        expected_effects={},
        actual_effects=None,
        risk_level=0.2,
        status="queued",
        parent_event_id=None,
        source_llm_call_id=None,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# BigBangRun round-trip
# ---------------------------------------------------------------------------

class TestBigBangRun:
    def test_roundtrip_json(self):
        data = _make_big_bang()
        obj = BigBangRun.model_validate(data)
        dumped = obj.model_dump(mode="json")
        restored = BigBangRun.model_validate(dumped)
        assert restored.big_bang_id == obj.big_bang_id
        assert restored.status == obj.status
        assert restored.tick_duration_minutes == obj.tick_duration_minutes

    def test_active_status_is_valid_runtime_state(self):
        obj = BigBangRun.model_validate(_make_big_bang(status="active"))
        assert obj.status == "active"

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            BigBangRun.model_validate(_make_big_bang(status="unknown_status"))

    def test_zero_tick_duration_rejected(self):
        with pytest.raises(ValidationError):
            BigBangRun.model_validate(_make_big_bang(tick_duration_minutes=0))


@pytest.mark.parametrize(
    "model,payload",
    [
        (BigBangRun, _make_big_bang(big_bang_id="   ")),
        (BigBangRun, _make_big_bang(display_name="   ")),
        (BigBangRun, _make_big_bang(scenario_text="   ")),
        (BigBangRun, _make_big_bang(root_universe_id="   ")),
        (Universe, _make_universe(universe_id="   ", lineage_path=["u_000", "   "])),
        (Universe, _make_universe(big_bang_id="   ")),
        (Universe, _make_universe(parent_universe_id="   ")),
        (JobEnvelope, _make_job_envelope(run_id="   ")),
        (JobEnvelope, _make_job_envelope(job_id="   ")),
        (JobEnvelope, _make_job_envelope(idempotency_key="   ")),
    ],
)
def test_runtime_schemas_reject_blank_identity_strings(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    "model,payload",
    [
        (ModelConfig, _make_model_config(provider="   ")),
        (ModelConfig, _make_model_config(model="   ")),
        (ModelConfig, _make_model_config(fallback_model="   ")),
        (ModelConfig, _make_model_config(retry_policy="   ")),
        (LLMResult, _make_llm_result(call_id="   ")),
        (LLMResult, _make_llm_result(provider="   ")),
        (LLMResult, _make_llm_result(model_used="   ")),
        (EmbeddingConfig, _make_embedding_config(provider="   ")),
        (EmbeddingConfig, _make_embedding_config(model="   ")),
        (ProviderHealth, {"provider": "   ", "ok": True}),
    ],
)
def test_llm_schemas_reject_blank_provider_metadata(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"default_tick_duration_minutes": 0},
        {"default_max_ticks": 0},
        {"default_max_schedule_horizon_ticks": 0},
    ],
)
def test_patch_settings_rejects_invalid_runtime_defaults(payload):
    with pytest.raises(ValidationError):
        PatchSettingsRequest.model_validate(payload)


def test_patch_settings_rejects_invalid_theme():
    with pytest.raises(ValidationError):
        PatchSettingsRequest.model_validate({"theme": "neon"})


@pytest.mark.parametrize(
    "field,value",
    [
        ("provider", ""),
        ("base_url", ""),
        ("api_key_env", ""),
        ("default_model", ""),
        ("extra_headers", {"X-Test": 123}),
    ],
)
def test_patch_providers_rejects_invalid_provider_fields(field, value):
    row = {
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "default_model": "deepseek/deepseek-v4-flash",
    }
    row[field] = value

    with pytest.raises(ValidationError):
        PatchProvidersRequest.model_validate({"providers": [row]})


@pytest.mark.parametrize(
    "payload",
    [
        {"max_active_universes": 0},
        {"max_total_branches": 0},
        {"max_depth": 0},
        {"max_branches_per_tick": 0},
        {"branch_cooldown_ticks": -1},
        {"min_divergence_score": -0.1},
        {"min_divergence_score": 1.1},
    ],
)
def test_patch_branch_policy_rejects_invalid_bounds(payload):
    with pytest.raises(ValidationError):
        PatchBranchPolicyRequest.model_validate(payload)


def test_patch_routing_rejects_invalid_entry_bounds():
    payload = {
        "entries": [
            {
                "job_type": "initializer_agent",
                "preferred_provider": "openrouter",
                "preferred_model": "model",
                "temperature": 2.1,
            }
        ]
    }

    with pytest.raises(ValidationError):
        PatchRoutingRequest.model_validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("preferred_provider", ""),
        ("preferred_model", ""),
        ("retry_policy", "quadratic"),
    ],
)
def test_patch_routing_rejects_invalid_entry_fields(field, value):
    row = {
        "job_type": "initializer_agent",
        "preferred_provider": "openrouter",
        "preferred_model": "model",
    }
    row[field] = value

    with pytest.raises(ValidationError):
        PatchRoutingRequest.model_validate({"entries": [row]})


@pytest.mark.parametrize(
    "field,value",
    [
        ("rpm_limit", 0),
        ("tpm_limit", 0),
        ("max_concurrency", 0),
        ("burst_multiplier", 0.9),
        ("daily_budget_usd", -1),
        ("branch_reserved_capacity_pct", -1),
        ("branch_reserved_capacity_pct", 101),
    ],
)
def test_patch_rate_limits_rejects_invalid_bounds(field, value):
    row = {
        "provider": "openrouter",
        "rpm_limit": 60,
        "tpm_limit": 100000,
        "max_concurrency": 4,
    }
    row[field] = value

    with pytest.raises(ValidationError):
        PatchRateLimitsRequest.model_validate({"rate_limits": [row]})


def test_patch_rate_limits_rejects_invalid_retry_policy():
    with pytest.raises(ValidationError):
        PatchRateLimitsRequest.model_validate(
            {
                "rate_limits": [
                    {
                        "provider": "openrouter",
                        "rpm_limit": 60,
                        "tpm_limit": 100000,
                        "max_concurrency": 4,
                        "retry_policy": "quadratic",
                    }
                ]
            }
        )


@pytest.mark.parametrize(
    "model,payload",
    [
        (PatchSettingsRequest, {"log_level": ""}),
        (PatchSettingsRequest, {"display_timezone": ""}),
        (PatchRunRequest, {"display_name": ""}),
        (FocusBranchRequest, {"universe_id": ""}),
        (CompareRequest, {"universe_ids": ["", "u-2"]}),
        (CompareRequest, {"universe_ids": ["u-1", "u-2"], "aspect": ""}),
        (ToolCallRequest, {"tool_name": ""}),
        (ProviderTestRequest, {"provider": ""}),
        (WebhookTestRequest, {"url": "", "secret": "secret"}),
        (WebhookTestRequest, {"url": "https://example.test/hook", "secret": ""}),
        (WebhookTestRequest, {"url": "https://example.test/hook", "secret": "secret", "event_type": ""}),
        (WebhookReplayRequest, {"event_id": ""}),
        (WebhookReplayRequest, {"event_id": "event-1", "target_url": ""}),
        (SimulateTickRequest, {"idempotency_key": ""}),
        (ToolCallRequest, {"tool_name": "continue_timeline", "idempotency_key": ""}),
        (BigBangPatch, {"status": ""}),
        (EndpointLedgerEvaluateRequest, {"idempotency_key": ""}),
        (TimelineAdjudicationRequest, {"source_type": ""}),
        (TimelineAdjudicationRequest, {"summary": ""}),
        (ReportRequest, {"title": ""}),
        (ReportRequest, {"summary": ""}),
        (JobCreate, {"job_type": ""}),
        (JobCreate, {"job_type": "run_tick", "idempotency_key": ""}),
        (MultiverseContinueRequest, {"max_ticks": 1, "reason": ""}),
        (RetryRequest, {"queue": ""}),
    ],
)
def test_request_schemas_reject_empty_required_strings(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    "model,payload",
    [
        (PatchSettingsRequest, {"log_level": "   "}),
        (PatchSettingsRequest, {"display_timezone": "\t"}),
        (PatchRunRequest, {"display_name": "   "}),
        (FocusBranchRequest, {"universe_id": "   "}),
        (CompareRequest, {"universe_ids": ["   ", "u-2"]}),
        (CompareRequest, {"universe_ids": ["u-1", "u-2"], "aspect": "   "}),
        (ProviderTestRequest, {"provider": "   "}),
        (SimulateTickRequest, {"idempotency_key": "   "}),
        (BigBangPatch, {"status": "   "}),
        (RetryRequest, {"queue": "   "}),
    ],
)
def test_request_schemas_reject_blank_required_strings(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    "model,payload",
    [
        (BigBangCreate, {"name": "   "}),
        (BigBangCreate, {"name": "Run", "description": "   "}),
        (BigBangCreate, {"name": "Run", "scenario_text": "   "}),
        (BigBangCreate, {"name": "Run", "initializer_prompt": "   "}),
        (BigBangPatch, {"name": "   "}),
        (BigBangPatch, {"description": "   "}),
        (ToolCallRequest, {"tool_name": "   "}),
        (ReportVersionPatch, {"title": "   "}),
        (
            PatchProvidersRequest,
            {
                "providers": [
                    {
                        "provider": "openrouter",
                        "base_url": "https://openrouter.ai/api/v1",
                        "api_key_env": "OPENROUTER_API_KEY",
                        "default_model": "deepseek/deepseek-v4-flash",
                        "extra_headers": {"   ": "value"},
                    }
                ]
            },
        ),
        (
            PatchProvidersRequest,
            {
                "providers": [
                    {
                        "provider": "openrouter",
                        "base_url": "https://openrouter.ai/api/v1",
                        "api_key_env": "OPENROUTER_API_KEY",
                        "default_model": "deepseek/deepseek-v4-flash",
                        "extra_headers": {"X-Test": "   "},
                    }
                ]
            },
        ),
    ],
)
def test_request_schemas_reject_blank_operator_metadata_strings(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    "model,payload",
    [
        (AgentModelPatch, {"default_model": "   "}),
        (AgentModelPatch, {"agent_models": {"   ": "gpt-5.4"}}),
        (AgentModelPatch, {"agent_models": {"initializer": "   "}}),
        (CreateRunRequest, {"display_name": "Run", "scenario_text": "scenario", "uploaded_doc_ids": ["   "]}),
        (CreateRunRequest, {"display_name": "Run", "scenario_text": "scenario", "provider_snapshot_id": "   "}),
        (PatchRunRequest, {"description": "   "}),
        (PatchRunRequest, {"tags": ["   "]}),
        (BranchPreviewRequest, {"reason": "   "}),
        (BranchRequest, {"reason": "   "}),
        (ForceDeviationRequest, {"tick": 0, "mode": "god_prompt", "prompt": "   "}),
    ],
)
def test_operator_request_schemas_reject_blank_reference_strings(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def _provider_patch(**overrides) -> dict:
    row = {
        "provider": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "default_model": "deepseek/deepseek-v4-flash",
    }
    row.update(overrides)
    return row


def _routing_patch(**overrides) -> dict:
    row = {
        "job_type": "initializer_agent",
        "preferred_provider": "openrouter",
        "preferred_model": "deepseek/deepseek-v4-flash",
    }
    row.update(overrides)
    return row


def _rate_limit_patch(**overrides) -> dict:
    row = {
        "provider": "openrouter",
        "rpm_limit": 60,
        "tpm_limit": 100000,
        "max_concurrency": 4,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    "model,payload",
    [
        (CreateRunRequest, {"display_name": "   ", "scenario_text": "scenario"}),
        (CreateRunRequest, {"display_name": "Run", "scenario_text": "   "}),
        (CreateRunRequest, {"display_name": "Run", "scenario_text": "scenario", "time_horizon_label": "   "}),
        (PatchProvidersRequest, {"providers": [_provider_patch(provider="   ")]}),
        (PatchProvidersRequest, {"providers": [_provider_patch(base_url="   ")]}),
        (PatchProvidersRequest, {"providers": [_provider_patch(api_key_env="   ")]}),
        (PatchProvidersRequest, {"providers": [_provider_patch(default_model="   ")]}),
        (PatchRoutingRequest, {"entries": [_routing_patch(job_type="   ")]}),
        (PatchRoutingRequest, {"entries": [_routing_patch(preferred_provider="   ")]}),
        (PatchRoutingRequest, {"entries": [_routing_patch(preferred_model="   ")]}),
    ],
)
def test_request_schemas_reject_blank_legacy_min_length_strings(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    "model,payload",
    [
        (PatchProvidersRequest, {"providers": [_provider_patch(fallback_model="")]}),
        (PatchRoutingRequest, {"entries": [_routing_patch(fallback_provider="openrouter", fallback_model="")]}),
        (PatchRoutingRequest, {"entries": [_routing_patch(fallback_provider="")]}),
        (PatchRateLimitsRequest, {"rate_limits": [_rate_limit_patch(provider="")]}),
        (ProviderTestRequest, {"provider": "openrouter", "model": ""}),
        (WebhookTestRequest, {"url": "not-a-url", "secret": "secret"}),
        (WebhookReplayRequest, {"event_id": "event-1", "target_url": "not-a-url"}),
    ],
)
def test_request_schemas_reject_invalid_optional_string_metadata(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


# ---------------------------------------------------------------------------
# Universe lineage invariants
# ---------------------------------------------------------------------------

class TestUniverse:
    def test_valid_root_universe(self):
        u = Universe.model_validate(
            dict(
                universe_id="u_000",
                big_bang_id="bb_001",
                parent_universe_id=None,
                child_universe_ids=[],
                branch_from_tick=0,
                branch_depth=0,
                lineage_path=["u_000"],
                status="active",
                branch_reason="root",
                branch_delta=None,
                current_tick=0,
                latest_metrics={},
                created_at=_NOW,
                frozen_at=None,
                killed_at=None,
                completed_at=None,
            )
        )
        assert u.branch_depth == 0
        assert u.lineage_path[-1] == u.universe_id

    def test_valid_child_universe(self):
        u = Universe.model_validate(_make_universe())
        assert u.branch_depth == 1

    def test_rejects_mismatched_lineage_path_last(self):
        """lineage_path[-1] must equal universe_id."""
        with pytest.raises(ValidationError, match="lineage_path"):
            Universe.model_validate(
                _make_universe(lineage_path=["u_000", "u_WRONG"])
            )

    def test_rejects_branch_depth_mismatch(self):
        """branch_depth must equal len(lineage_path) - 1."""
        with pytest.raises(ValidationError, match="branch_depth"):
            Universe.model_validate(
                _make_universe(branch_depth=99)
            )

    def test_rejects_null_parent_nonzero_depth(self):
        """parent_universe_id=None implies branch_depth=0."""
        with pytest.raises(ValidationError):
            Universe.model_validate(
                _make_universe(
                    universe_id="u_001",
                    parent_universe_id=None,
                    branch_depth=1,
                    lineage_path=["u_000", "u_001"],
                )
            )

    def test_rejects_nonnull_parent_zero_depth(self):
        """branch_depth=0 implies parent_universe_id=None."""
        with pytest.raises(ValidationError):
            Universe.model_validate(
                _make_universe(
                    universe_id="u_000",
                    parent_universe_id="u_parent",
                    branch_depth=0,
                    lineage_path=["u_000"],
                )
            )

    def test_frozen_requires_frozen_at(self):
        with pytest.raises(ValidationError, match="frozen_at"):
            Universe.model_validate(
                _make_universe(status="frozen", frozen_at=None)
            )

    def test_killed_requires_killed_at(self):
        with pytest.raises(ValidationError, match="killed_at"):
            Universe.model_validate(
                _make_universe(status="killed", killed_at=None)
            )

    def test_completed_requires_completed_at(self):
        with pytest.raises(ValidationError, match="completed_at"):
            Universe.model_validate(
                _make_universe(status="completed", completed_at=None)
            )

    def test_candidate_timestamps_all_none(self):
        """candidate status allows all timestamp fields to be None."""
        u = Universe.model_validate(_make_universe(status="candidate"))
        assert u.frozen_at is None
        assert u.killed_at is None
        assert u.completed_at is None

    def test_terminated_status_is_valid_runtime_state(self):
        u = Universe.model_validate(_make_universe(status="terminated"))
        assert u.status == "terminated"


# ---------------------------------------------------------------------------
# Event runtime statuses
# ---------------------------------------------------------------------------

class TestEvent:
    @pytest.mark.parametrize("status", ["queued", "executed"])
    def test_accepts_runtime_event_statuses(self, status):
        event = Event.model_validate(_make_event(status=status))
        assert event.status == status

    def test_invalid_status_rejected(self):
        with pytest.raises(ValidationError):
            Event.model_validate(_make_event(status="unknown_status"))

    @pytest.mark.parametrize(
        "payload",
        [
            _make_event(event_id="   "),
            _make_event(universe_id="   "),
            _make_event(event_type="   "),
            _make_event(title="   "),
            _make_event(description="   "),
            _make_event(created_by_actor_id="   "),
            _make_event(participants=["   "]),
            _make_event(target_audience=["   "]),
            _make_event(parent_event_id="   "),
            _make_event(source_llm_call_id="   "),
        ],
    )
    def test_rejects_blank_identity_strings(self, payload):
        with pytest.raises(ValidationError):
            Event.model_validate(payload)


@pytest.mark.parametrize(
    "model,payload",
    [
        (PopulationArchetype, _make_population_archetype(archetype_id="   ")),
        (PopulationArchetype, _make_population_archetype(label="   ")),
        (PopulationArchetype, _make_population_archetype(description="   ")),
        (CohortState, _make_cohort(cohort_id="   ")),
        (CohortState, _make_cohort(universe_id="   ")),
        (CohortState, _make_cohort(archetype_id="   ")),
        (HeroArchetype, _make_hero_archetype(hero_id="   ")),
        (HeroArchetype, _make_hero_archetype(label="   ")),
        (HeroArchetype, _make_hero_archetype(description="   ")),
        (HeroArchetype, _make_hero_archetype(role="   ")),
    ],
)
def test_actor_schemas_reject_blank_identity_strings(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    "model,payload",
    [
        (CohortState, _make_cohort(parent_cohort_id="   ")),
        (CohortState, _make_cohort(child_cohort_ids=["   "])),
        (CohortState, _make_cohort(memory_session_id="   ")),
        (CohortState, _make_cohort(recent_post_ids=["   "])),
        (CohortState, _make_cohort(queued_event_ids=["   "])),
        (CohortState, _make_cohort(previous_action_ids=["   "])),
        (CohortState, _make_cohort(allowed_tools=["   "])),
        (HeroState, _make_hero_state(hero_id="   ")),
        (HeroState, _make_hero_state(universe_id="   ")),
        (HeroState, _make_hero_state(queued_events=["   "])),
    ],
)
def test_actor_schemas_reject_blank_reference_strings(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    "model,payload",
    [
        (SocialPost, _make_social_post(post_id="   ")),
        (SocialPost, _make_social_post(universe_id="   ")),
        (SocialPost, _make_social_post(platform="   ")),
        (SocialPost, _make_social_post(author_actor_id="   ")),
        (SocialPost, _make_social_post(author_avatar_id="   ")),
        (SocialPost, _make_social_post(content="   ")),
        (HeroState, _make_hero_state(recent_posts=["   "])),
        (HeroState, _make_hero_state(memory_session_id="   ")),
        (HeroArchetype, _make_hero_archetype(scheduling_permissions=["   "])),
        (HeroArchetype, _make_hero_archetype(allowed_channels=["   "])),
    ],
)
def test_post_and_actor_schemas_reject_blank_reference_strings(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    "model,payload",
    [
        (PopulationArchetype, _make_population_archetype(age_band="   ")),
        (PopulationArchetype, _make_population_archetype(education_profile="   ")),
        (PopulationArchetype, _make_population_archetype(occupation_or_role="   ")),
        (PopulationArchetype, _make_population_archetype(socioeconomic_band="   ")),
        (PopulationArchetype, _make_population_archetype(institution_membership=["   "])),
        (PopulationArchetype, _make_population_archetype(demographic_tags=["   "])),
        (PopulationArchetype, _make_population_archetype(preferred_channels=["   "])),
        (PopulationArchetype, _make_population_archetype(identity_tags=["   "])),
        (PopulationArchetype, _make_population_archetype(allowed_action_classes=["   "])),
        (HeroArchetype, _make_hero_archetype(institution="   ")),
    ],
)
def test_actor_profile_schemas_reject_blank_metadata_strings(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


# ---------------------------------------------------------------------------
# CohortState emotion clamping
# ---------------------------------------------------------------------------

class TestCohortState:
    def test_valid_cohort(self):
        c = CohortState.model_validate(_make_cohort())
        assert c.cohort_id == "c_001"

    def test_emotions_clamped_above_10(self):
        """Emotions > 10 should be clamped to 10, not rejected."""
        c = CohortState.model_validate(
            _make_cohort(emotions={"anger": 15.0, "fear": -2.0})
        )
        assert c.emotions["anger"] == 10.0
        assert c.emotions["fear"] == 0.0

    def test_behavior_state_clamped(self):
        c = CohortState.model_validate(
            _make_cohort(behavior_state={"stubbornness": 1.5, "openness": -0.1})
        )
        assert c.behavior_state["stubbornness"] == 1.0
        assert c.behavior_state["openness"] == 0.0

    def test_invalid_mobilization_mode_rejected(self):
        with pytest.raises(ValidationError, match="mobilization_mode"):
            CohortState.model_validate(
                _make_cohort(mobilization_mode="flying")
            )

    def test_invalid_speech_mode_rejected(self):
        with pytest.raises(ValidationError, match="speech_mode"):
            CohortState.model_validate(
                _make_cohort(speech_mode="whisper")
            )

    def test_population_share_out_of_range(self):
        with pytest.raises(ValidationError):
            CohortState.model_validate(
                _make_cohort(population_share_of_archetype=1.5)
            )

    def test_is_active_defaults_true(self):
        data = _make_cohort()
        data.pop("is_active", None)
        c = CohortState.model_validate(data)
        assert c.is_active is True


# ---------------------------------------------------------------------------
# SplitProposal validation
# ---------------------------------------------------------------------------

def _make_child_spec(pop: int = 100) -> dict:
    return dict(
        archetype_id="arch_001",
        represented_population=pop,
        issue_stance={"labor_rights": 0.7},
        expression_level=0.5,
        mobilization_mode="dormant",
        speech_mode="public",
        seed_emotions={"anger": 3.0},
        interpretation_note="test",
    )


def _make_split_proposal(**overrides) -> dict:
    base = dict(
        parent_cohort_id="c_001",
        children=[_make_child_spec(300), _make_child_spec(200)],
        split_distance=0.4,
        rationale="opinion split",
    )
    base.update(overrides)
    return base


def _make_merge_proposal(**overrides) -> dict:
    base = dict(
        cohort_ids=["c_001", "c_002"],
        archetype_id="arch_001",
        rationale="low divergence",
    )
    base.update(overrides)
    return base


class TestSplitProposal:
    def test_valid_two_children(self):
        sp = SplitProposal.model_validate(
            dict(
                parent_cohort_id="c_001",
                children=[_make_child_spec(300), _make_child_spec(200)],
                split_distance=0.4,
                rationale="opinion split",
            )
        )
        assert len(sp.children) == 2

    def test_rejects_single_child(self):
        """len(children) < 2 must be rejected."""
        with pytest.raises(ValidationError):
            SplitProposal.model_validate(
                dict(
                    parent_cohort_id="c_001",
                    children=[_make_child_spec(500)],
                    split_distance=0.4,
                    rationale="invalid",
                )
            )

    @pytest.mark.parametrize(
        "model,payload",
        [
            (ChildSplitSpec, _make_child_spec() | {"archetype_id": "   "}),
            (ChildSplitSpec, _make_child_spec() | {"mobilization_mode": "   "}),
            (ChildSplitSpec, _make_child_spec() | {"speech_mode": "   "}),
            (ChildSplitSpec, _make_child_spec() | {"interpretation_note": "   "}),
            (SplitProposal, _make_split_proposal(parent_cohort_id="   ")),
            (SplitProposal, _make_split_proposal(rationale="   ")),
            (MergeProposal, _make_merge_proposal(cohort_ids=["   ", "c_002"])),
            (MergeProposal, _make_merge_proposal(cohort_ids=["c_001", "   "])),
            (MergeProposal, _make_merge_proposal(archetype_id="   ")),
            (MergeProposal, _make_merge_proposal(rationale="   ")),
        ],
    )
    def test_rejects_blank_sociology_strings(self, model, payload):
        with pytest.raises(ValidationError):
            model.model_validate(payload)

    def test_rejects_zero_children(self):
        with pytest.raises(ValidationError):
            SplitProposal.model_validate(
                dict(
                    parent_cohort_id="c_001",
                    children=[],
                    split_distance=0.4,
                    rationale="invalid",
                )
            )


# ---------------------------------------------------------------------------
# BranchPolicy validation
# ---------------------------------------------------------------------------

class TestBranchPolicy:
    def test_valid_policy(self):
        p = BranchPolicy.model_validate(
            dict(
                max_active_universes=50,
                max_total_branches=500,
                max_depth=8,
                max_branches_per_tick=5,
                branch_cooldown_ticks=3,
                min_divergence_score=0.35,
                auto_prune_low_value=True,
            )
        )
        assert p.max_depth == 8

    def test_rejects_max_depth_zero(self):
        with pytest.raises(ValidationError):
            BranchPolicy.model_validate(
                dict(
                    max_active_universes=50,
                    max_total_branches=500,
                    max_depth=0,
                    max_branches_per_tick=5,
                    branch_cooldown_ticks=3,
                    min_divergence_score=0.35,
                    auto_prune_low_value=True,
                )
            )

    def test_rejects_negative_max_depth(self):
        with pytest.raises(ValidationError):
            BranchPolicy.model_validate(
                dict(
                    max_active_universes=50,
                    max_total_branches=500,
                    max_depth=-1,
                    max_branches_per_tick=5,
                    branch_cooldown_ticks=3,
                    min_divergence_score=0.35,
                    auto_prune_low_value=True,
                )
            )

    def test_rejects_min_divergence_above_1(self):
        with pytest.raises(ValidationError):
            BranchPolicy.model_validate(
                dict(
                    max_active_universes=50,
                    max_total_branches=500,
                    max_depth=8,
                    max_branches_per_tick=5,
                    branch_cooldown_ticks=3,
                    min_divergence_score=1.5,
                    auto_prune_low_value=True,
                )
            )

    def test_rejects_min_divergence_below_0(self):
        with pytest.raises(ValidationError):
            BranchPolicy.model_validate(
                dict(
                    max_active_universes=50,
                    max_total_branches=500,
                    max_depth=8,
                    max_branches_per_tick=5,
                    branch_cooldown_ticks=3,
                    min_divergence_score=-0.1,
                    auto_prune_low_value=True,
                )
            )


# ---------------------------------------------------------------------------
# BranchDelta discriminated union
# ---------------------------------------------------------------------------

class TestBranchDelta:
    def _parse(self, data: dict):
        from pydantic import TypeAdapter
        ta = TypeAdapter(BranchDelta)
        return ta.validate_python(data)

    def test_counterfactual_event_rewrite(self):
        obj = self._parse(
            dict(
                type="counterfactual_event_rewrite",
                target_event_id="event_001",
                parent_version="defensive statement",
                child_version="apology plus audit",
            )
        )
        assert isinstance(obj, CounterfactualEventRewriteDelta)
        assert obj.type == "counterfactual_event_rewrite"

    def test_parameter_shift(self):
        obj = self._parse(
            dict(
                type="parameter_shift",
                target="news_channel.local_press.bias",
                delta={"risk_salience": 0.2},
            )
        )
        assert isinstance(obj, ParameterShiftDelta)
        assert obj.delta["risk_salience"] == 0.2

    def test_actor_state_override(self):
        obj = self._parse(
            dict(
                type="actor_state_override",
                actor_id="c_001",
                field="expression_level",
                new_value=0.9,
            )
        )
        assert isinstance(obj, ActorStateOverrideDelta)
        assert obj.field == "expression_level"

    def test_actor_state_override_rejects_population_override(self):
        with pytest.raises(ValidationError):
            ActorStateOverrideDelta(
                type="actor_state_override",
                actor_id="c_001",
                field="represented_population",
                new_value=999,
            )

    def test_hero_decision_override(self):
        obj = self._parse(
            dict(
                type="hero_decision_override",
                hero_id="hero_001",
                tick=4,
                new_decision={"action": "press_release"},
            )
        )
        assert isinstance(obj, HeroDecisionOverrideDelta)
        assert obj.tick == 4

    def test_unknown_type_rejected(self):
        from pydantic import TypeAdapter, ValidationError as PydanticValidationError
        ta = TypeAdapter(BranchDelta)
        with pytest.raises(PydanticValidationError):
            ta.validate_python({"type": "unknown_delta_type", "foo": "bar"})

    @pytest.mark.parametrize(
        "payload",
        [
            {
                "type": "counterfactual_event_rewrite",
                "target_event_id": "   ",
                "parent_version": "defensive statement",
                "child_version": "apology plus audit",
            },
            {
                "type": "counterfactual_event_rewrite",
                "target_event_id": "event_001",
                "parent_version": "   ",
                "child_version": "apology plus audit",
            },
            {
                "type": "counterfactual_event_rewrite",
                "target_event_id": "event_001",
                "parent_version": "defensive statement",
                "child_version": "   ",
            },
            {"type": "parameter_shift", "target": "   ", "delta": {"risk_salience": 0.2}},
            {"type": "actor_state_override", "actor_id": "   ", "field": "expression_level", "new_value": 0.9},
            {"type": "actor_state_override", "actor_id": "c_001", "field": "   ", "new_value": 0.9},
            {"type": "actor_state_override", "actor_id": "c_001", "field": "expression_level", "new_value": "   "},
            {"type": "hero_decision_override", "hero_id": "   ", "tick": 4, "new_decision": {"action": "press_release"}},
        ],
    )
    def test_branch_delta_rejects_blank_strings(self, payload):
        with pytest.raises(ValidationError):
            self._parse(payload)

    @pytest.mark.parametrize("field", ["universe_id", "branch_trigger"])
    def test_branch_node_rejects_blank_identifiers(self, field):
        payload = {
            "universe_id": "u_001",
            "parent_universe_id": None,
            "child_universe_ids": [],
            "depth": 0,
            "branch_tick": 0,
            "branch_point_id": "bp_001",
            "branch_trigger": "god_review",
            "branch_delta": {},
            "status": "active",
            "metrics_summary": {},
            "cost_estimate": {},
            "descendant_count": 0,
        }
        payload[field] = "   "

        with pytest.raises(ValidationError):
            BranchNode.model_validate(payload)


# ---------------------------------------------------------------------------
# JobEnvelope.redis_key()
# ---------------------------------------------------------------------------

class TestJobEnvelope:
    def test_redis_key_is_deterministic(self):
        """Same idempotency_key → same redis_key regardless of attempt_number."""
        base = _make_job_envelope(idempotency_key="sim:bb_001:u_001:t0", attempt_number=0)
        retry = _make_job_envelope(idempotency_key="sim:bb_001:u_001:t0", attempt_number=3)
        j1 = JobEnvelope.model_validate(base)
        j2 = JobEnvelope.model_validate(retry)
        assert j1.redis_key() == j2.redis_key()

    def test_redis_key_contains_idempotency_key(self):
        key = "sim:bb_001:u_001:t2:a0"
        j = JobEnvelope.model_validate(_make_job_envelope(idempotency_key=key))
        assert key in j.redis_key()

    def test_redis_key_idempotent(self):
        """Calling redis_key() multiple times returns the same string."""
        j = JobEnvelope.model_validate(_make_job_envelope())
        assert j.redis_key() == j.redis_key()

    def test_different_idem_keys_produce_different_redis_keys(self):
        j1 = JobEnvelope.model_validate(_make_job_envelope(idempotency_key="sim:a"))
        j2 = JobEnvelope.model_validate(_make_job_envelope(idempotency_key="sim:b"))
        assert j1.redis_key() != j2.redis_key()


class TestJobStatus:
    @pytest.mark.parametrize(
        "status",
        [
            "paused",
            "interrupt_requested",
            "interrupted",
            "cancelled",
            "completed",
            "dead_lettered",
        ],
    )
    def test_accepts_runtime_lifecycle_statuses(self, status):
        payload = {
            "job_id": "job-001",
            "status": status,
            "attempt_number": 0,
        }

        parsed = JobStatus.model_validate(payload)

        assert parsed.status == status


def _make_provider_config(**overrides) -> dict:
    base = dict(
        provider="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        default_model="deepseek/deepseek-v4-flash",
        fallback_model=None,
    )
    base.update(overrides)
    return base


def _make_model_routing_entry(**overrides) -> dict:
    base = dict(
        job_type="simulate_universe_tick",
        preferred_provider="openrouter",
        preferred_model="deepseek/deepseek-v4-flash",
        fallback_provider=None,
        fallback_model=None,
        temperature=0.4,
        top_p=1.0,
        max_tokens=4096,
        max_concurrency=4,
        requests_per_minute=60,
        tokens_per_minute=150000,
        timeout_seconds=120,
        retry_policy="exponential_backoff",
        daily_budget_usd=None,
    )
    base.update(overrides)
    return base


def _make_rate_limit_config(**overrides) -> dict:
    base = dict(
        provider="openrouter",
        rpm_limit=60,
        tpm_limit=150000,
        max_concurrency=4,
        burst_multiplier=1.2,
        retry_policy="exponential_backoff",
        branch_reserved_capacity_pct=20.0,
    )
    base.update(overrides)
    return base


def _make_branch_node(**overrides) -> dict:
    base = dict(
        universe_id="u_001",
        parent_universe_id=None,
        child_universe_ids=[],
        depth=0,
        branch_tick=0,
        branch_point_id="bp_001",
        branch_trigger="god_review",
        branch_delta={},
        status="active",
        metrics_summary={},
        cost_estimate={},
        descendant_count=0,
    )
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "model,payload",
    [
        (ProviderConfig, _make_provider_config(extra_headers={"   ": "value"})),
        (ProviderConfig, _make_provider_config(extra_headers={"X-Test": "   "})),
        (GlobalSettings, {"log_level": "   "}),
        (GlobalSettings, {"display_timezone": "   "}),
        (GlobalSettings, {"run_folder_root": "   "}),
        (GlobalSettings, {"default_representation_mode_thresholds": {"   ": [2, 25]}}),
        (BranchNode, _make_branch_node(parent_universe_id="   ")),
        (BranchNode, _make_branch_node(child_universe_ids=["   "])),
        (BranchPolicyResult, {"decision": "approve", "reason": "   "}),
        (HeroArchetype, _make_hero_archetype(location_scope="   ")),
    ],
)
def test_remaining_persistence_schemas_reject_blank_strings(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


@pytest.mark.parametrize(
    "model,payload",
    [
        (ProviderConfig, _make_provider_config(provider="   ")),
        (ProviderConfig, _make_provider_config(base_url="   ")),
        (ProviderConfig, _make_provider_config(api_key_env="   ")),
        (ProviderConfig, _make_provider_config(default_model="   ")),
        (ProviderConfig, _make_provider_config(fallback_model="   ")),
        (ModelRoutingEntry, _make_model_routing_entry(preferred_provider="   ")),
        (ModelRoutingEntry, _make_model_routing_entry(preferred_model="   ")),
        (ModelRoutingEntry, _make_model_routing_entry(fallback_provider="   ")),
        (ModelRoutingEntry, _make_model_routing_entry(fallback_model="   ")),
        (RateLimitConfig, _make_rate_limit_config(provider="   ")),
    ],
)
def test_settings_schemas_reject_blank_provider_strings(model, payload):
    with pytest.raises(ValidationError):
        model.model_validate(payload)


# ---------------------------------------------------------------------------
# ModelRoutingEntry — unknown job_type via Literal
# ---------------------------------------------------------------------------

class TestModelRoutingEntry:
    def test_valid_job_type(self):
        entry = ModelRoutingEntry.model_validate(
            dict(
                job_type="simulate_universe_tick",
                preferred_provider="openrouter",
                preferred_model="openai/gpt-4o",
                fallback_provider=None,
                fallback_model="openai/gpt-4o-mini",
                temperature=0.4,
                top_p=1.0,
                max_tokens=4096,
                max_concurrency=4,
                requests_per_minute=60,
                tokens_per_minute=150000,
                timeout_seconds=120,
                retry_policy="exponential_backoff",
                daily_budget_usd=None,
            )
        )
        assert entry.job_type == "simulate_universe_tick"

    def test_valid_audited_llm_route_type(self):
        entry = ModelRoutingEntry.model_validate(
            dict(
                job_type="report_agent",
                preferred_provider="openai-codex",
                preferred_model="gpt-5.4",
                fallback_provider="openrouter",
                fallback_model="deepseek/deepseek-v4-flash",
                temperature=0.2,
                top_p=1.0,
                max_tokens=8192,
                max_concurrency=2,
                requests_per_minute=20,
                tokens_per_minute=200000,
                timeout_seconds=300,
                retry_policy="exponential_backoff",
                daily_budget_usd=None,
            )
        )
        assert entry.job_type == "report_agent"

    def test_audited_llm_route_type_matches_runtime_catalog(self):
        schema_routes = set(get_args(AuditedLLMRouteType))
        runtime_routes = {str(route.route) for route in AUDITED_LLM_ROUTES}

        assert runtime_routes <= schema_routes

    def test_unknown_job_type_rejected(self):
        with pytest.raises(ValidationError):
            ModelRoutingEntry.model_validate(
                dict(
                    job_type="not_a_real_job_type",
                    preferred_provider="openrouter",
                    preferred_model="openai/gpt-4o",
                    temperature=0.4,
                    top_p=1.0,
                    max_tokens=4096,
                    max_concurrency=4,
                    requests_per_minute=60,
                    tokens_per_minute=150000,
                    timeout_seconds=120,
                    retry_policy="exponential_backoff",
                )
            )
