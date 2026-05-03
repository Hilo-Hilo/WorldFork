from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    MultiverseContinueRequest,
    MultiverseLineageOut,
    MultiverseOut,
    ReportRequest,
    ReportVersionOut,
    SimulateTickRequest,
    SimulateTicksRequest,
    TickSnapshotOut,
)
from app.api.utils import commit_or_500, raise_llm_unavailable, require
from app.db import models
from app.db.session import get_db
from app.llm.audit import LLMCallError
from app.domains.report.engine import generate_multiverse_report
from app.domains.big_bang.run_orchestrator import simulate_ticks
from app.domains.tick.tick_bundles import hydrate_tick_snapshot_for_read, hydrate_tick_snapshots_for_read
from app.domains.tick.tick_runner import TERMINAL_MULTIVERSE_STATUSES, run_next_tick
from app.storage.artifact_store import ArtifactStore

router = APIRouter(tags=["multiverses"])


@router.get("/big-bangs/{big_bang_id}/multiverses", response_model=list[MultiverseOut])
def list_for_big_bang(big_bang_id: UUID, db: Session = Depends(get_db)):
    require(db, models.BigBang, big_bang_id)
    return db.scalars(select(models.Multiverse).where(models.Multiverse.big_bang_id == big_bang_id).order_by(models.Multiverse.ui_label)).all()


@router.get("/multiverses/{multiverse_id}", response_model=MultiverseOut)
def get(multiverse_id: UUID, db: Session = Depends(get_db)):
    return require(db, models.Multiverse, multiverse_id)


@router.get("/multiverses/{multiverse_id}/lineage", response_model=MultiverseLineageOut)
def lineage(multiverse_id: UUID, db: Session = Depends(get_db)):
    multiverse = require(db, models.Multiverse, multiverse_id)
    universe_rows = db.scalars(
        select(models.Multiverse).where(models.Multiverse.big_bang_id == multiverse.big_bang_id)
    ).all()
    visible_ids = _visible_lineage_ids(universe_rows, multiverse.id)

    edges = db.scalars(
        select(models.MultiverseLineageEdge).where(
            models.MultiverseLineageEdge.big_bang_id == multiverse.big_bang_id,
            models.MultiverseLineageEdge.parent_multiverse_id.in_(visible_ids),
            models.MultiverseLineageEdge.child_multiverse_id.in_(visible_ids),
        )
    ).all()
    refs = db.scalars(select(models.TickLineageRef).where(models.TickLineageRef.child_multiverse_id == multiverse_id)).all()
    return {"multiverse": multiverse, "edges": edges, "inherited_ticks": refs}


def _visible_lineage_ids(universe_rows: list[Any], requested_id: UUID) -> set[UUID]:
    by_id = {row.id: row for row in universe_rows}
    children_by_parent: dict[UUID, list[UUID]] = {}
    for row in universe_rows:
        if row.parent_multiverse_id is not None:
            children_by_parent.setdefault(row.parent_multiverse_id, []).append(row.id)

    visible_ids: set[UUID] = set()
    cursor = by_id.get(requested_id)
    while cursor is not None and cursor.id not in visible_ids:
        visible_ids.add(cursor.id)
        cursor = by_id.get(cursor.parent_multiverse_id) if cursor.parent_multiverse_id else None

    queue = [requested_id]
    while queue:
        current_id = queue.pop()
        for child_id in children_by_parent.get(current_id, []):
            if child_id not in visible_ids:
                visible_ids.add(child_id)
                queue.append(child_id)
    return visible_ids


@router.get("/multiverses/{multiverse_id}/ticks", response_model=list[TickSnapshotOut])
def ticks(multiverse_id: UUID, db: Session = Depends(get_db)):
    require(db, models.Multiverse, multiverse_id)
    rows = db.scalars(
        select(models.TickSnapshot)
        .where(models.TickSnapshot.multiverse_id == multiverse_id)
        .order_by(models.TickSnapshot.tick_index)
    ).all()
    return hydrate_tick_snapshots_for_read(db, rows)


