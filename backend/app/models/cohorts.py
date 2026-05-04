"""
PopulationArchetypeModel and CohortStateModel.
Tables: population_archetypes, cohort_states
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from backend.app.schemas.actors import CohortState, PopulationArchetype


# ---------------------------------------------------------------------------
# PopulationArchetypeModel
# ---------------------------------------------------------------------------

class PopulationArchetypeModel(Base, TimestampMixin):
    __tablename__ = "population_archetypes"

    archetype_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    big_bang_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("big_bang_runs.big_bang_id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)

    population_total: Mapped[int] = mapped_column(Integer, nullable=False)

    geography: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    age_band: Mapped[str | None] = mapped_column(String, nullable=True)
    education_profile: Mapped[str | None] = mapped_column(String, nullable=True)
    occupation_or_role: Mapped[str | None] = mapped_column(String, nullable=True)
    socioeconomic_band: Mapped[str | None] = mapped_column(String, nullable=True)
    institution_membership: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    demographic_tags: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )

    issue_exposure: Mapped[float] = mapped_column(Float, nullable=False)
    material_stake: Mapped[float] = mapped_column(Float, nullable=False)
    symbolic_stake: Mapped[float] = mapped_column(Float, nullable=False)
    vulnerability_to_policy: Mapped[float] = mapped_column(Float, nullable=False)
    ability_to_influence_outcome: Mapped[float] = mapped_column(Float, nullable=False)

    ideology_axes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    value_priors: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    behavior_axes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    baseline_media_diet: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    preferred_channels: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    platform_access: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    attention_capacity: Mapped[float] = mapped_column(Float, nullable=False)
    attention_decay_rate: Mapped[float] = mapped_column(Float, nullable=False)

    baseline_trust_priors: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    identity_tags: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    ingroup_affinities: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    outgroup_distances: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    allowed_action_classes: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    coordination_capacity: Mapped[float] = mapped_column(Float, nullable=False)
    mobilization_capacity: Mapped[float] = mapped_column(Float, nullable=False)
    legal_or_status_risk_sensitivity: Mapped[float] = mapped_column(Float, nullable=False)

    min_split_population: Mapped[int] = mapped_column(Integer, nullable=False)
    min_split_share: Mapped[float] = mapped_column(Float, nullable=False)
    max_child_cohorts: Mapped[int] = mapped_column(Integer, nullable=False)

    def to_schema(self) -> PopulationArchetype:
        from backend.app.schemas.actors import PopulationArchetype

        return PopulationArchetype(
            archetype_id=self.archetype_id,
            label=self.label,
            description=self.description,
            population_total=self.population_total,
            geography=dict(self.geography or {}),
            age_band=self.age_band,
            education_profile=self.education_profile,
            occupation_or_role=self.occupation_or_role,
            socioeconomic_band=self.socioeconomic_band,
            institution_membership=list(self.institution_membership or []),
            demographic_tags=list(self.demographic_tags or []),
            issue_exposure=self.issue_exposure,
            material_stake=self.material_stake,
            symbolic_stake=self.symbolic_stake,
            vulnerability_to_policy=self.vulnerability_to_policy,
            ability_to_influence_outcome=self.ability_to_influence_outcome,
            ideology_axes=dict(self.ideology_axes or {}),
            value_priors=dict(self.value_priors or {}),
            behavior_axes=dict(self.behavior_axes or {}),
            baseline_media_diet=dict(self.baseline_media_diet or {}),
            preferred_channels=list(self.preferred_channels or []),
            platform_access=dict(self.platform_access or {}),
            attention_capacity=self.attention_capacity,
            attention_decay_rate=self.attention_decay_rate,
            baseline_trust_priors=dict(self.baseline_trust_priors or {}),
            identity_tags=list(self.identity_tags or []),
            ingroup_affinities=dict(self.ingroup_affinities or {}),
            outgroup_distances=dict(self.outgroup_distances or {}),
            allowed_action_classes=list(self.allowed_action_classes or []),
            coordination_capacity=self.coordination_capacity,
            mobilization_capacity=self.mobilization_capacity,
            legal_or_status_risk_sensitivity=self.legal_or_status_risk_sensitivity,
            min_split_population=self.min_split_population,
            min_split_share=self.min_split_share,
            max_child_cohorts=self.max_child_cohorts,
        )

    @classmethod
    def from_schema(cls, s: PopulationArchetype, big_bang_id: str) -> PopulationArchetypeModel:
        return cls(
            archetype_id=s.archetype_id,
            big_bang_id=big_bang_id,
            label=s.label,
            description=s.description,
            population_total=s.population_total,
            geography=dict(s.geography),
            age_band=s.age_band,
            education_profile=s.education_profile,
            occupation_or_role=s.occupation_or_role,
            socioeconomic_band=s.socioeconomic_band,
            institution_membership=list(s.institution_membership),
            demographic_tags=list(s.demographic_tags),
            issue_exposure=s.issue_exposure,
            material_stake=s.material_stake,
            symbolic_stake=s.symbolic_stake,
            vulnerability_to_policy=s.vulnerability_to_policy,
            ability_to_influence_outcome=s.ability_to_influence_outcome,
            ideology_axes=dict(s.ideology_axes),
            value_priors=dict(s.value_priors),
            behavior_axes=dict(s.behavior_axes),
            baseline_media_diet=dict(s.baseline_media_diet),
            preferred_channels=list(s.preferred_channels),
            platform_access=dict(s.platform_access),
            attention_capacity=s.attention_capacity,
            attention_decay_rate=s.attention_decay_rate,
            baseline_trust_priors=dict(s.baseline_trust_priors),
            identity_tags=list(s.identity_tags),
            ingroup_affinities=dict(s.ingroup_affinities),
            outgroup_distances=dict(s.outgroup_distances),
            allowed_action_classes=list(s.allowed_action_classes),
            coordination_capacity=s.coordination_capacity,
            mobilization_capacity=s.mobilization_capacity,
            legal_or_status_risk_sensitivity=s.legal_or_status_risk_sensitivity,
            min_split_population=s.min_split_population,
            min_split_share=s.min_split_share,
            max_child_cohorts=s.max_child_cohorts,
        )


# ---------------------------------------------------------------------------
# CohortStateModel
# ---------------------------------------------------------------------------

class CohortStateModel(Base):
    __tablename__ = "cohort_states"

    cohort_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tick: Mapped[int] = mapped_column(Integer, primary_key=True)

    universe_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("universes.universe_id", ondelete="CASCADE"),
        nullable=False,
    )
    archetype_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("population_archetypes.archetype_id", ondelete="RESTRICT"),
        nullable=False,
    )
    parent_cohort_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("cohort_states.cohort_id", ondelete="SET NULL"),
        nullable=True,
    )
    child_cohort_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )

    represented_population: Mapped[int] = mapped_column(Integer, nullable=False)
    population_share_of_archetype: Mapped[float] = mapped_column(Float, nullable=False)

    issue_stance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    expression_level: Mapped[float] = mapped_column(Float, nullable=False)
    mobilization_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    speech_mode: Mapped[str] = mapped_column(String(32), nullable=False)

    emotions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    behavior_state: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    attention: Mapped[float] = mapped_column(Float, nullable=False)
    fatigue: Mapped[float] = mapped_column(Float, nullable=False)
    grievance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    perceived_efficacy: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    perceived_majority: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    fear_of_isolation: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    willingness_to_speak: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    identity_salience: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)

    visible_trust_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    exposure_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    dependency_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    memory_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    recent_post_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    queued_event_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    previous_action_ids: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )

    prompt_temperature: Mapped[float] = mapped_column(Float, nullable=False)
    representation_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    allowed_tools: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        CheckConstraint("represented_population >= 0", name="represented_population_nonneg"),
        CheckConstraint(
            "population_share_of_archetype BETWEEN 0 AND 1",
            name="population_share_range",
        ),
        Index("ix_cohort_states_universe_tick", "universe_id", "tick"),
        Index("ix_cohort_states_archetype_tick", "archetype_id", "tick"),
        Index(
            "ix_cohort_states_universe_active",
            "universe_id",
            postgresql_where="is_active = true",
        ),
    )

    def to_schema(self) -> CohortState:
        from backend.app.schemas.actors import CohortState

        return CohortState(
            cohort_id=self.cohort_id,
            universe_id=self.universe_id,
            tick=self.tick,
            archetype_id=self.archetype_id,
            parent_cohort_id=self.parent_cohort_id,
            child_cohort_ids=list(self.child_cohort_ids or []),
            represented_population=self.represented_population,
            population_share_of_archetype=self.population_share_of_archetype,
            issue_stance=dict(self.issue_stance or {}),
            expression_level=self.expression_level,
            mobilization_mode=self.mobilization_mode,
            speech_mode=self.speech_mode,
            emotions=dict(self.emotions or {}),
            behavior_state=dict(self.behavior_state or {}),
            attention=self.attention,
            fatigue=self.fatigue,
            grievance=self.grievance,
            perceived_efficacy=self.perceived_efficacy,
            perceived_majority=dict(self.perceived_majority or {}),
            fear_of_isolation=self.fear_of_isolation,
            willingness_to_speak=self.willingness_to_speak,
            identity_salience=self.identity_salience,
            visible_trust_summary=dict(self.visible_trust_summary or {}),
            exposure_summary=dict(self.exposure_summary or {}),
            dependency_summary=dict(self.dependency_summary or {}),
            memory_session_id=self.memory_session_id,
            recent_post_ids=list(self.recent_post_ids or []),
            queued_event_ids=list(self.queued_event_ids or []),
            previous_action_ids=list(self.previous_action_ids or []),
            prompt_temperature=self.prompt_temperature,
            representation_mode=self.representation_mode,  # type: ignore[arg-type]
            allowed_tools=list(self.allowed_tools or []),
            is_active=self.is_active,
        )

    @classmethod
    def from_schema(cls, s: CohortState) -> CohortStateModel:
        return cls(
            cohort_id=s.cohort_id,
            universe_id=s.universe_id,
            tick=s.tick,
            archetype_id=s.archetype_id,
            parent_cohort_id=s.parent_cohort_id,
            child_cohort_ids=list(s.child_cohort_ids),
            represented_population=s.represented_population,
            population_share_of_archetype=s.population_share_of_archetype,
            issue_stance=dict(s.issue_stance),
            expression_level=s.expression_level,
            mobilization_mode=s.mobilization_mode,
            speech_mode=s.speech_mode,
            emotions=dict(s.emotions),
            behavior_state=dict(s.behavior_state),
            attention=s.attention,
            fatigue=s.fatigue,
            grievance=s.grievance,
            perceived_efficacy=s.perceived_efficacy,
            perceived_majority=dict(s.perceived_majority),
            fear_of_isolation=s.fear_of_isolation,
            willingness_to_speak=s.willingness_to_speak,
            identity_salience=s.identity_salience,
            visible_trust_summary=dict(s.visible_trust_summary),
            exposure_summary=dict(s.exposure_summary),
            dependency_summary=dict(s.dependency_summary),
            memory_session_id=s.memory_session_id,
            recent_post_ids=list(s.recent_post_ids),
            queued_event_ids=list(s.queued_event_ids),
            previous_action_ids=list(s.previous_action_ids),
            prompt_temperature=s.prompt_temperature,
            representation_mode=s.representation_mode,
            allowed_tools=list(s.allowed_tools),
            is_active=s.is_active,
        )
