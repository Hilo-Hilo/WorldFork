"""Thin wrapper around :func:`branching.branch_engine.commit_branch`.

This module exposes a single coroutine, :func:`run_branch_universe`, that
the ``branch_universe`` Celery task delegates to.  It re-fetches the
parent :class:`UniverseModel`, builds the policy result and delta from
the envelope payload, and invokes ``commit_branch`` inside the caller's
session.

Per PRD §15.5 the task is idempotent at the message-key level; the
underlying ledger is the ultimate dedupe (a child universe with the
same lineage_path will already have a row).
"""
from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.branching.branch_engine import BranchCommitResult, commit_branch
from backend.app.schemas.branching import (
    BranchDelta,
    BranchPolicyResult,
)
from backend.app.storage.ledger import Ledger

_log = logging.getLogger(__name__)


async def run_branch_universe(
    *,
    session: AsyncSession,
    parent_universe_id: str,
    branch_from_tick: int,
    delta_payload: dict,
    reason: str,
    policy_decision: str = "approve",
    cost_estimate: dict | None = None,
    ledger: Ledger | None = None,
    enqueue_first_tick: bool = True,
) -> BranchCommitResult:
    """Re-hydrate inputs from a branch_universe envelope and commit the branch.

    Parameters
    ----------
    session
        Open async SQLAlchemy session.
    parent_universe_id
        The universe to branch from.
    branch_from_tick
        Tick at which the branch diverges.
    delta_payload
        Discriminated-union dict for :class:`BranchDelta`.
    reason
        Free-form reason string.
    policy_decision
        Output of an earlier ``evaluate_branch_policy`` call: ``"approve"``
        or ``"downgrade_to_candidate"``.  ``"reject"`` means the branch
        should not be committed; ``commit_branch`` will refuse.
    cost_estimate
        Optional precomputed cost estimate dict.
    ledger
        Optional :class:`Ledger` to record the new universe folder.
    """
    from backend.app.models.universes import UniverseModel

    parent = await session.get(UniverseModel, parent_universe_id)
    if parent is None:
        raise ValueError(f"parent universe {parent_universe_id!r} not found")

    # Construct the discriminated BranchDelta via Pydantic ValidationAdapter.
    from pydantic import TypeAdapter
    delta_obj = TypeAdapter(BranchDelta).validate_python(delta_payload)

    policy_result = BranchPolicyResult(
        decision=policy_decision,  # type: ignore[arg-type]
        reason=reason or "policy precomputed",
        cost_estimate=cost_estimate,
        divergence_score=None,
    )

    return await commit_branch(
        session=session,
        parent_universe=parent,
        branch_from_tick=branch_from_tick,
        delta=delta_obj,
        branch_reason=reason,
        policy_result=policy_result,
        ledger=ledger,
        enqueue_first_tick=enqueue_first_tick,
    )


__all__ = ["run_branch_universe"]
