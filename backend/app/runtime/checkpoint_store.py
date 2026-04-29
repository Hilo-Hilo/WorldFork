from __future__ import annotations

from typing import Protocol

from app.runtime.state import TickRuntimeState


class CheckpointStore(Protocol):
    """Persistence contract for per-tick runtime checkpoints."""

    def save(self, state: TickRuntimeState) -> None: ...

    def load(self, checkpoint_id: str) -> TickRuntimeState | None: ...
