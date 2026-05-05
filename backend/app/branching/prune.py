"""Low-value-branch pruning helper.

Implements the ``auto_prune_low_value`` half of the explosion controls.
A branch is considered low-value when its ``metrics_summary.divergence``
falls below the supplied threshold and its status is ``active``.

In dry-run mode the function only reports the candidates; otherwise it
flips the universe + branch_node status to ``killed`` and stamps the
``killed_at`` timestamp.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def prune_low_value(
    session: AsyncSession,
    big_bang_id: str,
    *,
    min_value: float,
    dry_run: bool,
) -> dict[str, Any]:
    """Mark active universes with ``divergence < min_value`` as killed.

    Parameters
    ----------
    session
        Open async session.
    big_bang_id
        Filter scope — only the given run's universes are considered.
    min_value
        Inclusive lower bound; rows whose divergence is **strictly less**
        than this value are flagged.
    dry_run
        If True, report candidates without mutating any rows.
    """
    from backend.app.models.branches import BranchNodeModel
    from backend.app.models.universes import UniverseModel

    rows = (
        await session.execute(
            select(UniverseModel).where(
                UniverseModel.big_bang_id == big_bang_id,
                UniverseModel.status == "active",
            )
        )
    ).scalars().all()

    candidates: list[dict[str, Any]] = []
    for u in rows:
        metrics = u.latest_metrics or {}
        divergence_raw = metrics.get("divergence")
        # Tolerate missing divergence (treat as 0.0 → likely a candidate).
        try:
            divergence = float(divergence_raw) if divergence_raw is not None else 0.0
        except (TypeError, ValueError):
            divergence = 0.0
        if divergence < min_value:
            candidates.append({
                "universe_id": u.universe_id,
                "divergence": divergence,
                "branch_depth": u.branch_depth,
            })

    killed = 0
    if not dry_run and candidates:
        cand_ids = [c["universe_id"] for c in candidates]
        rows_to_kill = (
            await session.execute(
                select(UniverseModel).where(
                    UniverseModel.universe_id.in_(cand_ids),
                )
            )
        ).scalars().all()
        bn_rows = (
            await session.execute(
                select(BranchNodeModel).where(
                    BranchNodeModel.universe_id.in_(cand_ids),
                )
            )
        ).scalars().all()
        bn_by_uid = {bn.universe_id: bn for bn in bn_rows}

        now = datetime.now(UTC)
        for u in rows_to_kill:
            u.status = "killed"
            if hasattr(u, "killed_at"):
                u.killed_at = now
            bn = bn_by_uid.get(u.universe_id)
            if bn is not None:
                bn.status = "killed"
            killed += 1
        await session.flush()

    return {
        "big_bang_id": big_bang_id,
        "candidates": candidates,
        "killed": killed,
        "dry_run": dry_run,
        "min_value": min_value,
    }
