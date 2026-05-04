"""Unit tests for backend.app.llm.sot_prompt_builder."""
from __future__ import annotations


import pytest

from backend.app.schemas.actors import (
    CohortState,
    HeroArchetype,
    HeroState,
    PopulationArchetype,
)
from backend.app.schemas.common import Clock
from backend.app.llm.sot_prompt_builder import PromptBuilder
from backend.app.storage.sot_loader import load_sot


@pytest.fixture(scope="module")
def sot():
    return load_sot()


@pytest.fixture(scope="module")
def builder(sot):
    return PromptBuilder(sot)


@pytest.fixture
def archetype():
    return PopulationArchetype(
        archetype_id="arch_couriers",
        label="Metro Region gig couriers",
        description="Couriers reliant on platform algorithms.",
        population_total=15000,
        geography={"region_label": "Metro Region", "scope": "metro"},
        issue_exposure=0.8,
        material_stake=0.85,
        symbolic_stake=0.4,
        vulnerability_to_policy=0.7,
        ability_to_influence_outcome=0.3,
        attention_capacity=0.6,
        attention_decay_rate=0.15,
        coordination_capacity=0.5,
        mobilization_capacity=0.6,
        legal_or_status_risk_sensitivity=0.5,
        min_split_population=100,
        min_split_share=0.1,
        max_child_cohorts=4,
        identity_tags=["worker", "platform_dependent"],
    )


@pytest.fixture
def cohort():
    return CohortState(
        cohort_id="coh_couriers_oppose",
        universe_id="U000",
        tick=4,
        archetype_id="arch_couriers",
        represented_population=2500,
        population_share_of_archetype=0.17,
        issue_stance={"primary_issue": -0.4},
        expression_level=0.55,
        mobilization_mode="murmur",
        speech_mode="semi_public",
        emotions={"anger": 6.0, "fear": 3.0},
        behavior_state={},
        attention=0.6,
        fatigue=0.3,
        prompt_temperature=0.5,
        representation_mode="population",
        allowed_tools=[
            "read_visible_feed",
            "create_social_post",
            "stay_silent",
            "self_rate_emotions",
            "queue_event",
        ],
    )


@pytest.fixture
def clock():
    return Clock(
        current_tick=4,
        tick_duration_minutes=120,
        elapsed_minutes=480,
        previous_tick_minutes=120,
        max_schedule_horizon_ticks=5,
    )


# ---------------------------------------------------------------------------
# Cohort packet
# ---------------------------------------------------------------------------


class TestCohortPacket:
    def test_returns_packet_with_nonempty_system(self, builder, cohort, archetype, clock):
        pkt = builder.build_cohort_packet(
            cohort=cohort,
            archetype=archetype,
            clock=clock,
            visible_feed=[],
            visible_events=[],
            own_queued_events=[],
            own_recent_actions=[],
            retrieved_memory=None,
        )
        assert pkt.system
        assert len(pkt.system) > 500
        assert pkt.actor_kind == "cohort"
        assert pkt.actor_id == "coh_couriers_oppose"

    def test_correct_output_schema_id(self, builder, cohort, archetype, clock):
        pkt = builder.build_cohort_packet(
            cohort=cohort,
            archetype=archetype,
            clock=clock,
            visible_feed=[],
            visible_events=[],
            own_queued_events=[],
            own_recent_actions=[],
            retrieved_memory=None,
        )
        assert pkt.output_schema_id == "cohort_decision_schema"

    def test_temperature_in_population_band(self, builder, cohort, archetype, clock):
        # represented_population=2500 → "population" band: 0.35–0.6
        pkt = builder.build_cohort_packet(
            cohort=cohort,
            archetype=archetype,
            clock=clock,
            visible_feed=[],
            visible_events=[],
            own_queued_events=[],
            own_recent_actions=[],
            retrieved_memory=None,
        )
        assert 0.35 <= pkt.temperature <= 0.6

    def test_rendered_system_contains_inline_jsonschema(
        self, builder, sot, cohort, archetype, clock
    ):
        pkt = builder.build_cohort_packet(
            cohort=cohort,
            archetype=archetype,
            clock=clock,
            visible_feed=[],
            visible_events=[],
            own_queued_events=[],
            own_recent_actions=[],
            retrieved_memory=None,
        )
        schema = sot.prompt_contracts["cohort_decision_schema"]
        # The rendered template injects the full JSONSchema under "Schema".
        # We check that the schema's $id fragment appears verbatim.
        assert schema["$id"] in pkt.system
        assert "CohortDecisionOutput" in pkt.system
        assert "Return JSON only" in pkt.system

    def test_allowed_tools_filtered(self, builder, cohort, archetype, clock):
        pkt = builder.build_cohort_packet(
            cohort=cohort,
            archetype=archetype,
            clock=clock,
            visible_feed=[],
            visible_events=[],
            own_queued_events=[],
            own_recent_actions=[],
            retrieved_memory=None,
        )
        # Only the tools in cohort.allowed_tools should be exposed.
        assert set(pkt.allowed_tools) == set(cohort.allowed_tools)

    def test_metadata_keys(self, builder, cohort, archetype, clock):
        pkt = builder.build_cohort_packet(
            cohort=cohort,
            archetype=archetype,
            clock=clock,
            visible_feed=[],
            visible_events=[],
            own_queued_events=[],
            own_recent_actions=[],
            retrieved_memory=None,
        )
        for key in ("prompt_template", "sot_version", "actor_id", "tick",
                    "universe_id", "build_ms", "schema_id"):
            assert key in pkt.metadata
        assert pkt.metadata["prompt_template"] == "cohort_tick"
        assert pkt.metadata["schema_id"] == "cohort_decision_schema"

    def test_temperature_deterministic_for_same_actor_tick(
        self, builder, cohort, archetype, clock
    ):
        pkt1 = builder.build_cohort_packet(
            cohort=cohort, archetype=archetype, clock=clock,
            visible_feed=[], visible_events=[], own_queued_events=[],
            own_recent_actions=[], retrieved_memory=None,
        )
        pkt2 = builder.build_cohort_packet(
            cohort=cohort, archetype=archetype, clock=clock,
            visible_feed=[], visible_events=[], own_queued_events=[],
            own_recent_actions=[], retrieved_memory=None,
        )
        assert pkt1.temperature == pkt2.temperature


