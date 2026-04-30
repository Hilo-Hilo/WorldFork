"""Unit tests for backend.app.branching.god_agent."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import pytest
import pytest_asyncio

from backend.app.branching.god_agent import (
    GodReviewInput,
    _apply_invariants,
    _is_safe_noop_decision,
    _known_event_ids,
    god_review,
)
from backend.app.providers import (
    ProviderRateLimiter,
    RoutingTable,
    clear_registry,
)
from backend.app.schemas.llm import GodReviewOutput, LLMResult
from backend.app.storage.ledger import Ledger
from backend.app.storage.sot_loader import load_sot

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sot():
    return load_sot()


@pytest_asyncio.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose(close_connection_pool=True)


@pytest.fixture
def limiter(redis_client) -> ProviderRateLimiter:
    return ProviderRateLimiter(
        redis_client,
        provider="openrouter",
        rpm_limit=600,
        tpm_limit=1_000_000,
        max_concurrency=8,
        daily_budget_usd=None,
        jitter=False,
    )


@pytest.fixture
def routing() -> RoutingTable:
    return RoutingTable.defaults()


@pytest.fixture(autouse=True)
def _clear_registry():
    clear_registry()
    yield
    clear_registry()


@pytest.fixture
def ledger(tmp_run_root: Path) -> Ledger:
    return Ledger.begin_run(
        tmp_run_root,
        big_bang_id="bb_test",
        scenario_text="god review test",
        sot_snapshot_sha="deadbeef",
        config_snapshot={"k": "v"},
    )


@pytest.fixture
def god_input() -> GodReviewInput:
    return GodReviewInput(
        universe_id="U000",
        run_id="run_test",
        current_tick=3,
        universe_state_summary={
            "universe_id": "U000",
            "branch_depth": 0,
            "lineage_path": ["U000"],
            "status": "active",
            "current_tick": 3,
        },
        recent_ticks=[
            {"tick": 0, "divergence_score": 0.0},
            {"tick": 1, "divergence_score": 0.05},
            {"tick": 2, "divergence_score": 0.07},
        ],
        event_proposals=[
            {
                "event_id": "evt_company_statement",
                "universe_id": "U000",
                "title": "Company issues a statement.",
                "description": "Some sufficiently long description text to satisfy minLength constraints.",
                "event_type": "press_release",
                "tick_proposed": 3,
                "scheduled_tick": 4,
                "status": "queued",
                "actor_id": "company_a",
            }
        ],
        social_posts=[
            {
                "post_id": "post_0001",
                "universe_id": "U000",
                "tick_posted": 3,
                "actor_id": "cohort_a",
                "channel": "social_media_global",
                "text": "Some post body text.",
                "expression": "express_self_normally",
            }
        ],
        metrics={"opinion_dispersion": 0.4, "active_cohorts": 5},
        branch_candidates=[],
        rate_limit_state={"rpm_used_pct": 0.1, "tpm_used_pct": 0.1},
        budget_state={"used_usd": 0.5, "limit_usd": 50.0, "pct_used": 0.01},
        prior_branch_history=[],
    )


# ---------------------------------------------------------------------------
# Canned decisions returned by the mock LLM
# ---------------------------------------------------------------------------


def _canned_continue() -> dict:
    return {
        "decision": "continue",
        "branch_delta": None,
        "marked_key_events": ["evt_company_statement"],
        "tick_summary": (
            "The universe continued without significant divergence this tick. "
            "Metrics remain calm and aligned with the prior trajectory."
        ),
        "rationale": {
            "main_factors": ["calm metrics", "no novel events"],
            "confidence": "high",
        },
    }


def _canned_spawn_active_no_delta() -> dict:
    """Intentionally missing branch_delta — should be coerced to spawn_candidate."""
    return {
        "decision": "spawn_active",
        "branch_delta": None,
        "marked_key_events": [],
        "tick_summary": (
            "The God-agent wants to branch but did not specify a delta. "
            "We expect a coercion to spawn_candidate."
        ),
        "rationale": {
            "main_factors": ["wanted to explore alt counterfactual"],
            "confidence": "medium",
        },
    }


def _canned_kill_no_factors() -> dict:
    """Kill decision with empty main_factors — placeholder must be appended."""
    return {
        "decision": "kill",
        "branch_delta": None,
        "marked_key_events": [],
        "tick_summary": (
            "The universe has stalled and offers no further analytical value. "
            "Killing it to free capacity."
        ),
        "rationale": {
            "main_factors": [],
            "confidence": "low",
        },
    }


def _canned_with_unknown_marked_ids() -> dict:
    return {
        "decision": "continue",
        "branch_delta": None,
        "marked_key_events": [
            "evt_company_statement",  # known
            "evt_does_not_exist",  # unknown
            "post_0001",  # known
            "totally_unknown_id",
        ],
        "tick_summary": (
            "Marked some key events. Some IDs may not be known to the engine "
            "and should be filtered out."
        ),
        "rationale": {
            "main_factors": ["routine"],
            "confidence": "high",
        },
    }


# ---------------------------------------------------------------------------
# Helpers — patch the orchestrator to return a canned LLMResult.
# ---------------------------------------------------------------------------


def _mock_llm_result(parsed: dict) -> LLMResult:
    return LLMResult(
        call_id="llm_mock_god",
        provider="openrouter",
        model_used="anthropic/claude-3-5-sonnet",
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
        cost_usd=0.001,
        latency_ms=5,
        parsed_json=parsed,
        tool_calls=[],
        raw_response={},
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# god_review integration tests
# ---------------------------------------------------------------------------


class TestGodReviewIntegration:
    pytestmark = pytest.mark.asyncio

    async def test_canned_continue_returns_parsed_object_and_persists(
        self, god_input, sot, routing, limiter, ledger
    ):
        mock = AsyncMock(return_value=_mock_llm_result(_canned_continue()))
        with patch(
            "backend.app.branching.god_agent.call_with_policy",
            new=mock,
        ):
            decision = await god_review(
                god_input,
                sot=sot,
                routing=routing,
                limiter=limiter,
                ledger=ledger,
            )
        assert isinstance(decision, GodReviewOutput)
        assert decision.decision == "continue"
        assert decision.branch_delta is None
        assert "evt_company_statement" in decision.marked_key_events
        # Ledger artifact written.
        decision_path = (
            ledger.run_folder
            / f"universes/{god_input.universe_id}/ticks/tick_{god_input.current_tick:03d}/god/decision.json"
        )
        assert decision_path.exists()
        on_disk = json.loads(decision_path.read_bytes())
        assert on_disk["decision"] == "continue"
        # call_with_policy was invoked once with the right job type.
        mock.assert_awaited_once()
        kwargs = mock.await_args.kwargs
        assert kwargs["job_type"] == "god_agent_review"
        assert kwargs["universe_id"] == "U000"
        assert kwargs["tick"] == 3
        assert kwargs["run_id"] == "run_test"

    async def test_spawn_active_without_branch_delta_is_coerced(
        self, god_input, sot, routing, limiter, ledger
    ):
        mock = AsyncMock(return_value=_mock_llm_result(_canned_spawn_active_no_delta()))
        with patch(
            "backend.app.branching.god_agent.call_with_policy",
            new=mock,
        ):
            decision = await god_review(
                god_input,
                sot=sot,
                routing=routing,
                limiter=limiter,
                ledger=ledger,
            )
        assert decision.decision == "spawn_candidate"
        assert decision.branch_delta is None

    async def test_kill_without_rationale_gets_placeholder(
        self, god_input, sot, routing, limiter, ledger
    ):
        mock = AsyncMock(return_value=_mock_llm_result(_canned_kill_no_factors()))
        with patch(
            "backend.app.branching.god_agent.call_with_policy",
            new=mock,
        ):
            decision = await god_review(
                god_input,
                sot=sot,
                routing=routing,
                limiter=limiter,
                ledger=ledger,
            )
        assert decision.decision == "kill"
        factors = decision.rationale.get("main_factors") or []
        assert "low_value_auto" in factors

    async def test_marked_key_events_drop_unknown_ids(
        self, god_input, sot, routing, limiter, ledger
    ):
        mock = AsyncMock(return_value=_mock_llm_result(_canned_with_unknown_marked_ids()))
        with patch(
            "backend.app.branching.god_agent.call_with_policy",
            new=mock,
        ):
            decision = await god_review(
                god_input,
                sot=sot,
                routing=routing,
                limiter=limiter,
                ledger=ledger,
            )
        assert "evt_company_statement" in decision.marked_key_events
        assert "post_0001" in decision.marked_key_events
        assert "evt_does_not_exist" not in decision.marked_key_events
        assert "totally_unknown_id" not in decision.marked_key_events

    async def test_ledger_optional(self, god_input, sot, routing, limiter):
        # ledger=None should not raise.
        mock = AsyncMock(return_value=_mock_llm_result(_canned_continue()))
        with patch(
            "backend.app.branching.god_agent.call_with_policy",
            new=mock,
        ):
            decision = await god_review(
                god_input,
                sot=sot,
                routing=routing,
                limiter=limiter,
                ledger=None,
            )
        assert decision.decision == "continue"


# ---------------------------------------------------------------------------
# Pure-helper unit tests
# ---------------------------------------------------------------------------


class TestApplyInvariants:
    def test_spawn_active_without_delta_coerced(self):
        out = _apply_invariants(
            {
                "decision": "spawn_active",
                "branch_delta": None,
                "marked_key_events": [],
                "tick_summary": "x",
                "rationale": {"main_factors": ["a"], "confidence": "low"},
            },
            known_ids=set(),
        )
        assert out["decision"] == "spawn_candidate"

    def test_kill_with_empty_factors_gets_placeholder(self):
        out = _apply_invariants(
            {
                "decision": "kill",
                "rationale": {"main_factors": [], "confidence": "low"},
                "marked_key_events": [],
                "tick_summary": "x",
            },
            known_ids=set(),
        )
        assert "low_value_auto" in out["rationale"]["main_factors"]

    def test_kill_with_existing_factors_unchanged(self):
        before = {
            "decision": "kill",
            "rationale": {"main_factors": ["actual_reason"], "confidence": "low"},
            "marked_key_events": [],
            "tick_summary": "x",
        }
        out = _apply_invariants(before, known_ids=set())
        assert out["rationale"]["main_factors"] == ["actual_reason"]
        assert "low_value_auto" not in out["rationale"]["main_factors"]

    def test_marked_key_events_filtered(self):
        out = _apply_invariants(
            {
                "decision": "continue",
                "marked_key_events": ["evt_a", "evt_unknown", "post_b"],
                "tick_summary": "x",
                "rationale": {"main_factors": ["a"], "confidence": "high"},
            },
            known_ids={"evt_a", "post_b"},
        )
        assert out["marked_key_events"] == ["evt_a", "post_b"]


class TestKnownEventIds:
    def test_extracts_event_ids_and_post_ids(self):
        events = [{"event_id": "e1"}, {"event_id": "e2"}]
        posts = [{"post_id": "p1"}]
        ids = _known_event_ids(events, posts)
        assert ids == {"e1", "e2", "p1"}

    def test_handles_id_field_alias(self):
        events = [{"id": "e1"}]
        ids = _known_event_ids(events, [])
        assert "e1" in ids


class TestSafeNoopDetection:
    def test_detects_explicit_safe_noop(self):
        decision = GodReviewOutput.model_validate(
            {
                "decision": "continue",
                "branch_delta": None,
                "marked_key_events": [],
                "tick_summary": "[safe no-op: invalid JSON from provider]",
                "rationale": {"reason": "invalid_json_safe_noop"},
            }
        )
        assert _is_safe_noop_decision(decision) is True

    def test_normal_continue_is_not_noop(self):
        decision = GodReviewOutput.model_validate(_canned_continue())
        assert _is_safe_noop_decision(decision) is False
