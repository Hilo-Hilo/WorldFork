from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ValidationResult(BaseModel):
    """Normalized validation output for one runtime node."""

    ok: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    payload: dict[str, Any] | None = None