@router.post("/multiverses/{multiverse_id}/simulate-next-tick", response_model=TickSnapshotOut)
def simulate(
    multiverse_id: UUID,
    payload: SimulateTickRequest | None = None,
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
):
    multiverse = require(db, models.Multiverse, multiverse_id)
    idempotency_key = (payload.idempotency_key if payload else None) or idempotency_key_header
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header or payload idempotency_key is required")
    try:
        tick = run_next_tick(db, multiverse=multiverse, idempotency_key=idempotency_key, force=(payload.force if payload else False))
    except LLMCallError as exc:
        db.rollback()
        raise_llm_unavailable(exc)
    except ValueError as exc:
        db.rollback()
        raise_simulation_value_error(exc)
    commit_or_500(db)
    return hydrate_tick_snapshot_for_read(db, tick)


@router.post("/multiverses/{multiverse_id}/simulate-ticks", response_model=list[TickSnapshotOut])
def simulate_many(multiverse_id: UUID, payload: SimulateTicksRequest | None = None, db: Session = Depends(get_db)):
    request = payload or SimulateTicksRequest()
    multiverse = require(db, models.Multiverse, multiverse_id)
    try:
        ticks = simulate_ticks(db, multiverse=multiverse, count=request.count, raise_on_domain_error=True)
    except LLMCallError as exc:
        db.rollback()
        raise_llm_unavailable(exc)
    except ValueError as exc:
        db.rollback()
        raise_simulation_value_error(exc)
    commit_or_500(db)
    return hydrate_tick_snapshots_for_read(db, ticks)


@router.post("/multiverses/{multiverse_id}/terminate", response_model=MultiverseOut)
def terminate(multiverse_id: UUID, db: Session = Depends(get_db)):
    multiverse = require(db, models.Multiverse, multiverse_id)
    multiverse.status = "terminated"
    multiverse.report_status = "ready"
    multiverse.ended_at = datetime.now(timezone.utc)
    commit_or_500(db)
    return multiverse


@router.post("/multiverses/{multiverse_id}/continue", response_model=MultiverseOut)
def continue_multiverse(
    multiverse_id: UUID,
    payload: MultiverseContinueRequest,
    db: Session = Depends(get_db),
):
    multiverse = require(db, models.Multiverse, multiverse_id)
    if multiverse.status not in TERMINAL_MULTIVERSE_STATUSES:
        raise HTTPException(status_code=409, detail="only terminal multiverses can be continued")

    latest_tick = db.scalar(
        select(models.TickSnapshot)
        .where(models.TickSnapshot.multiverse_id == multiverse.id)
        .order_by(models.TickSnapshot.tick_index.desc())
        .limit(1)
    )
    latest_tick_index = int(latest_tick.tick_index) if latest_tick else 0
    if payload.max_ticks <= latest_tick_index:
        raise HTTPException(
            status_code=422,
            detail=f"max_ticks must be greater than latest tick index {latest_tick_index}",
        )

    big_bang = require(db, models.BigBang, multiverse.big_bang_id)
    if big_bang.status == "archived":
        raise HTTPException(status_code=409, detail="archived big bang cannot be continued")
    latest_config = db.scalar(
        select(models.BigBangConfig)
        .where(models.BigBangConfig.big_bang_id == big_bang.id)
        .order_by(models.BigBangConfig.version.desc())
        .limit(1)
    )
    if latest_config is None:
        raise HTTPException(status_code=409, detail="big bang has no simulation config")

    previous_version = int(multiverse.version or 1)
    source_report_version_id = _resolve_continuation_report_version_id(
        db,
        multiverse=multiverse,
        requested_report_version_id=payload.continued_from_report_version_id,
        expected_multiverse_version=previous_version,
    )

    next_config_version = max(int(big_bang.current_config_version or 0), int(latest_config.version or 0)) + 1
    simulation_config = {**(latest_config.simulation_config or {}), "max_ticks": payload.max_ticks}
    config_artifact = ArtifactStore().write_json(
        db,
        big_bang_id=big_bang.id,
        relative_path=f"big_bang_{big_bang.id}/configs/simulation_config_v{next_config_version}.json",
        payload={
            "simulation_config": simulation_config,
            "model_config": latest_config.model_config or {},
            "branch_policy": latest_config.branch_policy or {},
            "continued_multiverse_id": str(multiverse.id),
            "continued_from_report_version_id": str(source_report_version_id) if source_report_version_id else None,
            "reason": payload.reason,
            "scope": "multiverse_continuation",
        },
        kind="big_bang_config",
    )
    db.add(
        models.BigBangConfig(
            big_bang_id=big_bang.id,
            version=next_config_version,
            simulation_config=simulation_config,
            model_config=latest_config.model_config or {},
            branch_policy=latest_config.branch_policy or {},
            artifact_id=config_artifact.id,
        )
    )
    db.add(
        models.BigBangConfigVersion(
            big_bang_id=big_bang.id,
            version=next_config_version,
            simulation_config=simulation_config,
            model_config=latest_config.model_config or {},
            branch_policy=latest_config.branch_policy or {},
            artifact_id=config_artifact.id,
        )
    )

    multiverse.version = previous_version + 1
    multiverse.status = "active"
    multiverse.report_status = "not_ready"
    multiverse.ended_at = None
    multiverse.continued_from_report_version_id = source_report_version_id
    previous_state = multiverse.state or {}
    multiverse.state = {
        **previous_state,
        "runtime_config_version": next_config_version,
        "runtime_overrides": {
            **(previous_state.get("runtime_overrides") or {}),
            "config_version": next_config_version,
            "max_ticks": payload.max_ticks,
            "simulation_config": {
                **((previous_state.get("runtime_overrides") or {}).get("simulation_config") or {}),
                "max_ticks": payload.max_ticks,
            },
        },
        "continued_from": {
            "multiverse_version": previous_version,
            "report_version_id": str(source_report_version_id) if source_report_version_id else None,
            "config_version": next_config_version,
            "latest_tick_index": latest_tick_index,
            "reason": payload.reason,
            "new_max_ticks": payload.max_ticks,
        },
    }
    if big_bang.status == "completed":
        big_bang.status = "running"
    db.add(
        models.OperationLog(
            big_bang_id=big_bang.id,
            multiverse_id=multiverse.id,
            event_type="multiverse_continued",
            level="info",
            body={
                "from_version": previous_version,
                "to_version": multiverse.version,
                "latest_tick_index": latest_tick_index,
                "max_ticks": payload.max_ticks,
                "continued_from_report_version_id": str(source_report_version_id) if source_report_version_id else None,
                "reason": payload.reason,
            },
        )
    )
    commit_or_500(db)
    return multiverse


