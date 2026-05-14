"""
Event schema.
Import-free of backend.app.models.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from backend.app.schemas.common import EventStatus

_VALID_VISIBILITY = {"public", "private", "institution", "cohort", "invite"}
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Event(BaseModel):
    """A scheduled or realized world action."""

    model_config = ConfigDict(extra="forbid")

    event_id: NonEmptyStr
    universe_id: NonEmptyStr
    created_tick: int = Field(..., ge=0)
    scheduled_tick: int = Field(..., ge=0)
    duration_ticks: int | None = Field(default=None)

    event_type: NonEmptyStr
    title: NonEmptyStr
    description: NonEmptyStr

    created_by_actor_id: NonEmptyStr
    participants: list[NonEmptyStr] = Field(default_factory=list)
    target_audience: list[NonEmptyStr] = Field(default_factory=list)

    visibility: str
    preconditions: list[dict[str, Any]] = Field(default_factory=list)
    expected_effects: dict[str, Any] = Field(default_factory=dict)
    actual_effects: dict[str, Any] | None = None
    risk_level: float = Field(..., ge=0.0, le=1.0)

    status: EventStatus
    parent_event_id: NonEmptyStr | None = None
    source_llm_call_id: NonEmptyStr | None = None

    @field_validator("visibility")
    @classmethod
    def _validate_visibility(cls, v: str) -> str:
        if v not in _VALID_VISIBILITY:
            raise ValueError(
                f"visibility must be one of {sorted(_VALID_VISIBILITY)}, got {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _validate_tick_order_and_duration(self) -> Event:
        if self.scheduled_tick < self.created_tick:
            raise ValueError(
                f"scheduled_tick ({self.scheduled_tick}) must be >= "
                f"created_tick ({self.created_tick})"
            )
        if self.duration_ticks is not None and self.duration_ticks <= 0:
            raise ValueError(
                f"duration_ticks must be > 0, got {self.duration_ticks}"
            )
        return self
