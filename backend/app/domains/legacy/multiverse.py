"""Multiverse REST API — §20.3.

Endpoints:
  GET  /api/multiverse/{big_bang_id}/tree
  GET  /api/multiverse/{big_bang_id}/dag
  GET  /api/multiverse/{big_bang_id}/metrics
  POST /api/multiverse/{big_bang_id}/prune
  POST /api/multiverse/{big_bang_id}/focus-branch
  POST /api/multiverse/{big_bang_id}/compare
  POST /api/multiverse/{big_bang_id}/simulate-next-tick
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.clock import now_utc
from backend.app.core.db import get_session
from backend.app.models.branches import BranchNodeModel
from backend.app.models.runs import BigBangRunModel
from backend.app.models.settings import BranchPolicySettingModel
from backend.app.models.universes import UniverseModel
from backend.app.schemas.api import (
    CompareRequest,
    CompareResponse,
    FocusBranchRequest,
    MultiverseDagResponse,
    MultiverseEdge,
    MultiverseMetricsResponse,
    MultiverseTreeNode,
    MultiverseTreeResponse,
    PruneRequest,
    PruneResponse,
)
from backend.app.workers.scheduler import enqueue, make_envelope

router = APIRouter(prefix="/api/multiverse", tags=["multiverse"])

DbSession = Annotated[AsyncSession, Depends(get_session)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_run_or_404(big_bang_id: str, session: AsyncSession) -> BigBangRunModel:
    result = await session.execute(
        select(BigBangRunModel).where(BigBangRunModel.big_bang_id == big_bang_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Big Bang run {big_bang_id!r} not found.")
    return row


async def _fetch_all_universes(
    big_bang_id: str, session: AsyncSession
) -> list[UniverseModel]:
    result = await session.execute(
        select(UniverseModel)
        .where(UniverseModel.big_bang_id == big_bang_id)
        .order_by(UniverseModel.branch_depth, UniverseModel.created_at)
    )
    return list(result.scalars().all())


def _build_tree_nodes_and_edges(
    universes: list[UniverseModel],
) -> tuple[list[MultiverseTreeNode], list[MultiverseEdge]]:
    """Build nodes and edges lists from a flat universe list."""
    # Count descendants for each universe (simple in-Python pass).

    def _count_descendants(uid: str, children_map: dict[str, list[str]]) -> int:
        direct = children_map.get(uid, [])
        total = len(direct)
        for child in direct:
            total += _count_descendants(child, children_map)
        return total

    children_map: dict[str, list[str]] = {}
    for u in universes:
        if u.parent_universe_id:
            children_map.setdefault(u.parent_universe_id, []).append(u.universe_id)

    nodes: list[MultiverseTreeNode] = []
    edges: list[MultiverseEdge] = []

    for u in universes:
        desc_count = _count_descendants(u.universe_id, children_map)
        nodes.append(
            MultiverseTreeNode(
                universe_id=u.universe_id,
                parent_universe_id=u.parent_universe_id,
                depth=u.branch_depth,
                branch_from_tick=u.branch_from_tick,
                status=u.status,
                current_tick=u.current_tick,
                latest_metrics=dict(u.latest_metrics or {}),
                branch_reason=u.branch_reason or "",
                branch_delta=dict(u.branch_delta or {}),
                lineage_path=list(u.lineage_path or []),
                descendant_count=desc_count,
                created_at=u.created_at,
            )
        )
        if u.parent_universe_id:
            edges.append(
                MultiverseEdge(
                    source=u.parent_universe_id,
                    target=u.universe_id,
                    branch_tick=u.branch_from_tick,
                )
            )

    return nodes, edges


# ---------------------------------------------------------------------------
# GET /api/multiverse/{big_bang_id}/tree
# ---------------------------------------------------------------------------


@router.get("/{big_bang_id}/tree", response_model=MultiverseTreeResponse)
async def get_tree(big_bang_id: str, session: DbSession) -> MultiverseTreeResponse:
    """Return the full multiverse tree as nodes + edges."""
    run = await _get_run_or_404(big_bang_id, session)
    universes = await _fetch_all_universes(big_bang_id, session)
    nodes, edges = _build_tree_nodes_and_edges(universes)

    return MultiverseTreeResponse(
        big_bang_id=big_bang_id,
        max_ticks=run.max_ticks,
        nodes=nodes,
        edges=edges,
    )


# ---------------------------------------------------------------------------
# GET /api/multiverse/{big_bang_id}/dag
# ---------------------------------------------------------------------------


@router.get("/{big_bang_id}/dag", response_model=MultiverseDagResponse)
async def get_dag(big_bang_id: str, session: DbSession) -> MultiverseDagResponse:
    """Return the DAG representation (same data, different response wrapper)."""
    await _get_run_or_404(big_bang_id, session)
    universes = await _fetch_all_universes(big_bang_id, session)
    nodes, edges = _build_tree_nodes_and_edges(universes)

    return MultiverseDagResponse(
        big_bang_id=big_bang_id,
        nodes=nodes,
        edges=edges,
    )


# ---------------------------------------------------------------------------
# GET /api/multiverse/{big_bang_id}/metrics
# ---------------------------------------------------------------------------


@router.get("/{big_bang_id}/metrics", response_model=MultiverseMetricsResponse)
async def get_metrics(big_bang_id: str, session: DbSession) -> MultiverseMetricsResponse:
    """Return aggregated KPIs for the multiverse."""
    await _get_run_or_404(big_bang_id, session)

    # Active universes.
    active_stmt = select(func.count(UniverseModel.universe_id)).where(
        UniverseModel.big_bang_id == big_bang_id,
        UniverseModel.status == "active",
    )
    active = (await session.execute(active_stmt)).scalar_one()

    # Candidate universes.
    candidate_stmt = select(func.count(UniverseModel.universe_id)).where(
        UniverseModel.big_bang_id == big_bang_id,
        UniverseModel.status == "candidate",
    )
    candidate = (await session.execute(candidate_stmt)).scalar_one()

    # Max depth.
    depth_stmt = select(func.max(UniverseModel.branch_depth)).where(
        UniverseModel.big_bang_id == big_bang_id
    )
    max_depth = (await session.execute(depth_stmt)).scalar_one() or 0

    # Branches = universes with a parent (total - 1 for root, but safer to count directly).
    branch_stmt = select(func.count(UniverseModel.universe_id)).where(
        UniverseModel.big_bang_id == big_bang_id,
        UniverseModel.parent_universe_id.is_not(None),
    )
    total_branches = (await session.execute(branch_stmt)).scalar_one()

    policy = await session.get(BranchPolicySettingModel, "default")
    branch_budget_pct = (
        round((total_branches / policy.max_total_branches) * 100, 2)
        if policy is not None and policy.max_total_branches > 0
        else 0.0
    )
    branch_budget_limit = policy.max_total_branches if policy is not None else 0

    max_tick_stmt = select(func.max(UniverseModel.current_tick)).where(
        UniverseModel.big_bang_id == big_bang_id
    )
    max_tick = (await session.execute(max_tick_stmt)).scalar_one() or 0
    active_branches_per_tick = round(total_branches / max(1, int(max_tick)), 2)

    return MultiverseMetricsResponse(
        big_bang_id=big_bang_id,
        active_universes=active,
        total_branches=total_branches,
        max_depth=max_depth,
        candidate_branches=candidate,
        branch_budget_pct=branch_budget_pct,
        branch_budget_used=total_branches,
        branch_budget_limit=branch_budget_limit,
        active_branches_per_tick=active_branches_per_tick,
    )


# ---------------------------------------------------------------------------
# POST /api/multiverse/{big_bang_id}/simulate-next-tick
# ---------------------------------------------------------------------------


@router.post("/{big_bang_id}/simulate-next-tick", status_code=202)
async def simulate_next_tick(big_bang_id: str, session: DbSession) -> dict:
    """Queue one tick for every active universe in a run."""
    run = await _get_run_or_404(big_bang_id, session)
    result = await session.execute(
        select(UniverseModel).where(
            UniverseModel.big_bang_id == big_bang_id,
            UniverseModel.status == "active",
        )
    )
    universes = list(result.scalars().all())
    job_ids: list[str] = []
    failed: list[dict] = []
    skipped: list[dict] = []
    for uni in universes:
        target_tick = uni.current_tick + 1
        if target_tick > run.max_ticks:
            skipped.append(
                {
                    "universe_id": uni.universe_id,
                    "reason": "run max_ticks reached",
                    "current_tick": uni.current_tick,
                    "max_ticks": run.max_ticks,
                }
            )
            continue
        envelope = make_envelope(
            job_type="simulate_universe_tick",
            run_id=big_bang_id,
            universe_id=uni.universe_id,
            tick=target_tick,
            payload={
                "run_id": big_bang_id,
                "universe_id": uni.universe_id,
                "tick": target_tick,
            },
        )
        try:
            await enqueue(envelope)
            job_ids.append(envelope.job_id)
        except Exception as exc:
            failed.append({"universe_id": uni.universe_id, "error": str(exc)[:300]})

    return {
        "big_bang_id": big_bang_id,
        "enqueued": len(job_ids),
        "job_ids": job_ids,
        "failed": failed,
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# POST /api/multiverse/{big_bang_id}/prune
# ---------------------------------------------------------------------------


@router.post("/{big_bang_id}/prune", response_model=PruneResponse)
async def prune_multiverse(
    big_bang_id: str, body: PruneRequest, session: DbSession
) -> PruneResponse:
    """Mark low-value universes as 'killed'.

    Value scoring uses BranchNode.metrics_summary['value'] if available,
    otherwise falls back to 0.0 (will be pruned if min_value > 0).
    """
    await _get_run_or_404(big_bang_id, session)

    # Fetch branch nodes for this run.
    branch_result = await session.execute(
        select(BranchNodeModel)
        .join(
            UniverseModel,
            BranchNodeModel.universe_id == UniverseModel.universe_id,
        )
        .where(UniverseModel.big_bang_id == big_bang_id)
    )
    branch_nodes = branch_result.scalars().all()

    # Fetch universes to update.
    universes_result = await session.execute(
        select(UniverseModel).where(
            UniverseModel.big_bang_id == big_bang_id,
            UniverseModel.status.in_(["candidate", "frozen"]),
        )
    )
    prunable_universes = universes_result.scalars().all()

    # Map universe_id → branch node for value lookup.
    branch_map = {bn.universe_id: bn for bn in branch_nodes}

    pruned_ids: list[str] = []
    now = now_utc()

    for uni in prunable_universes:
        bn = branch_map.get(uni.universe_id)
        value = 0.0
        if bn is not None:
            value = float((bn.metrics_summary or {}).get("value", 0.0))

        if value < body.min_value:
            pruned_ids.append(uni.universe_id)
            if not body.dry_run:
                uni.status = "killed"
                uni.killed_at = now

    if not body.dry_run and pruned_ids:
        await session.commit()

    return PruneResponse(
        dry_run=body.dry_run,
        pruned_universe_ids=pruned_ids,
        pruned_count=len(pruned_ids),
    )


# ---------------------------------------------------------------------------
# POST /api/multiverse/{big_bang_id}/focus-branch
# ---------------------------------------------------------------------------


@router.post("/{big_bang_id}/focus-branch", response_model=MultiverseTreeResponse)
async def focus_branch(
    big_bang_id: str, body: FocusBranchRequest, session: DbSession
) -> MultiverseTreeResponse:
    """Return the subtree rooted at the given universe."""
    run = await _get_run_or_404(big_bang_id, session)

    # Verify the focal universe exists and belongs to this run.
    focal_result = await session.execute(
        select(UniverseModel).where(
            UniverseModel.universe_id == body.universe_id,
            UniverseModel.big_bang_id == big_bang_id,
        )
    )
    focal = focal_result.scalar_one_or_none()
    if focal is None:
        raise HTTPException(
            status_code=404,
            detail=f"Universe {body.universe_id!r} not found in run {big_bang_id!r}.",
        )

    # Fetch all universes and filter to those whose lineage_path contains focal.
    all_universes = await _fetch_all_universes(big_bang_id, session)

    # Keep only universes that are descendants (lineage includes focal).
    subtree = [
        u for u in all_universes
        if body.universe_id in (u.lineage_path or [])
    ]

    nodes, edges = _build_tree_nodes_and_edges(subtree)

    return MultiverseTreeResponse(
        big_bang_id=big_bang_id,
        max_ticks=run.max_ticks,
        nodes=nodes,
        edges=edges,
    )


# ---------------------------------------------------------------------------
# POST /api/multiverse/{big_bang_id}/compare
# ---------------------------------------------------------------------------


@router.post("/{big_bang_id}/compare", response_model=CompareResponse)
async def compare_universes(
    big_bang_id: str, body: CompareRequest, session: DbSession
) -> CompareResponse:
    """Return side-by-side metric comparison for the requested universes."""
    await _get_run_or_404(big_bang_id, session)

    result = await session.execute(
        select(UniverseModel).where(
            UniverseModel.universe_id.in_(body.universe_ids),
            UniverseModel.big_bang_id == big_bang_id,
        )
    )
    universes = result.scalars().all()

    found_ids = {u.universe_id for u in universes}
    missing = [uid for uid in body.universe_ids if uid not in found_ids]
    if missing:
        raise HTTPException(
            status_code=404,
            detail=f"Universes not found or not in this run: {missing}",
        )

    comparison: list[dict] = []
    for u in universes:
        entry: dict = {
            "universe_id": u.universe_id,
            "status": u.status,
            "current_tick": u.current_tick,
            "branch_depth": u.branch_depth,
            "branch_from_tick": u.branch_from_tick,
        }
        # Add aspect-specific fields.
        if body.aspect == "metrics":
            entry["metrics"] = dict(u.latest_metrics or {})
        elif body.aspect == "status":
            entry["frozen_at"] = u.frozen_at.isoformat() if u.frozen_at else None
            entry["killed_at"] = u.killed_at.isoformat() if u.killed_at else None
            entry["completed_at"] = u.completed_at.isoformat() if u.completed_at else None
        else:
            entry["metrics"] = dict(u.latest_metrics or {})

        comparison.append(entry)

    return CompareResponse(aspect=body.aspect, comparison=comparison)
