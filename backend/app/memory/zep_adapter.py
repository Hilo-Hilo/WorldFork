"""
ZepMemoryProvider — Zep Cloud v2 memory adapter.

SDK surface (zep-cloud >= 2.0):
  - client.user.add(user_id=..., metadata=...)
  - client.thread.create(thread_id=..., user_id=...)
  - client.thread.add_messages(thread_id, messages=[Message(...)])
  - client.thread.get_user_context(thread_id) -> ThreadContextResponse
  - client.graph.search(query=..., user_id=..., limit=...) -> GraphSearchResults

Key quirk discovered: the v2 SDK uses Thread API, not the legacy Session/Memory API.
`client.thread.get_user_context` returns the assembled context from the knowledge graph.

Degradation: tracks failures in a rolling 60-second window; after 3 failures
routes all calls to local_fallback and sets self.degraded = True.
Call reset_degraded() to re-probe after a successful healthcheck.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Literal

import structlog

from backend.app.memory.base import MemoryFailure  # noqa: F401 (re-exported convenience)
from backend.app.memory.local import LocalMemoryProvider

# Module-level import so tests can patch zep_adapter.AsyncZep
try:
    from zep_cloud.client import AsyncZep
    from zep_cloud.types import Message as ZepMessage
except ImportError:  # pragma: no cover — only happens in environments without SDK
    AsyncZep = None  # type: ignore[assignment, misc]
    ZepMessage = None  # type: ignore[assignment]

log: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)

_FAILURE_WINDOW_SECONDS = 60
_FAILURE_THRESHOLD = 3


class ZepMemoryProvider:
    """Zep Cloud v2 memory adapter with automatic local fallback on degradation."""

    def __init__(
        self,
        api_key: str,
        mode: str = "cohort_memory",
        local_fallback: LocalMemoryProvider | None = None,
        *,
        request_timeout: float = 10.0,
    ) -> None:
        # Never log the api_key
        if AsyncZep is None:
            raise RuntimeError("zep-cloud is not installed; install the zep extra to enable Zep")
        self.client = AsyncZep(api_key=api_key, timeout=request_timeout)
        self.mode = mode
        self.local_fallback = local_fallback or LocalMemoryProvider()
        self.degraded: bool = False
        self._failure_times: list[float] = []

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _user_id(self, actor_id: str, actor_kind: str) -> str:
        return f"wf:{actor_kind}:{actor_id}"

    def _session_id(self, actor_id: str, universe_id: str) -> str:
        """Compute the primary thread/session id based on the mode."""
        if self.mode in ("cohort_memory", "hero_memory"):
            return f"wf:{universe_id}:{actor_id}"
        elif self.mode == "run_scoped_threads":
            return f"wf:{universe_id}"
        elif self.mode == "hybrid":
            return f"wf:{universe_id}:{actor_id}"
        # fallback
        return f"wf:{universe_id}:{actor_id}"

    def _universe_session_id(self, universe_id: str) -> str:
        """Universe-wide session id (used for hybrid parallel writes)."""
        return f"wf:{universe_id}"

    def _record_failure(self, exc: Exception) -> None:
        now = time.monotonic()
        # Trim old failures outside the window
        self._failure_times = [
            t for t in self._failure_times if now - t < _FAILURE_WINDOW_SECONDS
        ]
        self._failure_times.append(now)
        log.warning(
            "zep_memory_failure",
            error=str(exc),
            failure_count=len(self._failure_times),
        )
        if len(self._failure_times) >= _FAILURE_THRESHOLD:
            if not self.degraded:
                log.error(
                    "zep_memory_degraded",
                    threshold=_FAILURE_THRESHOLD,
                    window_seconds=_FAILURE_WINDOW_SECONDS,
                )
            self.degraded = True

    async def reset_degraded(self) -> bool:
        """Retry Zep with a healthcheck; clear degraded flag if successful."""
        result = await self._raw_healthcheck()
        if result["ok"]:
            self.degraded = False
            self._failure_times.clear()
            log.info("zep_memory_recovered")
        return result["ok"]

    async def _raw_healthcheck(self) -> dict:
        """Perform healthcheck against Zep without degradation tracking."""
        start = time.monotonic()
        try:
            await self.client.thread.list_all(page_size=1, page_number=1)
            latency_ms = int((time.monotonic() - start) * 1000)
            return {"ok": True, "latency_ms": latency_ms, "details": {"provider": "zep"}}
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            return {
                "ok": False,
                "latency_ms": latency_ms,
                "details": {"provider": "zep", "error": str(exc)},
            }

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
        if self.degraded:
            return await self.local_fallback.ensure_user(
                actor_id=actor_id, actor_kind=actor_kind, metadata=metadata
            )
        user_id = self._user_id(actor_id, actor_kind)
        try:
            await self.client.user.add(user_id=user_id, metadata=metadata)
        except Exception as exc:
            # Idempotent: user already exists errors are safe to swallow.
            # We record failures for all other errors and let them pass.
            err_str = str(exc).lower()
            if "already exists" in err_str or "conflict" in err_str or "409" in err_str:
                return  # idempotent — user already exists
            self._record_failure(exc)
            if self.degraded:
                await self.local_fallback.ensure_user(
                    actor_id=actor_id, actor_kind=actor_kind, metadata=metadata
                )

    async def ensure_session(
        self,
        *,
        actor_id: str,
        universe_id: str,
        metadata: dict,
    ) -> str:
        if self.degraded:
            return await self.local_fallback.ensure_session(
                actor_id=actor_id, universe_id=universe_id, metadata=metadata
            )
        session_id = self._session_id(actor_id, universe_id)
        # In run_scoped_threads mode the user_id is a universe-level synthetic id
        if self.mode == "run_scoped_threads":
            user_id = f"wf:universe:{universe_id}"
        else:
            # Determine actor_kind from metadata if provided
            actor_kind = metadata.get("actor_kind", "cohort")
            user_id = self._user_id(actor_id, actor_kind)
        try:
            await self.client.thread.create(thread_id=session_id, user_id=user_id)
        except Exception as exc:
            err_str = str(exc).lower()
            if "already exists" in err_str or "conflict" in err_str or "409" in err_str:
                pass  # idempotent
            else:
                self._record_failure(exc)
                if self.degraded:
                    return await self.local_fallback.ensure_session(
                        actor_id=actor_id, universe_id=universe_id, metadata=metadata
                    )
        self.local_fallback.register_session(actor_id=actor_id, session_id=session_id)

        # Hybrid mode: also write to universe-scoped session
        if self.mode == "hybrid":
            universe_sid = self._universe_session_id(universe_id)
            universe_user_id = f"wf:universe:{universe_id}"
            try:
                await self.client.thread.create(
                    thread_id=universe_sid, user_id=universe_user_id
                )
            except Exception as exc:
                err_str = str(exc).lower()
                if "already exists" not in err_str and "conflict" not in err_str and "409" not in err_str:
                    self._record_failure(exc)

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
        if self.degraded:
            return await self.local_fallback.add_episode(
                session_id=session_id,
                role=role,
                role_type=role_type,
                content=content,
                metadata=metadata,
            )
        # Zep cloud v3 schema: `role` is an enum (user|assistant|system|tool|function|norole)
        # while `name` carries the speaker label (cohort/hero id). Map our role_type → role
        # and stash the caller-provided `role` string into name.
        message_cls = ZepMessage or SimpleNamespace
        msg = message_cls(
            role=role_type,  # role_type IS the enum value Zep expects
            name=role,       # speaker label (e.g. cohort_id, hero_id, "tick_summary")
            content=content,
            metadata=metadata or {},
        )
        try:
            await self.client.thread.add_messages(session_id, messages=[msg])
            await self.local_fallback.add_episode(
                session_id=session_id,
                role=role,
                role_type=role_type,
                content=content,
                metadata=metadata,
            )
        except Exception as exc:
            self._record_failure(exc)
            if self.degraded:
                await self.local_fallback.add_episode(
                    session_id=session_id,
                    role=role,
                    role_type=role_type,
                    content=content,
                    metadata=metadata,
                )

    async def get_context(
        self,
        *,
        session_id: str,
        max_tokens: int = 2000,
    ) -> str:
        if self.degraded:
            return await self.local_fallback.get_context(
                session_id=session_id, max_tokens=max_tokens
            )
        try:
            response = await self.client.thread.get_user_context(session_id)
            return response.context or ""
        except Exception as exc:
            self._record_failure(exc)
            if self.degraded:
                return await self.local_fallback.get_context(
                    session_id=session_id, max_tokens=max_tokens
                )
            return ""

    async def search_graph(
        self,
        *,
        actor_id: str,
        query: str,
        top_k: int = 5,
    ) -> list[dict]:
        if self.degraded:
            return await self.local_fallback.search_graph(
                actor_id=actor_id, query=query, top_k=top_k
            )
        # user_id is used for user-graph search (Zep v2 graph.search with user_id param)
        # We don't know actor_kind here, so attempt both prefixes gracefully
        user_id = f"wf:cohort:{actor_id}"
        try:
            results = await self.client.graph.search(
                query=query,
                user_id=user_id,
                limit=top_k,
            )
            # GraphSearchResults has .edges, .nodes, .episodes, .context, etc.
            items: list[dict] = []
            if results.edges:
                for edge in results.edges:
                    items.append({"type": "edge", "fact": getattr(edge, "fact", str(edge))})
            if results.nodes:
                for node in results.nodes:
                    items.append({"type": "node", "name": getattr(node, "name", str(node))})
            if results.episodes:
                for ep in results.episodes:
                    items.append(
                        {
                            "type": "episode",
                            "content": getattr(ep, "content", str(ep)),
                        }
                    )
            if results.context:
                # context is a pre-assembled string — wrap it as a single result
                items.insert(0, {"type": "context", "content": results.context})
            return items[:top_k]
        except Exception as exc:
            self._record_failure(exc)
            log.warning("zep_graph_search_failed", error=str(exc), actor_id=actor_id)
            if self.degraded:
                return await self.local_fallback.search_graph(
                    actor_id=actor_id, query=query, top_k=top_k
                )
            return []

    async def end_of_tick_summary(
        self,
        *,
        actor_id: str,
        universe_id: str,
        tick: int,
        summary_text: str,
    ) -> None:
        """Persist tick summary as a system message with role='tick_summary'."""
        session_id = self._session_id(actor_id, universe_id)
        await self.add_episode(
            session_id=session_id,
            role="tick_summary",
            role_type="system",
            content=summary_text,
            metadata={"tick": tick, "actor_id": actor_id, "universe_id": universe_id},
        )
        # Also write to local fallback so it's always available
        await self.local_fallback.end_of_tick_summary(
            actor_id=actor_id,
            universe_id=universe_id,
            tick=tick,
            summary_text=summary_text,
        )

    async def split_inheritance(
        self,
        *,
        parent_actor_id: str,
        child_actor_ids: list[str],
        split_note: str,
    ) -> None:
        """Copy parent's most recent summary to each child as a system message."""
        parent_summary = self.local_fallback._summaries.get(parent_actor_id, "")

        for child_id in child_actor_ids:
            content = f"You inherit from cohort {parent_actor_id}. {split_note}"
            if parent_summary:
                content = f"{content}\n\nParent memory summary:\n{parent_summary}"

            # Propagate to local fallback first (always available)
            await self.local_fallback.split_inheritance(
                parent_actor_id=parent_actor_id,
                child_actor_ids=[child_id],
                split_note=split_note,
            )

            if self.degraded:
                continue  # local fallback already handled

            # We need a session_id for the child — we'll use a synthetic one
            # since the caller hasn't necessarily called ensure_session yet
            child_session_id = f"wf:inherit:{child_id}"
            await self.add_episode(
                session_id=child_session_id,
                role="system",
                role_type="system",
                content=content,
                metadata={
                    "parent_actor_id": parent_actor_id,
                    "split_note": split_note,
                },
            )

    async def healthcheck(self) -> dict:
        result = await self._raw_healthcheck()
        if result["ok"]:
            # Opportunistic: clear degraded if we were degraded and it now passes
            if self.degraded:
                await self.reset_degraded()
        else:
            result["details"]["degraded"] = self.degraded
        return result
