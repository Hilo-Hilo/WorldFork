"""Branch-policy gate (branch creation and explosion controls).

After the God-agent emits a :class:`GodReviewOutput`, the engine asks this
module whether the proposed decision should be:

* ``approve`` — execute as-is.
* ``downgrade_to_candidate`` — record the branch as a candidate but do not
  spin up a child sim (cheap; preserves the branch idea for later promotion).
* ``reject`` — drop the branch entirely (e.g. ``max_depth`` hit).

Only ``spawn_active`` decisions can be downgraded or rejected — ``continue``,
``freeze``, ``kill``, and ``spawn_candidate`` are always approved (they don't
consume new universe slots).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from backend.app.branching.divergence import compute_divergence_estimate
from backend.app.schemas.branching import BranchPolicy, BranchPolicyResult
from backend.app.schemas.llm import GodReviewOutput

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MultiverseSnapshot
# ---------------------------------------------------------------------------


@dataclass
class MultiverseSnapshot:
    """A summary of multiverse state used by the branch-policy gate.

    All fields are read-only inputs to :func:`evaluate_branch_policy`.  The
    engine assembles this snapshot once per God review.
    """

    big_bang_id: str
    active_universe_count: int
    total_branch_count: int
    max_depth_reached: int
    branches_this_tick: int
    last_branch_tick_per_universe: dict[str, int] = field(default_factory=dict)
    parent_depth_by_universe: dict[str, int] = field(default_factory=dict)
    parent_metrics_history: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    budget_pct_used: float = 0.0
    capacity_p0_pct_used: float = 0.0


# ---------------------------------------------------------------------------
# Cost estimate heuristic
# ---------------------------------------------------------------------------

# Rough per-tick cost numbers — we don't have actuals at decide-time, so we
# use the Big Bang setup wizard's defaults: ~30 cohort+hero LLM calls per
# tick, ~1.5K tokens each.
_EST_CALLS_PER_TICK = 30
_EST_TOKENS_PER_CALL = 1_500
# $/token derived from the GPT-4o-mini routing default.
_EST_USD_PER_TOKEN = 0.00015 / 1000


def _cost_estimate(remaining_ticks: int = 5) -> dict[str, Any]:
    """Build a coarse ``{est_llm_calls, est_tokens, est_usd}`` projection.

    The remaining-ticks default is intentionally short — the policy gate
    cares about *incremental* spend if this branch is approved, not the
    full tail of the run.
    """
    calls = _EST_CALLS_PER_TICK * max(1, remaining_ticks)
    tokens = calls * _EST_TOKENS_PER_CALL
    return {
        "est_llm_calls": calls,
        "est_tokens": tokens,
        "est_usd": round(tokens * _EST_USD_PER_TOKEN, 4),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Decisions that don't consume any new universe-level capacity.
_NON_BRANCH_DECISIONS = {"continue", "freeze", "kill", "complete_universe"}


def evaluate_branch_policy(
    *,
    parent_universe_id: str,
    parent_current_tick: int,
    proposed_decision: GodReviewOutput,
    multiverse: MultiverseSnapshot,
    policy: BranchPolicy,
) -> BranchPolicyResult:
    """Apply §13.5 explosion controls to a God-agent decision.

    Logic summary:

    * ``continue`` / ``freeze`` / ``kill`` — always ``approve``.
    * ``spawn_candidate`` — always ``approve`` (candidates are cheap; no sim).
    * ``spawn_active`` runs the full ladder (depth, totals, per-tick cap,
      cooldown, budget, divergence).  If any tripwire fires, downgrade to
      candidate.  Only the depth check can hard-reject.
    """
    decision = proposed_decision.decision

    if decision in _NON_BRANCH_DECISIONS:
        return BranchPolicyResult(
            decision="approve",
            reason="not a branch",
            divergence_score=None,
            cost_estimate=None,
        )

    if decision == "spawn_candidate":
        # Candidates are essentially free metadata rows — never block them.
        return BranchPolicyResult(
            decision="approve",
            reason="candidate accepted (no immediate sim)",
            divergence_score=None,
            cost_estimate=None,
        )

    # ------------------------------------------------------------------
    # spawn_active — run the full §13.5 ladder.
    # ------------------------------------------------------------------

    # 1. Depth — the only hard-reject path.  Prefer the requested parent's
    #    depth when the snapshot carries it; global max depth can reject valid
    #    branches from shallower siblings.
    parent_depth = multiverse.parent_depth_by_universe.get(parent_universe_id)
    projected_depth = (
        parent_depth + 1
        if parent_depth is not None
        else multiverse.max_depth_reached + 1
    )
    if projected_depth > policy.max_depth:
        return BranchPolicyResult(
            decision="reject",
            reason=(
                f"max_depth would be exceeded "
                f"(projected={projected_depth}, max={policy.max_depth})"
            ),
            divergence_score=None,
            cost_estimate=None,
        )

    # 2. Active-universe cap.
    if multiverse.active_universe_count >= policy.max_active_universes:
        return BranchPolicyResult(
            decision="downgrade_to_candidate",
            reason=(
                f"max_active_universes reached "
                f"({multiverse.active_universe_count}/{policy.max_active_universes})"
            ),
            divergence_score=None,
            cost_estimate=_cost_estimate(),
        )

    # 3. Total-branch cap.
    if multiverse.total_branch_count >= policy.max_total_branches:
        return BranchPolicyResult(
            decision="downgrade_to_candidate",
            reason=(
                f"max_total_branches reached "
                f"({multiverse.total_branch_count}/{policy.max_total_branches})"
            ),
            divergence_score=None,
            cost_estimate=_cost_estimate(),
        )

    # 4. Per-tick cap (multiverse-wide).
    if multiverse.branches_this_tick >= policy.max_branches_per_tick:
        return BranchPolicyResult(
            decision="downgrade_to_candidate",
            reason=(
                f"max_branches_per_tick reached this tick "
                f"({multiverse.branches_this_tick}/{policy.max_branches_per_tick})"
            ),
            divergence_score=None,
            cost_estimate=_cost_estimate(),
        )

    # 5. Cooldown — the parent universe must have waited
    #    ``branch_cooldown_ticks`` since its last branch.  If never branched,
    #    cooldown is trivially satisfied.
    last_tick = multiverse.last_branch_tick_per_universe.get(parent_universe_id)
    if last_tick is not None:
        ticks_since = parent_current_tick - last_tick
        if ticks_since < policy.branch_cooldown_ticks:
            return BranchPolicyResult(
                decision="downgrade_to_candidate",
                reason=(
                    f"branch_cooldown_ticks not elapsed "
                    f"(elapsed={ticks_since}/{policy.branch_cooldown_ticks})"
                ),
                divergence_score=None,
                cost_estimate=_cost_estimate(),
            )

    # 6. Budget — be defensive once we're within 5% of the daily cap.
    if multiverse.budget_pct_used > 0.95:
        return BranchPolicyResult(
            decision="downgrade_to_candidate",
            reason=(
                f"budget_pct_used={multiverse.budget_pct_used:.2%} > 95%; "
                "preserving headroom for P0 traffic"
            ),
            divergence_score=None,
            cost_estimate=_cost_estimate(),
        )

    # 7. Divergence — drop low-value branches if auto_prune is on.
    history = multiverse.parent_metrics_history.get(parent_universe_id, [])
    divergence = compute_divergence_estimate(history, proposed_decision.branch_delta)
    if (
        policy.auto_prune_low_value
        and divergence < policy.min_divergence_score
    ):
        return BranchPolicyResult(
            decision="downgrade_to_candidate",
            reason=(
                f"divergence_estimate={divergence:.2f} < "
                f"min_divergence_score={policy.min_divergence_score:.2f}"
            ),
            divergence_score=round(divergence, 4),
            cost_estimate=_cost_estimate(),
        )

    # All checks passed — approve the active branch.
    return BranchPolicyResult(
        decision="approve",
        reason="all explosion controls within limits",
        divergence_score=round(divergence, 4),
        cost_estimate=_cost_estimate(),
    )


__all__ = [
    "MultiverseSnapshot",
    "evaluate_branch_policy",
]