def _resolve_continuation_report_version_id(
    db: Session,
    *,
    multiverse: models.Multiverse,
    requested_report_version_id: UUID | None,
    expected_multiverse_version: int,
):
    report_version_id = requested_report_version_id or _latest_multiverse_report_version_id(db, multiverse)
    if report_version_id is None:
        return None
    report_version = db.scalar(
        select(models.ReportVersion)
        .join(models.Report, models.Report.id == models.ReportVersion.report_id)
        .where(
            models.ReportVersion.id == report_version_id,
            models.Report.big_bang_id == multiverse.big_bang_id,
            models.Report.multiverse_id == multiverse.id,
            models.Report.report_type == "multiverse",
        )
        .limit(1)
    )
    if report_version is None:
        raise HTTPException(
            status_code=422,
            detail="continued_from_report_version_id must belong to this multiverse report",
        )
    if (
        report_version.source_multiverse_version is not None
        and report_version.source_multiverse_version != expected_multiverse_version
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "continued_from_report_version_id must point to a report generated "
                f"for multiverse version {expected_multiverse_version}"
            ),
        )
    return report_version.id


def _latest_multiverse_report_version_id(db: Session, multiverse: models.Multiverse):
    report = db.scalar(
        select(models.Report)
        .where(models.Report.multiverse_id == multiverse.id, models.Report.report_type == "multiverse")
        .order_by(models.Report.updated_at.desc())
        .limit(1)
    )
    if report is None:
        return None
    version = db.scalar(
        select(models.ReportVersion)
        .where(models.ReportVersion.report_id == report.id)
        .order_by(models.ReportVersion.version.desc())
        .limit(1)
    )
    return version.id if version else None


@router.post("/multiverses/{multiverse_id}/report", response_model=ReportVersionOut)
def report(multiverse_id: UUID, payload: ReportRequest | None = None, db: Session = Depends(get_db)):
    request = payload or ReportRequest()
    multiverse = require(db, models.Multiverse, multiverse_id)
    version = generate_multiverse_report(db, multiverse=multiverse, title=request.title, summary=request.summary)
    commit_or_500(db)
    return version


def raise_simulation_value_error(exc: ValueError):
    detail = str(exc) or "simulation request cannot be completed"
    status_code = 422 if "must be" in detail.lower() or "invalid" in detail.lower() else 409
    raise HTTPException(status_code=status_code, detail=detail) from exc
