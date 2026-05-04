"""
Memory provider base classes — Protocol + MemoryFailure exception.

MemoryFailure: raised on hard backend failures; callers catch and degrade.
MemoryProvider: structural Protocol defining the full memory adapter contract.
"""
from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable


class MemoryFailure(Exception):
    """Raised when a memory backend encounters a hard, unrecoverable failure.

    Callers should catch this, log it, mark the provider degraded, and
    continue using the local fallback.
    """


@runtime_checkable
class MemoryProvider(Protocol):
    """Structural protocol for memory backends."""

    async def ensure_user(
        self,
        *,
        actor_id: str,
        actor_kind: Literal["cohort", "hero"],
        metadata: dict,
    ) -> None:
        """Create or confirm user/actor exists in the backend (idempotent)."""
        ...

    async def ensure_session(
        self,
        *,
        actor_id: str,
        universe_id: str,
        metadata: dict,
    ) -> str:
        """Create or confirm a session/thread exists; return session_id (idempotent)."""
        ...

    async def add_episode(
        self,
        *,
        session_id: str,
        role: str,
        role_type: Literal["user", "assistant", "system"],
        content: str,
        metadata: dict | None = None,
    ) -> None:
        """Append a message/episode to the session."""
        ...

    async def get_context(
        self,
        *,
        session_id: str,
        max_tokens: int = 2000,
    ) -> str:
        """Return the assembled context string for the session."""
        ...

    async def search_graph(
        self,
        *,
        actor_id: str,
        query: str,
        top_k: int = 5,
    ) -> list[dict]:
        """Search the knowledge graph for the actor; returns ranked results."""
        ...

    async def end_of_tick_summary(
        self,
        *,
        actor_id: str,
        universe_id: str,
        tick: int,
        summary_text: str,
    ) -> None:
        """Persist a tick-level summary for the actor."""
        ...

    async def split_inheritance(
        self,
        *,
        parent_actor_id: str,
        child_actor_ids: list[str],
        split_note: str,
    ) -> None:
        """Copy parent summary memory to each child actor (cohort split)."""
        ...

    async def healthcheck(self) -> dict:
        """Return health status: {ok: bool, latency_ms: int|None, details: dict}."""
        ...
