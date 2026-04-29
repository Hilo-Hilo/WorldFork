"""
Unit tests for ZepMemoryProvider — all Zep SDK calls are mocked.

Tests:
 - SDK called with expected arguments
 - Degradation: 3 failures -> degraded=True, subsequent calls use local_fallback
 - Mode routing: cohort_memory, run_scoped_threads, hybrid session IDs
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch


from backend.app.memory.local import LocalMemoryProvider
from backend.app.memory.zep_adapter import (
    _FAILURE_THRESHOLD,
    _FAILURE_WINDOW_SECONDS,
    ZepMemoryProvider,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_provider(mode: str = "cohort_memory") -> tuple[ZepMemoryProvider, AsyncMock]:
    """Return (ZepMemoryProvider, mock_client).

    Patches the module-level AsyncZep so the constructor uses the mock,
    then also attaches the mock as provider.client for direct inspection.
    """
    local = LocalMemoryProvider()
    mock_client = AsyncMock()
    mock_client.user = AsyncMock()
    mock_client.thread = AsyncMock()
    mock_client.graph = AsyncMock()

    with patch("backend.app.memory.zep_adapter.AsyncZep", return_value=mock_client):
        provider = ZepMemoryProvider(
            api_key="z_fake_key",
            mode=mode,
            local_fallback=local,
        )
    # Ensure the client attribute points to the same mock
    provider.client = mock_client
    return provider, mock_client


# ---------------------------------------------------------------------------
# Session ID routing
# ---------------------------------------------------------------------------


class TestSessionIdRouting:
    def test_cohort_memory_session_id(self) -> None:
        provider, _ = _make_provider(mode="cohort_memory")
        sid = provider._session_id("cohort_abc", "U001")
        assert sid == "wf:U001:cohort_abc"

    def test_hero_memory_session_id(self) -> None:
        provider, _ = _make_provider(mode="hero_memory")
        sid = provider._session_id("hero_xyz", "U002")
        assert sid == "wf:U002:hero_xyz"

    def test_run_scoped_threads_session_id(self) -> None:
        provider, _ = _make_provider(mode="run_scoped_threads")
        sid = provider._session_id("cohort_abc", "U003")
        assert sid == "wf:U003"

    def test_hybrid_session_id(self) -> None:
        provider, _ = _make_provider(mode="hybrid")
        sid = provider._session_id("cohort_abc", "U004")
        assert sid == "wf:U004:cohort_abc"

    def test_user_id_cohort(self) -> None:
        provider, _ = _make_provider()
        uid = provider._user_id("actor1", "cohort")
        assert uid == "wf:cohort:actor1"

    def test_user_id_hero(self) -> None:
        provider, _ = _make_provider()
        uid = provider._user_id("hero1", "hero")
        assert uid == "wf:hero:hero1"


# ---------------------------------------------------------------------------
# ensure_user SDK call
# ---------------------------------------------------------------------------


class TestEnsureUser:
    async def test_calls_user_add(self) -> None:
        provider, mock_client = _make_provider()
        mock_client.user.add = AsyncMock(return_value=MagicMock())
        await provider.ensure_user(actor_id="a1", actor_kind="cohort", metadata={"x": 1})
        mock_client.user.add.assert_awaited_once()
        call_kwargs = mock_client.user.add.call_args.kwargs
        assert call_kwargs["user_id"] == "wf:cohort:a1"
        assert call_kwargs["metadata"] == {"x": 1}

    async def test_idempotent_on_conflict(self) -> None:
        provider, mock_client = _make_provider()
        mock_client.user.add = AsyncMock(side_effect=Exception("already exists"))
        # Should NOT raise or record failure
        await provider.ensure_user(actor_id="a1", actor_kind="cohort", metadata={})
        assert not provider.degraded

    async def test_non_conflict_error_recorded(self) -> None:
        provider, mock_client = _make_provider()
        mock_client.user.add = AsyncMock(side_effect=Exception("network error"))
        await provider.ensure_user(actor_id="a1", actor_kind="cohort", metadata={})
        assert len(provider._failure_times) == 1


# ---------------------------------------------------------------------------
# ensure_session SDK call
# ---------------------------------------------------------------------------


class TestEnsureSession:
    async def test_calls_thread_create_cohort_memory(self) -> None:
        provider, mock_client = _make_provider(mode="cohort_memory")
        mock_client.thread.create = AsyncMock(return_value=MagicMock())
        sid = await provider.ensure_session(
            actor_id="a1", universe_id="U001", metadata={"actor_kind": "cohort"}
        )
        assert sid == "wf:U001:a1"
        mock_client.thread.create.assert_awaited_once()
        call_kwargs = mock_client.thread.create.call_args.kwargs
        assert call_kwargs["thread_id"] == "wf:U001:a1"

    async def test_run_scoped_session_id(self) -> None:
        provider, mock_client = _make_provider(mode="run_scoped_threads")
        mock_client.thread.create = AsyncMock(return_value=MagicMock())
        sid = await provider.ensure_session(
            actor_id="a1", universe_id="U001", metadata={}
        )
        assert sid == "wf:U001"  # universe-scoped
        mock_client.thread.create.assert_awaited_once()

    async def test_hybrid_writes_two_threads(self) -> None:
        provider, mock_client = _make_provider(mode="hybrid")
        mock_client.thread.create = AsyncMock(return_value=MagicMock())
        await provider.ensure_session(
            actor_id="a1", universe_id="U001", metadata={"actor_kind": "cohort"}
        )
        # hybrid: actor session + universe session
        assert mock_client.thread.create.await_count == 2

    async def test_idempotent_on_conflict(self) -> None:
        provider, mock_client = _make_provider()
        mock_client.thread.create = AsyncMock(
            side_effect=Exception("409 already exists")
        )
        sid = await provider.ensure_session(
            actor_id="a1", universe_id="U001", metadata={}
        )
        # Should return session id without degrading
        assert sid == "wf:U001:a1"
        assert not provider.degraded


# ---------------------------------------------------------------------------
# add_episode SDK call
# ---------------------------------------------------------------------------


class TestAddEpisode:
    async def test_calls_thread_add_messages(self) -> None:
        provider, mock_client = _make_provider()
        mock_client.thread.add_messages = AsyncMock(return_value=MagicMock())
        await provider.add_episode(
            session_id="wf:U001:a1",
            role="cohort_X",
            role_type="user",
            content="Hello",
        )
        mock_client.thread.add_messages.assert_awaited_once()
        call_args = mock_client.thread.add_messages.call_args
        assert call_args.args[0] == "wf:U001:a1"
        msgs = call_args.kwargs["messages"]
        assert len(msgs) == 1
        assert msgs[0].content == "Hello"
        # Zep v3 schema: role is the enum (mapped from role_type), name is speaker label
        assert msgs[0].role == "user"
        assert msgs[0].name == "cohort_X"

    async def test_failure_recorded(self) -> None:
        provider, mock_client = _make_provider()
        mock_client.thread.add_messages = AsyncMock(side_effect=Exception("timeout"))
        await provider.add_episode(
            session_id="sid",
            role="r",
            role_type="user",
            content="content",
        )
        assert len(provider._failure_times) == 1

    async def test_successful_write_through_survives_degradation(self) -> None:
        provider, mock_client = _make_provider()
        mock_client.thread.create = AsyncMock(return_value=MagicMock())
        mock_client.thread.add_messages = AsyncMock(return_value=MagicMock())
        sid = await provider.ensure_session(
            actor_id="a1", universe_id="U001", metadata={"actor_kind": "cohort"}
        )

        await provider.add_episode(
            session_id=sid,
            role="cohort_X",
            role_type="user",
            content="workers remember the bridge meeting",
        )
        provider.degraded = True

        ctx = await provider.get_context(session_id=sid)
        results = await provider.search_graph(actor_id="a1", query="bridge", top_k=5)

        assert "bridge meeting" in ctx
        assert any(result["session_id"] == sid for result in results)


# ---------------------------------------------------------------------------
# get_context SDK call
# ---------------------------------------------------------------------------


class TestGetContext:
    async def test_returns_context_field(self) -> None:
        provider, mock_client = _make_provider()
        mock_response = MagicMock()
        mock_response.context = "assembled context text"
        mock_client.thread.get_user_context = AsyncMock(return_value=mock_response)
        ctx = await provider.get_context(session_id="wf:U001:a1")
        assert ctx == "assembled context text"
        mock_client.thread.get_user_context.assert_awaited_once_with("wf:U001:a1")

    async def test_returns_empty_string_on_none_context(self) -> None:
        provider, mock_client = _make_provider()
        mock_response = MagicMock()
        mock_response.context = None
        mock_client.thread.get_user_context = AsyncMock(return_value=mock_response)
        ctx = await provider.get_context(session_id="wf:U001:a1")
        assert ctx == ""

    async def test_failure_returns_empty(self) -> None:
        provider, mock_client = _make_provider()
        mock_client.thread.get_user_context = AsyncMock(side_effect=Exception("error"))
        ctx = await provider.get_context(session_id="wf:U001:a1")
        assert ctx == ""
        assert len(provider._failure_times) == 1


# ---------------------------------------------------------------------------
# search_graph SDK call
# ---------------------------------------------------------------------------


class TestSearchGraph:
    async def test_calls_graph_search(self) -> None:
        provider, mock_client = _make_provider()
        mock_results = MagicMock()
        mock_results.edges = []
        mock_results.nodes = []
        mock_results.episodes = []
        mock_results.context = "some context"
        mock_client.graph.search = AsyncMock(return_value=mock_results)
        await provider.search_graph(actor_id="a1", query="workers", top_k=3)
        mock_client.graph.search.assert_awaited_once()
        call_kwargs = mock_client.graph.search.call_args.kwargs
        assert call_kwargs["query"] == "workers"
        assert call_kwargs["limit"] == 3

    async def test_returns_empty_on_failure(self) -> None:
        provider, mock_client = _make_provider()
        mock_client.graph.search = AsyncMock(side_effect=Exception("graph error"))
        results = await provider.search_graph(actor_id="a1", query="test", top_k=5)
        assert results == []


# ---------------------------------------------------------------------------
# end_of_tick_summary
# ---------------------------------------------------------------------------


class TestEndOfTickSummary:
    async def test_persists_as_system_message(self) -> None:
        provider, mock_client = _make_provider()
        mock_client.thread.add_messages = AsyncMock(return_value=MagicMock())
        await provider.end_of_tick_summary(
            actor_id="a1", universe_id="U001", tick=5, summary_text="Tick 5 summary"
        )
        mock_client.thread.add_messages.assert_awaited_once()
        call_args = mock_client.thread.add_messages.call_args
        msgs = call_args.kwargs["messages"]
        # Zep v3: role is enum (system mapped from role_type), name is the label
        assert msgs[0].role == "system"
        assert msgs[0].name == "tick_summary"
        assert msgs[0].content == "Tick 5 summary"

    async def test_also_writes_local(self) -> None:
        provider, mock_client = _make_provider()
        mock_client.thread.add_messages = AsyncMock(return_value=MagicMock())
        await provider.end_of_tick_summary(
            actor_id="a1", universe_id="U001", tick=2, summary_text="local too"
        )
        assert provider.local_fallback._summaries.get("a1") == "local too"


# ---------------------------------------------------------------------------
# Degradation mechanism
# ---------------------------------------------------------------------------


class TestDegradation:
    async def test_three_failures_degrade(self) -> None:
        provider, mock_client = _make_provider()
        mock_client.thread.add_messages = AsyncMock(
            side_effect=Exception("repeated failure")
        )
        for _ in range(_FAILURE_THRESHOLD):
            await provider.add_episode(
                session_id="sid", role="r", role_type="user", content="c"
            )
        assert provider.degraded is True

    async def test_degraded_uses_local_fallback_ensure_session(self) -> None:
        provider, mock_client = _make_provider()
        provider.degraded = True
        mock_client.thread.create = AsyncMock()
        sid = await provider.ensure_session(
            actor_id="a1", universe_id="U001", metadata={}
        )
        # Zep client must NOT be called when degraded
        mock_client.thread.create.assert_not_awaited()
        assert sid.startswith("local:")

    async def test_degraded_uses_local_fallback_add_episode(self) -> None:
        provider, mock_client = _make_provider()
        provider.degraded = True
        mock_client.thread.add_messages = AsyncMock()
        await provider.add_episode(
            session_id="local:U001:a1",
            role="r",
            role_type="user",
            content="fallback content",
        )
        mock_client.thread.add_messages.assert_not_awaited()

    async def test_degraded_uses_local_fallback_get_context(self) -> None:
        provider, mock_client = _make_provider()
        provider.degraded = True
        # Pre-populate local
        local_sid = await provider.local_fallback.ensure_session(
            actor_id="a1", universe_id="U001", metadata={}
        )
        await provider.local_fallback.add_episode(
            session_id=local_sid, role="r", role_type="user", content="local context"
        )
        mock_client.thread.get_user_context = AsyncMock()
        ctx = await provider.get_context(session_id=local_sid)
        mock_client.thread.get_user_context.assert_not_awaited()
        assert "local context" in ctx

    async def test_failures_expire_after_window(self) -> None:
        provider, mock_client = _make_provider()
        # Inject old failures (outside window)
        old_time = time.monotonic() - (_FAILURE_WINDOW_SECONDS + 1)
        provider._failure_times = [old_time, old_time]
        mock_client.thread.add_messages = AsyncMock(return_value=MagicMock())
        # This fresh success should NOT cause degradation
        await provider.add_episode(
            session_id="sid", role="r", role_type="user", content="ok"
        )
        assert not provider.degraded

    async def test_reset_degraded(self) -> None:
        provider, mock_client = _make_provider()
        provider.degraded = True
        mock_response = MagicMock()
        mock_response.data = MagicMock()
        mock_client.thread.list_all = AsyncMock(return_value=mock_response)
        recovered = await provider.reset_degraded()
        assert recovered is True
        assert provider.degraded is False


# ---------------------------------------------------------------------------
# split_inheritance
# ---------------------------------------------------------------------------


class TestSplitInheritance:
    async def test_writes_inheritance_message(self) -> None:
        provider, mock_client = _make_provider()
        mock_client.thread.add_messages = AsyncMock(return_value=MagicMock())
        provider.local_fallback._summaries["parent"] = "Parent summary"
        await provider.split_inheritance(
            parent_actor_id="parent",
            child_actor_ids=["child1", "child2"],
            split_note="diverged at tick 5",
        )
        # Two calls — one per child
        assert mock_client.thread.add_messages.await_count == 2

    async def test_local_fallback_also_updated(self) -> None:
        provider, mock_client = _make_provider()
        mock_client.thread.add_messages = AsyncMock(return_value=MagicMock())
        provider.local_fallback._summaries["parent"] = "P"
        await provider.split_inheritance(
            parent_actor_id="parent",
            child_actor_ids=["c"],
            split_note="note",
        )
        assert "parent" in provider.local_fallback._summaries.get("c", "")

    async def test_degraded_skips_zep(self) -> None:
        provider, mock_client = _make_provider()
        provider.degraded = True
        mock_client.thread.add_messages = AsyncMock()
        await provider.split_inheritance(
            parent_actor_id="p", child_actor_ids=["c"], split_note="note"
        )
        mock_client.thread.add_messages.assert_not_awaited()


# ---------------------------------------------------------------------------
# healthcheck
# ---------------------------------------------------------------------------


class TestHealthcheck:
    async def test_ok_when_list_all_succeeds(self) -> None:
        provider, mock_client = _make_provider()
        mock_client.thread.list_all = AsyncMock(return_value=MagicMock())
        result = await provider.healthcheck()
        assert result["ok"] is True
        assert isinstance(result["latency_ms"], int)

    async def test_not_ok_when_list_all_fails(self) -> None:
        provider, mock_client = _make_provider()
        mock_client.thread.list_all = AsyncMock(side_effect=Exception("refused"))
        result = await provider.healthcheck()
        assert result["ok"] is False
        assert "error" in result["details"]
