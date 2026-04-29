from __future__ import annotations

from pydantic import BaseModel, Field


class RetryPolicy(BaseModel):
    """Retry knobs for one runtime node attempt."""

    max_attempts: int = Field(default=1, ge=1)
    backoff_seconds: float = Field(default=0.0, ge=0.0)
    escalate_after_attempts: int | None = Field(default=None, ge=1)


class RepairPolicy(BaseModel):
    """Repair prompt / fallback knobs for one runtime node."""

    enabled: bool = True
    max_repairs: int = Field(default=1, ge=0)
    strategy: str = "repair_prompt"
