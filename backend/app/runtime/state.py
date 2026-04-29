from __future__ import annotations

from pydantic import BaseModel, Field

from app.runtime.enums import NodeKind


class TickRuntimeState(BaseModel):
    """Per-tick execution state shared across runtime nodes."""

    run_id: str | None = None
    multiverse_id: str | None = None
    tick_id: str | None = None
    status: str = "pending"
    current_node: NodeKind | None = None
    completed_nodes: list[NodeKind] = Field(default_factory=list)
    pending_checkpoints: list[str] = Field(default_factory=list)
