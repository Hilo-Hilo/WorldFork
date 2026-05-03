"""
Branching schemas: BranchNode (§9.9), BranchPolicy (§13.5),
BranchDelta (discriminated union), BranchPolicyResult.
Import-free of backend.app.models.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.schemas.common import UniverseStatus

# ---------------------------------------------------------------------------
# BranchNode  §9.9
# ---------------------------------------------------------------------------

class BranchNode(BaseModel):
    """Branch point in the multiverse DAG — §9.9 verbatim."""

    model_config = ConfigDict(extra="forbid")

    universe_id: str
    parent_universe_id: str | None = None
    child_universe_ids: list[str] = Field(default_factory=list)
    depth: int = Field(..., ge=0)
    branch_tick: int = Field(..., ge=0)
    branch_point_id: str
    branch_trigger: str
    branch_delta: dict[str, Any] = Field(default_factory=dict)
    status: UniverseStatus
    metrics_summary: dict[str, Any] = Field(default_factory=dict)
    cost_estimate: dict[str, Any] = Field(default_factory=dict)
    descendant_count: int = Field(..., ge=0)


# ---------------------------------------------------------------------------
# BranchPolicy  §13.5
# ---------------------------------------------------------------------------

class BranchPolicy(BaseModel):
    """Branch explosion controls — §13.5 verbatim keys."""

    model_config = ConfigDict(extra="forbid")

    max_active_universes: int = Field(..., ge=1, le=10_000)
    max_total_branches: int = Field(..., ge=1, le=100_000)
    max_depth: int = Field(..., ge=1, le=50)
    max_branches_per_tick: int = Field(..., ge=1, le=100)
    branch_cooldown_ticks: int = Field(..., ge=0)
    min_divergence_score: float = Field(..., ge=0.0, le=1.0)
    auto_prune_low_value: bool = True


# ---------------------------------------------------------------------------
# BranchDelta discriminated union  §13.3
# ---------------------------------------------------------------------------

class CounterfactualEventRewriteDelta(BaseModel):
    """Rewrite an event in the child universe."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["counterfactual_event_rewrite"]
    target_event_id: str
    parent_version: str
    child_version: str


class ParameterShiftDelta(BaseModel):
    """Shift a parameter value in the child universe."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["parameter_shift"]
    target: str
    delta: dict[str, float]


class ActorStateOverrideDelta(BaseModel):
    """Override a field on an actor's state in the child universe."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["actor_state_override"]
    actor_id: str
    field: str
    new_value: float | int | str | dict[str, Any]

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
    hero_id: str
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
    reason: str
    cost_estimate: dict[str, Any] | None = None
    divergence_score: float | None = Field(default=None, ge=0.0, le=1.0)