# ---------------------------------------------------------------------------
# Hero packet
# ---------------------------------------------------------------------------


class TestHeroPacket:
    def test_hero_temperature_in_band(self, builder, clock):
        hero_arch = HeroArchetype(
            hero_id="h_mayor", label="Mayor A",
            description="Mayor of metro city", role="mayor",
            institution="City Hall", location_scope="metro",
            public_reach=0.8, institutional_power=0.85, financial_power=0.5,
            agenda_control=0.7, media_access=0.8, volatility=0.3,
            ego_sensitivity=0.6, strategic_discipline=0.7,
            controversy_tolerance=0.4, direct_event_power=0.5,
            scheduling_permissions=["policy_vote", "public_statement"],
            allowed_channels=["mainstream_news"],
        )
        hero_state = HeroState(
            hero_id="h_mayor", universe_id="U000", tick=4,
            current_emotions={"anger": 3.0, "calm": 6.0},
            current_issue_stances={"primary_issue": 0.2},
            attention=0.7, fatigue=0.3, perceived_pressure=0.6,
            current_strategy="Hold center",
        )
        b = builder
        pkt = b.build_hero_packet(
            hero=hero_state, archetype=hero_arch, clock=clock,
            visible_feed=[], visible_events=[], own_queued_events=[],
            own_recent_actions=[], retrieved_memory=None,
        )
        assert pkt.actor_kind == "hero"
        assert 0.7 <= pkt.temperature <= 1.0
        assert pkt.output_schema_id == "hero_decision_schema"


# ---------------------------------------------------------------------------
# God packet
# ---------------------------------------------------------------------------


class TestGodPacket:
    def test_god_packet_includes_branch_candidates(self, builder, clock):
        candidates = [
            {"id": "cand_1", "type": "counterfactual_event_rewrite"},
            {"id": "cand_2", "type": "parameter_shift"},
        ]
        pkt = builder.build_god_packet(
            universe_state={
                "universe_id": "U000",
                "branch_depth": 0,
                "lineage_path": ["U000"],
                "status": "active",
                "current_tick": 4,
                "tick_duration_minutes": 120,
                "elapsed_minutes": 480,
            },
            recent_ticks=[{"tick": 3, "summary": "calm"}],
            event_proposals=[],
            social_posts=[],
            metrics={"dominant_emotion": ["anger", 5.0]},
            branch_candidates=candidates,
            rate_limit_state={"rpm": 100},
            budget_state={"daily_used": 12.5},
            prior_branch_history=[],
            clock=clock,
        )
        assert pkt.actor_kind == "god"
        assert pkt.output_schema_id == "god_review_schema"
        assert "cand_1" in pkt.system
        assert "cand_2" in pkt.system
        assert 0.4 <= pkt.temperature <= 0.7


# ---------------------------------------------------------------------------
# Initializer packet
# ---------------------------------------------------------------------------


class TestInitializerPacket:
    def test_initializer_packet_includes_scenario_text(self, builder):
        scenario = "Metro Region gig couriers organize against algorithmic deactivations."
        pkt = builder.build_initializer_packet(
            scenario_text=scenario,
            uploaded_docs=[],
            time_horizon_label="6 months",
            tick_duration_minutes=1440,
            max_ticks=180,
        )
        assert scenario in pkt.system
        assert pkt.output_schema_id == "initializer_schema"
        assert 0.5 <= pkt.temperature <= 0.8

    def test_initializer_includes_uploaded_docs(self, builder):
        pkt = builder.build_initializer_packet(
            scenario_text="Test scenario",
            uploaded_docs=[
                {"name": "doc1.pdf", "summary": "Background", "excerpt": "Excerpt text"}
            ],
            time_horizon_label="3 months",
            tick_duration_minutes=720,
            max_ticks=90,
        )
        assert "doc1.pdf" in pkt.system
        assert "Excerpt text" in pkt.system
