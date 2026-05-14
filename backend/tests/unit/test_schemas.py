"""
Unit tests for backend.app.schemas.
Covers all validator cases specified in the B1-C deliverables.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import get_args

import pytest
from pydantic import ValidationError

from app.llm.routing import AUDITED_LLM_ROUTES
from backend.app.schemas import (
    BigBangRun,
    BranchDelta,
    BranchPolicy,
    CohortState,
    JobEnvelope,
    ModelRoutingEntry,
    SplitProposal,
    Universe,
)
from backend.app.schemas.jobs import AuditedLLMRouteType, JobStatus
from backend.app.schemas.branching import (
    ActorStateOverrideDelta,
    CounterfactualEventRewriteDelta,
    HeroDecisionOverrideDelta,
    ParameterShiftDelta,
)


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
