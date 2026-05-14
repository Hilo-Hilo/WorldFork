"""
Branching schemas: BranchNode, BranchPolicy,
BranchDelta (discriminated union), BranchPolicyResult.
Import-free of backend.app.models.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from backend.app.schemas.common import UniverseStatus

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

# ---------------------------------------------------------------------------
# BranchNode
# ---------------------------------------------------------------------------

class BranchNode(BaseModel):
    """Branch point in the multiverse DAG."""

    model_config = ConfigDict(extra="forbid")

    universe_id: NonEmptyStr
    parent_universe_id: NonEmptyStr | None = None
    child_universe_ids: list[NonEmptyStr] = Field(default_factory=list)
    depth: int = Field(..., ge=0)
    branch_tick: int = Field(..., ge=0)
    branch_point_id: NonEmptyStr
    branch_trigger: NonEmptyStr
    branch_delta: dict[str, Any] = Field(default_factory=dict)
    status: UniverseStatus
    metrics_summary: dict[str, Any] = Field(default_factory=dict)
    cost_estimate: dict[str, Any] = Field(default_factory=dict)
    descendant_count: int = Field(..., ge=0)


# ---------------------------------------------------------------------------
# BranchPolicy
# ---------------------------------------------------------------------------

class BranchPolicy(BaseModel):
    """Branch explosion controls."""

    model_config = ConfigDict(extra="forbid")

    max_active_universes: int = Field(..., ge=1, le=10_000)
    max_total_branches: int = Field(..., ge=1, le=100_000)
    max_depth: int = Field(..., ge=1, le=50)
    max_branches_per_tick: int = Field(..., ge=1, le=100)
    branch_cooldown_ticks: int = Field(..., ge=0)
    min_divergence_score: float = Field(..., ge=0.0, le=1.0)
    auto_prune_low_value: bool = True


# ---------------------------------------------------------------------------
# BranchDelta discriminated union
# ---------------------------------------------------------------------------

class CounterfactualEventRewriteDelta(BaseModel):
    """Rewrite an event in the child universe."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["counterfactual_event_rewrite"]
    target_event_id: NonEmptyStr
    parent_version: NonEmptyStr
    child_version: NonEmptyStr


class ParameterShiftDelta(BaseModel):
    """Shift a parameter value in the child universe."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["parameter_shift"]
    target: NonEmptyStr
    delta: dict[str, float]


class ActorStateOverrideDelta(BaseModel):
    """Override a field on an actor's state in the child universe."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["actor_state_override"]
    actor_id: NonEmptyStr
    field: NonEmptyStr
    new_value: float | int | NonEmptyStr | dict[str, Any]

    @model_validator(mode="after")
    def _reject_conservation_critical_fields(self) -> ActorStateOverrideDelta:
        protected = {
            "actor_id",
            "archetype_id",
            "cohort_id",
            "hero_id",
            "population_share_of_archetype",
            "represented_population",
            "tick",
            "universe_id",
        }
        root_field = self.field.split(".", 1)[0]
        if root_field in protected:
            raise ValueError(
                f"actor_state_override cannot mutate conservation-critical field {self.field!r}"
            )
        return self


class HeroDecisionOverrideDelta(BaseModel):
    """Override a hero's decision at a given tick in the child universe."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["hero_decision_override"]
    hero_id: NonEmptyStr
    tick: int = Field(..., ge=0)
    new_decision: dict[str, Any]


# Annotated discriminated union — dispatch on the "type" field
BranchDelta = Annotated[
    CounterfactualEventRewriteDelta | ParameterShiftDelta | ActorStateOverrideDelta | HeroDecisionOverrideDelta,
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# BranchPolicyResult
# ---------------------------------------------------------------------------

class BranchPolicyResult(BaseModel):
    """Result returned by the branch policy checker."""

    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "downgrade_to_candidate", "reject"]
    reason: NonEmptyStr
    cost_estimate: dict[str, Any] | None = None
    divergence_score: float | None = Field(default=None, ge=0.0, le=1.0)
