"""
Full unit test coverage for LocalMemoryProvider.

Tests:
 - ensure_user / ensure_session idempotency
 - add_episode + get_context returns appended content
 - end_of_tick_summary overwrites
 - split_inheritance propagates
 - healthcheck always ok
"""
from __future__ import annotations

import pytest

from backend.app.memory.local import LocalMemoryProvider


@pytest.fixture
def provider() -> LocalMemoryProvider:
    return LocalMemoryProvider()


# ---------------------------------------------------------------------------
# ensure_user / ensure_session idempotency
# ---------------------------------------------------------------------------

class TestEnsureUser:
    async def test_ensure_user_creates_user(self, provider: LocalMemoryProvider) -> None:
        await provider.ensure_user(actor_id="a1", actor_kind="cohort", metadata={})
        assert "a1" in provider._users

    async def test_ensure_user_idempotent(self, provider: LocalMemoryProvider) -> None:
        for _ in range(5):
            await provider.ensure_user(actor_id="a1", actor_kind="cohort", metadata={})
        assert len([u for u in provider._users if u == "a1"]) == 1

    async def test_ensure_user_hero_kind(self, provider: LocalMemoryProvider) -> None:
        await provider.ensure_user(actor_id="h1", actor_kind="hero", metadata={"label": "Hero"})
        assert "h1" in provider._users


class TestEnsureSession:
    async def test_ensure_session_returns_string(self, provider: LocalMemoryProvider) -> None:
        sid = await provider.ensure_session(actor_id="a1", universe_id="U000", metadata={})
        assert isinstance(sid, str)
        assert len(sid) > 0

    async def test_ensure_session_idempotent(self, provider: LocalMemoryProvider) -> None:
        sid1 = await provider.ensure_session(actor_id="a1", universe_id="U000", metadata={})
        sid2 = await provider.ensure_session(actor_id="a1", universe_id="U000", metadata={})
        assert sid1 == sid2

    async def test_ensure_session_different_actors(self, provider: LocalMemoryProvider) -> None:
        sid1 = await provider.ensure_session(actor_id="a1", universe_id="U000", metadata={})
        sid2 = await provider.ensure_session(actor_id="a2", universe_id="U000", metadata={})
        assert sid1 != sid2

    async def test_ensure_session_different_universes(self, provider: LocalMemoryProvider) -> None:
        sid1 = await provider.ensure_session(actor_id="a1", universe_id="U000", metadata={})
        sid2 = await provider.ensure_session(actor_id="a1", universe_id="U001", metadata={})
        assert sid1 != sid2

    async def test_ensure_session_creates_deque(self, provider: LocalMemoryProvider) -> None:
        sid = await provider.ensure_session(actor_id="a1", universe_id="U000", metadata={})
        assert sid in provider._episodes


# ---------------------------------------------------------------------------
# add_episode + get_context
# ---------------------------------------------------------------------------

