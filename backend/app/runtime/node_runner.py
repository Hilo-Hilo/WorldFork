from __future__ import annotations

from dataclasses import dataclass

from app.runtime.enums import NodeKind


@dataclass(frozen=True, slots=True)
class TickNodeSpec:
    key: str
    kind: NodeKind
    checkpoint: bool = False
    actor_id: str | None = None
    upstream: tuple[str, ...] = ()
    downstream: tuple[str, ...] = ()
