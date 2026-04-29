"""
LocalMemoryProvider — in-process, dict-backed memory fallback.

Keeps per-session deques of messages (max 200 entries) and a per-actor
summary blob.  All state is in-memory for the lifetime of the process;
it degrades gracefully when Zep is unavailable.

No external dependencies beyond stdlib.
"""
from __future__ import annotations

from collections import deque
from typing import Literal


class LocalMemoryProvider:
    """Pure in-memory fallback memory provider.

    Thread-safety: suitable for a single-process asyncio event loop.
    For multi-process workers, each process has its own in-memory store;
    the run ledger is the authoritative cross-process source.
    """

    _MAX_EPISODE_DEQUE = 200
    _CONTEXT_RECENT_N = 20  # messages to include in get_context

    def __init__(self) -> None:
        # session_id -> deque of {"role", "role_type", "content", "metadata"}
        self._episodes: dict[str, deque[dict]] = {}
        # actor_id -> summary blob string
        self._summaries: dict[str, str] = {}
        # actor_id -> set of session_ids (for idempotency)
        self._actor_sessions: dict[str, set[str]] = {}
        # known users (actor_ids)
        self._users: set[str] = set()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_deque(self, session_id: str) -> deque[dict]:
        if session_id not in self._episodes:
            self._episodes[session_id] = deque(maxlen=self._MAX_EPISODE_DEQUE)
        return self._episodes[session_id]

    def register_session(self, *, actor_id: str, session_id: str) -> None:
        """Associate an externally-created session id with an actor."""
        self._get_deque(session_id)
        actor_sessions = self._actor_sessions.setdefault(actor_id, set())
        actor_sessions.add(session_id)

    # ------------------------------------------------------------------
    # Protocol implementation
    # ------------------------------------------------------------------

    async def ensure_user(
        self,
        *,
        actor_id: str,
        actor_kind: Literal["cohort", "hero"],
        metadata: dict,
    ) -> None:
        """Idempotent — just records the actor_id."""
        self._users.add(actor_id)

    async def ensure_session(
        self,
        *,
        actor_id: str,
        universe_id: str,
        metadata: dict,
    ) -> str:
        """Return a deterministic session_id and create the deque if absent."""
        session_id = f"local:{universe_id}:{actor_id}"
        self.register_session(actor_id=actor_id, session_id=session_id)
        return session_id

    async def add_episode(
        self,
        *,
        session_id: str,
        role: str,
        role_type: Literal["user", "assistant", "system"],
        content: str,
        metadata: dict | None = None,
    ) -> None:
        """Append a message to the per-session deque (max 200 entries)."""
        dq = self._get_deque(session_id)
        dq.append(
            {
                "role": role,
                "role_type": role_type,
                "content": content,
                "metadata": metadata or {},
            }
        )

    async def get_context(
        self,
        *,
        session_id: str,
        max_tokens: int = 2000,
    ) -> str:
        """Return the most-recent N messages concatenated as a plain string."""
        dq = self._get_deque(session_id)
        recent = list(dq)[-self._CONTEXT_RECENT_N :]
        parts: list[str] = []
        for entry in recent:
            parts.append(f"[{entry['role']}] {entry['content']}")
        return "\n".join(parts)

    async def search_graph(
        self,
        *,
        actor_id: str,
        query: str,
        top_k: int = 5,
    ) -> list[dict]:
        """Simple keyword search over stored episodes (no embedding).

        Each word in the query is checked independently; an episode matches
        if any query token appears in its content.
        """
        results: list[dict] = []
        query_tokens = [t for t in query.lower().split() if t]
        if not query_tokens:
            return results
        actor_session_ids = self._actor_sessions.get(actor_id, set())
        for session_id in actor_session_ids:
            dq = self._episodes.get(session_id)
            if dq is None:
                continue
            for entry in dq:
                content_lower = entry["content"].lower()
                if any(token in content_lower for token in query_tokens):
                    results.append(
                        {
                            "session_id": session_id,
                            "content": entry["content"],
                            "role": entry["role"],
                        }
                    )
                    if len(results) >= top_k:
                        return results
        return results

    async def end_of_tick_summary(
        self,
        *,
        actor_id: str,
        universe_id: str,
        tick: int,
        summary_text: str,
    ) -> None:
        """Overwrite the per-actor summary blob."""
        self._summaries[actor_id] = summary_text

    async def split_inheritance(
        self,
        *,
        parent_actor_id: str,
        child_actor_ids: list[str],
        split_note: str,
    ) -> None:
        """Copy parent summary to each child's slot."""
        parent_summary = self._summaries.get(parent_actor_id, "")
        inheritance_text = (
            f"Inherited from {parent_actor_id}: {parent_summary}\n{split_note}"
            if parent_summary
            else split_note
        )
        for child_id in child_actor_ids:
            self._summaries[child_id] = inheritance_text

    async def healthcheck(self) -> dict:
        """Always healthy for the local provider."""
        return {
            "ok": True,
            "latency_ms": 0,
            "details": {
                "provider": "local",
                "users": len(self._users),
                "sessions": len(self._episodes),
                "summaries": len(self._summaries),
            },
        }
