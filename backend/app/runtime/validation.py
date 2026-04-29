from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ValidationResult(BaseModel):
    """Normalized validation output for one runtime node."""

    ok: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    payload: dict[str, Any] | None = None


def validate_node_output(node_kind: str, payload: dict[str, Any]) -> ValidationResult:
    """Apply the runtime's cleanliness/completeness checks for checkpoint output."""

    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(payload, dict):
        return ValidationResult(ok=False, errors=["node output must be a JSON object"], payload=None)

    if node_kind in {"cohort_decision", "hero_decision"}:
        if not isinstance(payload.get("actor_output"), dict):
            errors.append("actor_output is required")
        if not isinstance(payload.get("parsed_actions"), list):
            errors.append("parsed_actions must be a list")
        if not isinstance(payload.get("emotion_self_ratings"), list):
            errors.append("emotion_self_ratings must be a list")
    elif node_kind == "god_review":
        review = payload.get("review_payload")
        if not isinstance(review, dict):
            errors.append("review_payload is required")
        elif not isinstance(review.get("tool_calls"), list):
            errors.append("review_payload.tool_calls must be a list")
    elif node_kind == "tool_call":
        if not payload.get("tool_name"):
            errors.append("tool_name is required")
        if payload.get("status") not in {"succeeded", "failed"}:
            errors.append("tool call status must be succeeded or failed")
    elif node_kind == "tick_summary":
        if not isinstance(payload.get("final_bundle"), dict):
            errors.append("final_bundle is required")

    if not payload:
        warnings.append("node output is empty")
    return ValidationResult(ok=not errors, errors=errors, warnings=warnings, payload=payload)
