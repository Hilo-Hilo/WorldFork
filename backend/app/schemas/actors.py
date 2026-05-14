"""
Actor schemas: PopulationArchetype, CohortState,
HeroArchetype, HeroState.
Import-free of backend.app.models.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from backend.app.schemas.common import (
    RepresentationMode,
    clamp01,
    clamp_emotion,
)

_log = logging.getLogger(__name__)

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

# Valid mobilization modes
_MOBILIZATION_MODES = {"dormant", "murmur", "organize", "mobilize", "escalate"}

# Valid speech modes
_SPEECH_MODES = {"silent", "private", "semi_public", "public", "loud"}

# representation mode population thresholds
_REPRESENTATION_MODE_TABLE = {
    "micro": (2, 25),        # 2–25 (exclusive hero range)
    "small": (25, 250),      # 25–250
    "population": (250, 5000),  # 250–5,000
    "mass": (5000, None),    # 5,000+
}


# ---------------------------------------------------------------------------
# PopulationArchetype
# ---------------------------------------------------------------------------

class PopulationArchetype(BaseModel):
    """Mostly stable group identity over the simulation horizon."""

    model_config = ConfigDict(extra="forbid")

    archetype_id: NonEmptyStr
    label: NonEmptyStr
    description: NonEmptyStr

    population_total: int = Field(..., gt=0)
    geography: dict[str, Any] = Field(default_factory=dict)
    age_band: NonEmptyStr | None = None
    education_profile: NonEmptyStr | None = None
    occupation_or_role: NonEmptyStr | None = None
    socioeconomic_band: NonEmptyStr | None = None
    institution_membership: list[NonEmptyStr] = Field(default_factory=list)
    demographic_tags: list[NonEmptyStr] = Field(default_factory=list)

    # All numeric weights default to neutral 0.5 so the LLM may omit them.
    # Engine code reads these via .get-style helpers, so 0.5 is a safe baseline.
    issue_exposure: float = Field(0.5, ge=0.0, le=1.0)
    material_stake: float = Field(0.5, ge=0.0, le=1.0)
    symbolic_stake: float = Field(0.5, ge=0.0, le=1.0)
    vulnerability_to_policy: float = Field(0.5, ge=0.0, le=1.0)
    ability_to_influence_outcome: float = Field(0.5, ge=0.0, le=1.0)

    ideology_axes: dict[str, float] = Field(default_factory=dict)
    value_priors: dict[str, float] = Field(default_factory=dict)
    behavior_axes: dict[str, float] = Field(default_factory=dict)

    baseline_media_diet: dict[str, float] = Field(default_factory=dict)
    preferred_channels: list[NonEmptyStr] = Field(default_factory=list)
    platform_access: dict[str, float] = Field(default_factory=dict)
    attention_capacity: float = Field(0.6, ge=0.0, le=1.0)
    attention_decay_rate: float = Field(0.18, ge=0.0, le=1.0)

    baseline_trust_priors: dict[str, float] = Field(default_factory=dict)
    identity_tags: list[NonEmptyStr] = Field(default_factory=list)
    ingroup_affinities: dict[str, float] = Field(default_factory=dict)
    outgroup_distances: dict[str, float] = Field(default_factory=dict)

    allowed_action_classes: list[NonEmptyStr] = Field(default_factory=list)
    coordination_capacity: float = Field(0.4, ge=0.0, le=1.0)
    mobilization_capacity: float = Field(0.4, ge=0.0, le=1.0)
    legal_or_status_risk_sensitivity: float = Field(0.4, ge=0.0, le=1.0)

    min_split_population: int = Field(25, gt=0)
    min_split_share: float = Field(0.10, ge=0.0, le=1.0)
    max_child_cohorts: int = Field(4, gt=1)


# ---------------------------------------------------------------------------
# CohortState
# ---------------------------------------------------------------------------

class CohortState(BaseModel):
    """
    Mutable population slice inside an archetype.

    LLM noise tolerance: emotions and numeric state fields are clamped
    (not rejected) via model_validator. Violations are logged as warnings
    via the Pydantic validation context if provided, otherwise to the
    module logger.
    """

    model_config = ConfigDict(extra="forbid")

    cohort_id: NonEmptyStr
    universe_id: NonEmptyStr
    tick: int = Field(..., ge=0)
    archetype_id: NonEmptyStr
    parent_cohort_id: NonEmptyStr | None = None
    child_cohort_ids: list[NonEmptyStr] = Field(default_factory=list)

    represented_population: int = Field(..., ge=0)
    population_share_of_archetype: float = Field(..., ge=0.0, le=1.0)

    issue_stance: dict[str, float] = Field(default_factory=dict)
    expression_level: float = Field(..., ge=0.0, le=1.0)
    mobilization_mode: str
    speech_mode: str

    emotions: dict[str, float] = Field(default_factory=dict)
    behavior_state: dict[str, float] = Field(default_factory=dict)
    attention: float = Field(..., ge=0.0, le=1.0)
    fatigue: float = Field(..., ge=0.0, le=1.0)
    grievance: float = Field(default=0.0)
    perceived_efficacy: float = Field(default=0.5)
    perceived_majority: dict[str, float] = Field(default_factory=dict)
    fear_of_isolation: float = Field(default=0.0)
    willingness_to_speak: float = Field(default=0.5)
    identity_salience: float = Field(default=0.5)

    visible_trust_summary: dict[str, Any] = Field(default_factory=dict)
    exposure_summary: dict[str, Any] = Field(default_factory=dict)
    dependency_summary: dict[str, Any] = Field(default_factory=dict)

    memory_session_id: NonEmptyStr | None = None
    recent_post_ids: list[NonEmptyStr] = Field(default_factory=list)
    queued_event_ids: list[NonEmptyStr] = Field(default_factory=list)
    previous_action_ids: list[NonEmptyStr] = Field(default_factory=list)

    prompt_temperature: float = Field(..., ge=0.0, le=2.0)
    representation_mode: RepresentationMode
    allowed_tools: list[NonEmptyStr] = Field(default_factory=list)
    is_active: bool = True

    @field_validator("mobilization_mode")
    @classmethod
    def _validate_mobilization_mode(cls, v: str) -> str:
        if v not in _MOBILIZATION_MODES:
            raise ValueError(
                f"mobilization_mode must be one of {sorted(_MOBILIZATION_MODES)}, got {v!r}"
            )
        return v

    @field_validator("speech_mode")
    @classmethod
    def _validate_speech_mode(cls, v: str) -> str:
        if v not in _SPEECH_MODES:
            raise ValueError(
                f"speech_mode must be one of {sorted(_SPEECH_MODES)}, got {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _clamp_and_validate(self) -> CohortState:
        """
        Clamp emotions [0,10], behavior_state [0,1], expression_level [0,1].
        Warns rather than rejects so LLM noise doesn't break the pipeline.
        Also validates representation_mode vs represented_population.
        """
        # Clamp emotions
        clamped_emotions: dict[str, float] = {}
        for k, v in self.emotions.items():
            clamped = clamp_emotion(v)
            if clamped != v:
                _log.warning(
                    "CohortState %s: emotion %r clamped from %.4f to %.4f",
                    self.cohort_id, k, v, clamped,
                )
            clamped_emotions[k] = clamped
        self.emotions = clamped_emotions

        # Clamp behavior_state [0,1]
        clamped_behavior: dict[str, float] = {}
        for k, v in self.behavior_state.items():
            clamped = clamp01(v)
            if clamped != v:
                _log.warning(
                    "CohortState %s: behavior_state %r clamped from %.4f to %.4f",
                    self.cohort_id, k, v, clamped,
                )
            clamped_behavior[k] = clamped
        self.behavior_state = clamped_behavior

        # Clamp expression_level [0,1]
        clamped_expr = clamp01(self.expression_level)
        if clamped_expr != self.expression_level:
            _log.warning(
                "CohortState %s: expression_level clamped from %.4f to %.4f",
                self.cohort_id, self.expression_level, clamped_expr,
            )
            self.expression_level = clamped_expr

        # representation_mode alignment with represented_population
        pop = self.represented_population
        mode = self.representation_mode
        if mode == "micro" and not (2 <= pop <= 25):
            _log.warning(
                "CohortState %s: representation_mode='micro' but population=%d "
                "(expected 2–25)", self.cohort_id, pop,
            )
        elif mode == "small" and not (25 <= pop <= 250):
            _log.warning(
                "CohortState %s: representation_mode='small' but population=%d "
                "(expected 25–250)", self.cohort_id, pop,
            )
        elif mode == "population" and not (250 <= pop <= 5000):
            _log.warning(
                "CohortState %s: representation_mode='population' but population=%d "
                "(expected 250–5000)", self.cohort_id, pop,
            )
        elif mode == "mass" and pop < 5000:
            _log.warning(
                "CohortState %s: representation_mode='mass' but population=%d "
                "(expected >=5000)", self.cohort_id, pop,
            )

        return self


# ---------------------------------------------------------------------------
# HeroArchetype
# ---------------------------------------------------------------------------

_HERO_POWER_RANGE = (0.0, 1.0)


class HeroArchetype(BaseModel):
    """High-impact individual archetype."""

    model_config = ConfigDict(extra="forbid")

    hero_id: NonEmptyStr
    label: NonEmptyStr
    description: NonEmptyStr
    role: NonEmptyStr
    institution: NonEmptyStr | None = None
    location_scope: NonEmptyStr = "city"

    # All hero numeric weights default to 0.5 so the LLM may omit them.
    public_reach: float = Field(0.5, ge=0.0, le=1.0)
    institutional_power: float = Field(0.5, ge=0.0, le=1.0)
    financial_power: float = Field(0.5, ge=0.0, le=1.0)
    agenda_control: float = Field(0.5, ge=0.0, le=1.0)
    media_access: float = Field(0.5, ge=0.0, le=1.0)

    ideology_axes: dict[str, float] = Field(default_factory=dict)
    value_priors: dict[str, float] = Field(default_factory=dict)
    trust_priors: dict[str, float] = Field(default_factory=dict)
    behavioral_axes: dict[str, float] = Field(default_factory=dict)

    volatility: float = Field(0.5, ge=0.0, le=1.0)
    ego_sensitivity: float = Field(0.5, ge=0.0, le=1.0)
    strategic_discipline: float = Field(0.5, ge=0.0, le=1.0)
    controversy_tolerance: float = Field(0.5, ge=0.0, le=1.0)
    direct_event_power: float = Field(0.5, ge=0.0, le=1.0)

    scheduling_permissions: list[NonEmptyStr] = Field(default_factory=list)
    allowed_channels: list[NonEmptyStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_axes_ranges(self) -> HeroArchetype:
        """All ideology_axes in [-1,1]; behavioral_axes in [0,1]."""
        for k, v in self.ideology_axes.items():
            if not (-1.0 <= v <= 1.0):
                raise ValueError(
                    f"ideology_axes[{k!r}]={v} is outside [-1, 1]"
                )
        for k, v in self.behavioral_axes.items():
            if not (0.0 <= v <= 1.0):
                raise ValueError(
                    f"behavioral_axes[{k!r}]={v} is outside [0, 1]"
                )
        return self


# ---------------------------------------------------------------------------
# HeroState
# ---------------------------------------------------------------------------

class HeroState(BaseModel):
    """Per-tick mutable hero state."""

    model_config = ConfigDict(extra="forbid")

    hero_id: NonEmptyStr
    universe_id: NonEmptyStr
    tick: int = Field(..., ge=0)
    current_emotions: dict[str, float] = Field(default_factory=dict)
    current_issue_stances: dict[str, float] = Field(default_factory=dict)
    attention: float = Field(..., ge=0.0, le=1.0)
    fatigue: float = Field(..., ge=0.0, le=1.0)
    perceived_pressure: float = Field(..., ge=0.0, le=1.0)
    current_strategy: str = ""
    queued_events: list[NonEmptyStr] = Field(default_factory=list)
    recent_posts: list[NonEmptyStr] = Field(default_factory=list)
    memory_session_id: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _clamp_emotions(self) -> HeroState:
        """Clamp emotions [0,10]; warn on out-of-range values."""
        clamped: dict[str, float] = {}
        for k, v in self.current_emotions.items():
            c = clamp_emotion(v)
            if c != v:
                _log.warning(
                    "HeroState %s tick %d: emotion %r clamped from %.4f to %.4f",
                    self.hero_id, self.tick, k, v, c,
                )
            clamped[k] = c
        self.current_emotions = clamped
        return self
