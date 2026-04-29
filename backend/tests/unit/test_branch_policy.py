"""Unit tests for backend.app.branching.branch_policy."""
from __future__ import annotations

import pytest

from backend.app.branching import (
    BranchPolicy,
    MultiverseSnapshot,
    evaluate_branch_policy,
)
from backend.app.schemas.llm import GodReviewOutput


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def policy() -> BranchPolicy:
    return BranchPolicy(
        max_active_universes=10,
        max_total_branches=50,
        max_depth=3,
        max_branches_per_tick=2,
        branch_cooldown_ticks=2,
        min_divergence_score=0.45,
        auto_prune_low_value=True,
    )


def _snap(**overrides) -> MultiverseSnapshot:
    base = dict(
        big_bang_id="bb_1",
        active_universe_count=1,
        total_branch_count=1,
        max_depth_reached=0,
        branches_this_tick=0,
        last_branch_tick_per_universe={},
        parent_metrics_history={},
        budget_pct_used=0.10,
        capacity_p0_pct_used=0.10,
    )
    base.update(overrides)
    return MultiverseSnapshot(**base)


def _decision(decision: str, branch_delta: dict | None = None) -> GodReviewOutput:
    payload = {
        "decision": decision,
        "branch_delta": branch_delta,
        "marked_key_events": [],
        "tick_summary": "Test tick summary line one. Two sentences total here.",
        "rationale": {"main_factors": ["test"], "confidence": "high"},
    }
    return GodReviewOutput.model_validate(payload)


def _high_div_delta() -> dict:
    """Counterfactual rewrite — divergence_estimate baseline 0.5."""
    return {
        "type": "counterfactual_event_rewrite",
        "target_event_id": "evt_1",
        "parent_version": "defensive statement",
        "child_version": "apology + audit",
    }


def _low_div_delta() -> dict:
    """Empty parameter shift — divergence_estimate = 0.4 baseline (below 0.5)."""
    return {
        "type": "parameter_shift",
        "target": "channel.x",
        "delta": {},
    }


# ---------------------------------------------------------------------------
# Approve paths
# ---------------------------------------------------------------------------


class TestApproveTrivial:
    def test_continue_always_approved(self, policy):
        out = evaluate_branch_policy(
            parent_universe_id="U001",
            parent_current_tick=5,
            proposed_decision=_decision("continue"),
            multiverse=_snap(active_universe_count=999),
            policy=policy,
        )
        assert out.decision == "approve"
        assert out.divergence_score is None
        assert out.cost_estimate is None

    def test_freeze_always_approved(self, policy):
        out = evaluate_branch_policy(
            parent_universe_id="U001",
            parent_current_tick=5,
            proposed_decision=_decision("freeze"),
            multiverse=_snap(),
            policy=policy,
        )
        assert out.decision == "approve"

    def test_kill_always_approved(self, policy):
        out = evaluate_branch_policy(
            parent_universe_id="U001",
            parent_current_tick=5,
            proposed_decision=_decision("kill"),
            multiverse=_snap(),
            policy=policy,
        )
        assert out.decision == "approve"

    def test_complete_universe_is_not_a_branch(self, policy):
        out = evaluate_branch_policy(
            parent_universe_id="U001",
            parent_current_tick=5,
            proposed_decision=_decision("complete_universe"),
            multiverse=_snap(max_depth_reached=99, active_universe_count=999),
            policy=policy,
        )
        assert out.decision == "approve"
        assert out.reason == "not a branch"

    def test_spawn_candidate_approved_even_at_capacity(self, policy):
        out = evaluate_branch_policy(
            parent_universe_id="U001",
            parent_current_tick=5,
            proposed_decision=_decision("spawn_candidate", _high_div_delta()),
            multiverse=_snap(
                active_universe_count=999,
                budget_pct_used=0.99,
            ),
            policy=policy,
        )
        assert out.decision == "approve"


# ---------------------------------------------------------------------------
# spawn_active — the full ladder
# ---------------------------------------------------------------------------


