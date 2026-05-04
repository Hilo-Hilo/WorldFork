from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LLMRequest(BaseModel):
    purpose: str
    model: str
    messages: list[dict[str, Any]]
    json_schema: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LLMResponse(BaseModel):
    content: str
    parsed: dict[str, Any] | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
