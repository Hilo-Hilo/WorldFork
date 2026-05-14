"""
Common types, literals, and small utility models used across all schemas.
Import-free of backend.app.models — models import schemas, never the reverse.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Literal type aliases
# ---------------------------------------------------------------------------

RepresentationMode = Literal["micro", "small", "population", "mass"]

RunStatus = Literal[
    "draft", "initializing", "active", "running", "paused", "completed", "failed", "archived"
]

UniverseStatus = Literal[
    "candidate", "active", "frozen", "killed", "completed", "terminated", "merged"
]

EventStatus = Literal[
    "proposed",
    "queued",
    "scheduled",
    "active",
    "executed",
    "completed",
    "cancelled",
    "failed",
    "invalidated",
]


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------

class Clock(BaseModel):
    """
    Temporal context for a simulation tick.
    Provides the example prompt-block format.
    """

    model_config = ConfigDict(extra="forbid")

    current_tick: int = Field(..., ge=0)
    tick_duration_minutes: int = Field(..., gt=0)
    elapsed_minutes: int = Field(..., ge=0)
    previous_tick_minutes: int | None = Field(default=None, ge=0)
    max_schedule_horizon_ticks: int = Field(..., gt=0)

    def as_prompt_block(self) -> str:
        """Produce the clock-context text block."""
        elapsed_hours = self.elapsed_minutes / 60
        tick_hours = self.tick_duration_minutes / 60
        horizon_minutes = self.max_schedule_horizon_ticks * self.tick_duration_minutes
        horizon_hours = horizon_minutes / 60

        prev_line = ""
        if self.previous_tick_minutes is not None:
            prev_line = (
                f"\nTime since previous tick: {self.previous_tick_minutes} minutes"
                f" / {self.previous_tick_minutes / 60:.1f} hours"
            )

        return (
            f"Current tick: {self.current_tick}\n"
            f"Tick duration: {self.tick_duration_minutes} minutes"
            f" / {tick_hours:.1f} hours\n"
            f"Elapsed since Big Bang: {self.elapsed_minutes} minutes"
            f" / {elapsed_hours:.1f} hours"
            f"{prev_line}\n"
            f"You may schedule events up to {self.max_schedule_horizon_ticks} ticks"
            f" / {horizon_hours:.1f} hours into the future."
        )


# ---------------------------------------------------------------------------
# IdempotencyKey
# ---------------------------------------------------------------------------

_IDEMPOTENCY_KEY_RE = re.compile(r"^[a-zA-Z0-9_:\-\.]{1,256}$")


class IdempotencyKey(BaseModel):
    """A validated idempotency key (alphanumeric + _ : - . , max 256 chars)."""

    model_config = ConfigDict(extra="forbid")

    key: str

    @field_validator("key")
    @classmethod
    def _validate_key(cls, v: str) -> str:
        if not _IDEMPOTENCY_KEY_RE.match(v):
            raise ValueError(
                "idempotency_key must be 1–256 chars, "
                "characters: a-z A-Z 0-9 _ : - ."
            )
        return v


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------

def clamp01(value: float) -> float:
    """Clamp a float to [0, 1]."""
    return max(0.0, min(1.0, float(value)))


def clamp_emotion(value: float) -> float:
    """Clamp an emotion intensity to [0, 10]."""
    return max(0.0, min(10.0, float(value)))
