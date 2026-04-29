from __future__ import annotations

from pydantic import BaseModel, Field


class TickRuntimeState(BaseModel):
    """Per-tick execution state shared across runtime nodes."""

    run_id: str | None = None
    multiverse_id: str | None = None
    tick_id: str | None = None
    last_tick_id: str | None = None
    status: str = "pending"
    current_node: str | None = None
    current_node_kind: str | None = None
    completed_nodes: list[str] = Field(default_factory=list)
    completed_checkpoints: list[str] = Field(default_factory=list)
    pending_checkpoints: list[str] = Field(default_factory=list)
    cohort_ids: list[str] = Field(default_factory=list)
    hero_ids: list[str] = Field(default_factory=list)
    tool_call_keys: list[str] = Field(default_factory=list)
    interrupt_requested: bool = False
    staged_artifacts: dict[str, dict] = Field(default_factory=dict)