class TestAddEpisodeAndGetContext:
    async def test_add_episode_appends(self, provider: LocalMemoryProvider) -> None:
        sid = await provider.ensure_session(actor_id="a1", universe_id="U000", metadata={})
        await provider.add_episode(
            session_id=sid, role="cohort_X", role_type="user", content="Hello world"
        )
        assert len(provider._episodes[sid]) == 1

    async def test_get_context_returns_content(self, provider: LocalMemoryProvider) -> None:
        sid = await provider.ensure_session(actor_id="a1", universe_id="U000", metadata={})
        await provider.add_episode(
            session_id=sid, role="cohort_X", role_type="user", content="Hello world"
        )
        ctx = await provider.get_context(session_id=sid)
        assert "Hello world" in ctx

    async def test_get_context_multiple_episodes(self, provider: LocalMemoryProvider) -> None:
        sid = await provider.ensure_session(actor_id="a1", universe_id="U000", metadata={})
        for i in range(5):
            await provider.add_episode(
                session_id=sid, role="cohort", role_type="user", content=f"Message {i}"
            )
        ctx = await provider.get_context(session_id=sid)
        for i in range(5):
            assert f"Message {i}" in ctx

    async def test_get_context_empty_session(self, provider: LocalMemoryProvider) -> None:
        sid = await provider.ensure_session(actor_id="a1", universe_id="U000", metadata={})
        ctx = await provider.get_context(session_id=sid)
        assert ctx == ""

    async def test_get_context_unknown_session(self, provider: LocalMemoryProvider) -> None:
        ctx = await provider.get_context(session_id="unknown")
        assert ctx == ""

    async def test_episode_deque_max_size(self, provider: LocalMemoryProvider) -> None:
        """Deque must not exceed MAX_EPISODE_DEQUE entries."""
        sid = await provider.ensure_session(actor_id="a1", universe_id="U000", metadata={})
        limit = provider._MAX_EPISODE_DEQUE
        for i in range(limit + 10):
            await provider.add_episode(
                session_id=sid, role="r", role_type="user", content=f"msg{i}"
            )
        assert len(provider._episodes[sid]) == limit

    async def test_add_episode_with_metadata(self, provider: LocalMemoryProvider) -> None:
        sid = await provider.ensure_session(actor_id="a1", universe_id="U000", metadata={})
        await provider.add_episode(
            session_id=sid,
            role="system",
            role_type="system",
            content="System note",
            metadata={"tick": 3},
        )
        entry = list(provider._episodes[sid])[0]
        assert entry["metadata"] == {"tick": 3}

    async def test_get_context_role_label(self, provider: LocalMemoryProvider) -> None:
        sid = await provider.ensure_session(actor_id="a1", universe_id="U000", metadata={})
        await provider.add_episode(
            session_id=sid, role="mycohort", role_type="user", content="test"
        )
        ctx = await provider.get_context(session_id=sid)
        assert "[mycohort]" in ctx


# ---------------------------------------------------------------------------
# end_of_tick_summary
# ---------------------------------------------------------------------------

class TestEndOfTickSummary:
    async def test_summary_written(self, provider: LocalMemoryProvider) -> None:
        await provider.end_of_tick_summary(
            actor_id="a1", universe_id="U000", tick=1, summary_text="Tick 1 summary"
        )
        assert provider._summaries["a1"] == "Tick 1 summary"

    async def test_summary_overwrites(self, provider: LocalMemoryProvider) -> None:
        await provider.end_of_tick_summary(
            actor_id="a1", universe_id="U000", tick=1, summary_text="First"
        )
        await provider.end_of_tick_summary(
            actor_id="a1", universe_id="U000", tick=2, summary_text="Second"
        )
        assert provider._summaries["a1"] == "Second"

    async def test_summary_different_actors(self, provider: LocalMemoryProvider) -> None:
        await provider.end_of_tick_summary(
            actor_id="a1", universe_id="U000", tick=1, summary_text="A"
        )
        await provider.end_of_tick_summary(
            actor_id="a2", universe_id="U000", tick=1, summary_text="B"
        )
        assert provider._summaries["a1"] == "A"
        assert provider._summaries["a2"] == "B"


# ---------------------------------------------------------------------------
# split_inheritance
# ---------------------------------------------------------------------------