class TestSpawnActiveLadder:
    def test_happy_path_high_divergence_with_capacity(self, policy):
        out = evaluate_branch_policy(
            parent_universe_id="U001",
            parent_current_tick=5,
            proposed_decision=_decision("spawn_active", _high_div_delta()),
            multiverse=_snap(active_universe_count=2),
            policy=policy,
        )
        assert out.decision == "approve"
        assert out.divergence_score is not None
        assert out.divergence_score >= 0.45
        assert out.cost_estimate is not None
        assert out.cost_estimate["est_llm_calls"] > 0

    def test_max_active_universes_downgrades(self, policy):
        out = evaluate_branch_policy(
            parent_universe_id="U001",
            parent_current_tick=5,
            proposed_decision=_decision("spawn_active", _high_div_delta()),
            multiverse=_snap(active_universe_count=10),  # at cap
            policy=policy,
        )
        assert out.decision == "downgrade_to_candidate"
        assert "max_active_universes" in out.reason

    def test_max_total_branches_downgrades(self, policy):
        out = evaluate_branch_policy(
            parent_universe_id="U001",
            parent_current_tick=5,
            proposed_decision=_decision("spawn_active", _high_div_delta()),
            multiverse=_snap(total_branch_count=50),  # at cap
            policy=policy,
        )
        assert out.decision == "downgrade_to_candidate"
        assert "max_total_branches" in out.reason

    def test_max_depth_rejects(self, policy):
        out = evaluate_branch_policy(
            parent_universe_id="U001",
            parent_current_tick=5,
            proposed_decision=_decision("spawn_active", _high_div_delta()),
            multiverse=_snap(max_depth_reached=3),  # next would be 4 > 3
            policy=policy,
        )
        assert out.decision == "reject"
        assert "max_depth" in out.reason

    def test_depth_uses_parent_depth_when_available(self, policy):
        out = evaluate_branch_policy(
            parent_universe_id="U_shallow",
            parent_current_tick=5,
            proposed_decision=_decision("spawn_active", _high_div_delta()),
            multiverse=_snap(
                max_depth_reached=3,
                parent_depth_by_universe={"U_shallow": 1},
            ),
            policy=policy,
        )
        assert out.decision == "approve"

    def test_max_branches_per_tick_downgrades(self, policy):
        out = evaluate_branch_policy(
            parent_universe_id="U001",
            parent_current_tick=5,
            proposed_decision=_decision("spawn_active", _high_div_delta()),
            multiverse=_snap(branches_this_tick=2),  # at cap
            policy=policy,
        )
        assert out.decision == "downgrade_to_candidate"
        assert "max_branches_per_tick" in out.reason

    def test_cooldown_enforced(self, policy):
        out = evaluate_branch_policy(
            parent_universe_id="U001",
            parent_current_tick=5,
            proposed_decision=_decision("spawn_active", _high_div_delta()),
            multiverse=_snap(
                last_branch_tick_per_universe={"U001": 4},  # 5-4=1 < 2
            ),
            policy=policy,
        )
        assert out.decision == "downgrade_to_candidate"
        assert "branch_cooldown_ticks" in out.reason

    def test_cooldown_satisfied_after_enough_ticks(self, policy):
        out = evaluate_branch_policy(
            parent_universe_id="U001",
            parent_current_tick=5,
            proposed_decision=_decision("spawn_active", _high_div_delta()),
            multiverse=_snap(
                last_branch_tick_per_universe={"U001": 3},  # 5-3=2 >= 2
            ),
            policy=policy,
        )
        assert out.decision == "approve"

    def test_cooldown_irrelevant_for_other_universe(self, policy):
        out = evaluate_branch_policy(
            parent_universe_id="U001",
            parent_current_tick=5,
            proposed_decision=_decision("spawn_active", _high_div_delta()),
            multiverse=_snap(
                # cooldown only applies to U002, not the parent.
                last_branch_tick_per_universe={"U002": 5},
            ),
            policy=policy,
        )
        assert out.decision == "approve"

    def test_budget_threshold_downgrades(self, policy):
        out = evaluate_branch_policy(
            parent_universe_id="U001",
            parent_current_tick=5,
            proposed_decision=_decision("spawn_active", _high_div_delta()),
            multiverse=_snap(budget_pct_used=0.96),
            policy=policy,
        )
        assert out.decision == "downgrade_to_candidate"
        assert "budget" in out.reason

    def test_low_divergence_downgrades(self, policy):
        out = evaluate_branch_policy(
            parent_universe_id="U001",
            parent_current_tick=5,
            proposed_decision=_decision("spawn_active", _low_div_delta()),
            multiverse=_snap(),
            policy=policy,
        )
        assert out.decision == "downgrade_to_candidate"
        assert "divergence_estimate" in out.reason
        assert out.divergence_score is not None
        assert out.divergence_score < 0.45

    def test_low_divergence_approved_when_auto_prune_off(self):
        permissive = BranchPolicy(
            max_active_universes=10,
            max_total_branches=50,
            max_depth=3,
            max_branches_per_tick=2,
            branch_cooldown_ticks=2,
            min_divergence_score=0.45,
            auto_prune_low_value=False,
        )
        out = evaluate_branch_policy(
            parent_universe_id="U001",
            parent_current_tick=5,
            proposed_decision=_decision("spawn_active", _low_div_delta()),
            multiverse=_snap(),
            policy=permissive,
        )
        assert out.decision == "approve"

    def test_depth_check_runs_before_capacity(self, policy):
        # Even with everything else maxed, depth is the only hard reject.
        out = evaluate_branch_policy(
            parent_universe_id="U001",
            parent_current_tick=5,
            proposed_decision=_decision("spawn_active", _high_div_delta()),
            multiverse=_snap(
                max_depth_reached=3,
                active_universe_count=10,
                total_branch_count=50,
            ),
            policy=policy,
        )
        assert out.decision == "reject"
        assert "max_depth" in out.reason