class TestSplitInheritance:
    async def test_split_copies_parent_summary(self, provider: LocalMemoryProvider) -> None:
        await provider.end_of_tick_summary(
            actor_id="parent", universe_id="U000", tick=1, summary_text="Parent summary"
        )
        await provider.split_inheritance(
            parent_actor_id="parent",
            child_actor_ids=["child1", "child2"],
            split_note="Split at tick 1",
        )
        assert "parent" in provider._summaries.get("child1", "")
        assert "parent" in provider._summaries.get("child2", "")

    async def test_split_includes_note(self, provider: LocalMemoryProvider) -> None:
        await provider.split_inheritance(
            parent_actor_id="p",
            child_actor_ids=["c"],
            split_note="reason=divergence",
        )
        assert "reason=divergence" in provider._summaries.get("c", "")

    async def test_split_multiple_children(self, provider: LocalMemoryProvider) -> None:
        await provider.end_of_tick_summary(
            actor_id="p", universe_id="U000", tick=1, summary_text="S"
        )
        children = [f"child{i}" for i in range(5)]
        await provider.split_inheritance(
            parent_actor_id="p", child_actor_ids=children, split_note="note"
        )
        for c in children:
            assert c in provider._summaries or "p" in provider._summaries.get(c, "")

    async def test_split_no_parent_summary(self, provider: LocalMemoryProvider) -> None:
        """Children should still get the split note even with no parent summary."""
        await provider.split_inheritance(
            parent_actor_id="new_parent",
            child_actor_ids=["c"],
            split_note="from new parent",
        )
        assert "from new parent" in provider._summaries.get("c", "")


# ---------------------------------------------------------------------------
# search_graph
# ---------------------------------------------------------------------------

class TestSearchGraph:
    async def test_search_empty(self, provider: LocalMemoryProvider) -> None:
        results = await provider.search_graph(actor_id="a1", query="anything")
        assert results == []

    async def test_search_finds_content(self, provider: LocalMemoryProvider) -> None:
        sid = await provider.ensure_session(actor_id="a1", universe_id="U000", metadata={})
        await provider.add_episode(
            session_id=sid, role="user", role_type="user", content="The workers are organizing"
        )
        results = await provider.search_graph(
            actor_id="a1", query="workers organizing", top_k=5
        )
        assert len(results) >= 1
        assert any("organizing" in r.get("content", "") for r in results)

    async def test_search_top_k_limit(self, provider: LocalMemoryProvider) -> None:
        sid = await provider.ensure_session(actor_id="a1", universe_id="U000", metadata={})
        for i in range(10):
            await provider.add_episode(
                session_id=sid, role="user", role_type="user", content=f"strike event {i}"
            )
        results = await provider.search_graph(actor_id="a1", query="strike", top_k=3)
        assert len(results) <= 3

    async def test_search_only_scans_requested_actor_sessions(
        self, provider: LocalMemoryProvider
    ) -> None:
        a1_sid = await provider.ensure_session(actor_id="a1", universe_id="U000", metadata={})
        a2_sid = await provider.ensure_session(actor_id="a2", universe_id="U000", metadata={})
        await provider.add_episode(
            session_id=a1_sid,
            role="user",
            role_type="user",
            content="private strike plan for actor one",
        )
        await provider.add_episode(
            session_id=a2_sid,
            role="user",
            role_type="user",
            content="private strike plan for actor two",
        )

        results = await provider.search_graph(actor_id="a1", query="strike", top_k=10)

        assert {result["session_id"] for result in results} == {a1_sid}
        assert all("actor two" not in result["content"] for result in results)


# ---------------------------------------------------------------------------
# healthcheck
# ---------------------------------------------------------------------------

class TestHealthcheck:
    async def test_healthcheck_ok(self, provider: LocalMemoryProvider) -> None:
        result = await provider.healthcheck()
        assert result["ok"] is True

    async def test_healthcheck_latency_zero(self, provider: LocalMemoryProvider) -> None:
        result = await provider.healthcheck()
        assert result["latency_ms"] == 0

    async def test_healthcheck_details(self, provider: LocalMemoryProvider) -> None:
        result = await provider.healthcheck()
        assert "provider" in result["details"]
        assert result["details"]["provider"] == "local"

    async def test_healthcheck_always_ok_after_episodes(
        self, provider: LocalMemoryProvider
    ) -> None:
        sid = await provider.ensure_session(actor_id="a1", universe_id="U000", metadata={})
        for i in range(10):
            await provider.add_episode(
                session_id=sid, role="r", role_type="user", content=f"m{i}"
            )
        result = await provider.healthcheck()
        assert result["ok"] is True
