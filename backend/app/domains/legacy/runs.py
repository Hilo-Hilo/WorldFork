"""Runs REST API — §20.1.

Endpoints:
  POST   /api/runs
  GET    /api/runs
  GET    /api/runs/{run_id}
  PATCH  /api/runs/{run_id}
  POST   /api/runs/{run_id}/archive
  POST   /api/runs/{run_id}/duplicate
  POST   /api/runs/{run_id}/export
  GET    /api/runs/{run_id}/source-of-truth
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.legacy.compat_db import table_has_columns
from backend.app.core.db import get_session
from backend.app.core.ids import new_id
from backend.app.models.runs import BigBangRunModel
from backend.app.models.results import RunResultModel
from backend.app.models.universes import UniverseModel
from backend.app.schemas.api import (
    CreateRunRequest,
    CreateRunResponse,
    PatchRunRequest,
    RunResultsRegenerateResponse,
    RunResultsResponse,
    RunDetail,
    RunListItem,
    RunListResponse,
    SoTBundleResponse,
)
from backend.app.workers.scheduler import enqueue, make_envelope, mark_failed

router = APIRouter(prefix="/api/runs", tags=["runs"])

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DbSession = Annotated[AsyncSession, Depends(get_session)]


async def _get_run_or_404(run_id: str, session: AsyncSession) -> BigBangRunModel:
    result = await session.execute(
        select(BigBangRunModel).where(BigBangRunModel.big_bang_id == run_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")
    return row


async def _find_run_by_idempotency_key(
    session: AsyncSession,
    idempotency_key: str,
) -> BigBangRunModel | None:
    bind = session.get_bind()
    dialect_name = bind.dialect.name if bind is not None else ""
    if dialect_name == "sqlite":
        rows = (await session.execute(select(BigBangRunModel))).scalars().all()
        return next(
            (
                row
                for row in rows
                if (row.safe_edit_metadata or {}).get("idempotency_key") == idempotency_key
            ),
            None,
        )

    from sqlalchemy import String as SAString
    from sqlalchemy import cast

    existing_result = await session.execute(
        select(BigBangRunModel).where(
            cast(
                BigBangRunModel.safe_edit_metadata["idempotency_key"],
                SAString,
            )
            == f'"{idempotency_key}"'
        )
    )
    return existing_result.scalar_one_or_none()


async def _legacy_universe_table_available(session: AsyncSession) -> bool:
    return await table_has_columns(
        session,
        "universes",
        ["universe_id", "big_bang_id", "status", "latest_metrics"],
    )


async def _universe_counts_for_runs(
    session: AsyncSession,
    run_ids: list[str],
) -> tuple[dict[str, int], dict[str, int]]:
    if not run_ids or not await _legacy_universe_table_available(session):
        return {}, {}

    total_rows = (
        await session.execute(
            select(UniverseModel.big_bang_id, func.count(UniverseModel.universe_id))
            .where(UniverseModel.big_bang_id.in_(run_ids))
            .group_by(UniverseModel.big_bang_id)
        )
    ).all()
    total_counts = {run_id: int(count) for run_id, count in total_rows}

    active_rows = (
        await session.execute(
            select(UniverseModel.big_bang_id, func.count(UniverseModel.universe_id))
            .where(
                UniverseModel.big_bang_id.in_(run_ids),
                UniverseModel.status == "active",
            )
            .group_by(UniverseModel.big_bang_id)
        )
    ).all()
    active_counts = {run_id: int(count) for run_id, count in active_rows}
    return total_counts, active_counts


async def _run_universe_counts(
    session: AsyncSession,
    run_id: str,
    root_universe_id: str,
) -> tuple[int, int, dict]:
    if not await _legacy_universe_table_available(session):
        return 0, 0, {}

    total_stmt = select(func.count(UniverseModel.universe_id)).where(
        UniverseModel.big_bang_id == run_id
    )
    active_stmt = select(func.count(UniverseModel.universe_id)).where(
        UniverseModel.big_bang_id == run_id,
        UniverseModel.status == "active",
    )
    total_count = int((await session.execute(total_stmt)).scalar_one())
    active_count = int((await session.execute(active_stmt)).scalar_one())

    root_metrics: dict = {}
    root_stmt = select(UniverseModel).where(UniverseModel.universe_id == root_universe_id)
    root_result = await session.execute(root_stmt)
    root_uni = root_result.scalar_one_or_none()
    if root_uni is not None:
        root_metrics = dict(root_uni.latest_metrics or {})
    return total_count, active_count, root_metrics


# ---------------------------------------------------------------------------
# POST /api/runs
# ---------------------------------------------------------------------------


@router.post("", status_code=202, response_model=CreateRunResponse)
async def create_run(
    body: CreateRunRequest,
    session: DbSession,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CreateRunResponse:
    """Create a new Big Bang run and enqueue initialization.

    If an ``Idempotency-Key`` header is provided and a run with that key
    already exists, the existing run is returned without re-enqueuing.
    """
    # Deduplicate on idempotency key stored in safe_edit_metadata.
    if idempotency_key:
        existing = await _find_run_by_idempotency_key(session, idempotency_key)
        if existing is not None:
            return CreateRunResponse(
                run_id=existing.big_bang_id,
                root_universe_id=existing.root_universe_id,
                status=existing.status,
                enqueued=existing.status not in {"failed", "draft"},
            )

    big_bang_id = new_id("run")
    root_universe_id = new_id("uni")

    safe_meta: dict = {}
    if idempotency_key:
        safe_meta["idempotency_key"] = idempotency_key

    run = BigBangRunModel(
        big_bang_id=big_bang_id,
        display_name=body.display_name,
        scenario_text=body.scenario_text,
        input_file_ids=list(body.uploaded_doc_ids),
        status="initializing",
        time_horizon_label=body.time_horizon_label,
        tick_duration_minutes=body.tick_duration_minutes,
        max_ticks=body.max_ticks,
        max_schedule_horizon_ticks=body.max_schedule_horizon_ticks,
        source_of_truth_version="pending",
        source_of_truth_snapshot_path="",
        provider_snapshot_id=body.provider_snapshot_id or "",
        root_universe_id=root_universe_id,
        run_folder_path="",
        safe_edit_metadata=safe_meta,
    )
    session.add(run)
    await session.commit()

    if not await _legacy_universe_table_available(session):
        enqueue_error = (
            "legacy /api/runs initializer is unavailable on the canonical runtime schema; "
            "use /api/big-bangs or /api/agent surfaces"
        )
        run.status = "failed"
        run.safe_edit_metadata = {
            **safe_meta,
            "failure_reason": enqueue_error,
        }
        await session.commit()
        return CreateRunResponse(
            run_id=big_bang_id,
            root_universe_id=root_universe_id,
            status="failed",
            enqueued=False,
            degraded=True,
            error=enqueue_error,
        )

    # Enqueue initialize_big_bang via Celery.
    envelope = make_envelope(
        job_type="initialize_big_bang",
        run_id=big_bang_id,
        payload={
            "display_name": body.display_name,
            "scenario_text": body.scenario_text,
            "time_horizon_label": body.time_horizon_label,
            "tick_duration_minutes": body.tick_duration_minutes,
            "max_ticks": body.max_ticks,
            "max_schedule_horizon_ticks": body.max_schedule_horizon_ticks,
            "uploaded_doc_ids": list(body.uploaded_doc_ids),
            "uploaded_docs": [
                {
                    "name": doc_id,
                    "content_text": "",
                    "content_type": "uploaded_doc_id",
                }
                for doc_id in body.uploaded_doc_ids
            ],
            "provider_snapshot_id": body.provider_snapshot_id,
            "root_universe_id": root_universe_id,
        },
        idempotency_key=idempotency_key,
    )
    enqueued = False
    enqueue_error: str | None = None
    try:
        await enqueue(envelope)
        enqueued = True
    except Exception as exc:
        enqueue_error = str(exc)
        run.status = "failed"
        run.safe_edit_metadata = {
            **safe_meta,
            "failure_reason": f"initializer enqueue failed: {enqueue_error[:500]}",
        }
        await session.commit()

    return CreateRunResponse(
        run_id=big_bang_id,
        root_universe_id=root_universe_id,
        status="initializing" if enqueued else "failed",
        job_id=envelope.job_id,
        enqueued=enqueued,
        degraded=not enqueued,
        error=enqueue_error,
    )


# ---------------------------------------------------------------------------
# GET /api/runs
# ---------------------------------------------------------------------------


@router.get("", response_model=RunListResponse)
async def list_runs(
    session: DbSession,
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query()] = None,
) -> RunListResponse:
    """Paginated, filterable list of Big Bang runs."""
    stmt = select(BigBangRunModel)

    if status:
        stmt = stmt.where(BigBangRunModel.status == status)
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(
            BigBangRunModel.display_name.ilike(pattern)
            | BigBangRunModel.scenario_text.ilike(pattern)
        )

    count_stmt = select(func.count()).select_from(stmt.subquery())
    total_result = await session.execute(count_stmt)
    total = total_result.scalar_one()

    stmt = stmt.order_by(BigBangRunModel.created_at.desc()).limit(limit).offset(offset)
    rows_result = await session.execute(stmt)
    rows = rows_result.scalars().all()
    run_ids = [r.big_bang_id for r in rows]
    total_counts, active_counts = await _universe_counts_for_runs(session, run_ids)

    items = [
        RunListItem(
            run_id=r.big_bang_id,
            display_name=r.display_name,
            status=r.status,
            scenario_text=r.scenario_text,
            time_horizon_label=r.time_horizon_label,
            tick_duration_minutes=r.tick_duration_minutes,
            max_ticks=r.max_ticks,
            created_at=r.created_at,
            updated_at=r.updated_at,
            favorite=bool((r.safe_edit_metadata or {}).get("favorite", False)),
            archived=bool((r.safe_edit_metadata or {}).get("archived", False)),
            root_universe_id=r.root_universe_id,
            active_universe_count=active_counts.get(r.big_bang_id, 0),
            total_universe_count=total_counts.get(r.big_bang_id, 0),
        )
        for r in rows
    ]

    return RunListResponse(items=items, total=total, limit=limit, offset=offset)


# ---------------------------------------------------------------------------
# GET /api/runs/{run_id}
# ---------------------------------------------------------------------------


@router.get("/{run_id}", response_model=RunDetail)
async def get_run(run_id: str, session: DbSession) -> RunDetail:
    """Return detailed information about a single run."""
    run = await _get_run_or_404(run_id, session)
    total_count, active_count, root_metrics = await _run_universe_counts(
        session,
        run_id,
        run.root_universe_id,
    )

    meta = dict(run.safe_edit_metadata or {})

    return RunDetail(
        run_id=run.big_bang_id,
        display_name=run.display_name,
        description=meta.get("description"),
        status=run.status,
        scenario_text=run.scenario_text,
        time_horizon_label=run.time_horizon_label,
        tick_duration_minutes=run.tick_duration_minutes,
        max_ticks=run.max_ticks,
        max_schedule_horizon_ticks=run.max_schedule_horizon_ticks,
        root_universe_id=run.root_universe_id,
        run_folder_path=run.run_folder_path or None,
        source_of_truth_version=run.source_of_truth_version or None,
        provider_snapshot_id=run.provider_snapshot_id or None,
        created_at=run.created_at,
        updated_at=run.updated_at,
        active_universe_count=active_count,
        total_universe_count=total_count,
        latest_metrics=root_metrics,
        safe_edit_metadata=meta,
    )


# ---------------------------------------------------------------------------
# GET /api/runs/{run_id}/results
# ---------------------------------------------------------------------------


def _result_to_response(row: RunResultModel) -> RunResultsResponse:
    return RunResultsResponse(
        run_id=row.run_id,
        status=row.status,
        generated_at=row.generated_at,
        provider=row.provider,
        model_used=row.model_used,
        summary=row.summary,
        classifications=dict(row.classifications or {}),
        branch_clusters=list(row.branch_clusters or []),
        universe_outcomes=list(row.universe_outcomes or []),
        timeline_highlights=list(row.timeline_highlights or []),
        metrics=dict(row.metrics or {}),
        artifact_path=row.artifact_path,
        error=row.error,
        job_id=row.job_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/{run_id}/results", response_model=RunResultsResponse)
async def get_run_results(run_id: str, session: DbSession) -> RunResultsResponse:
    """Return the current run-level results aggregation state."""
    await _get_run_or_404(run_id, session)
    row = await session.get(RunResultModel, run_id)
    if row is None:
        return RunResultsResponse(
            run_id=run_id,
            status="not_started",
            summary=None,
        )
    return _result_to_response(row)


@router.post("/{run_id}/results/regenerate", status_code=202, response_model=RunResultsRegenerateResponse)
async def regenerate_run_results(run_id: str, session: DbSession) -> RunResultsRegenerateResponse:
    """Queue a god-tier results aggregation job for this run."""
    await _get_run_or_404(run_id, session)
    envelope = make_envelope(
        job_type="aggregate_run_results",
        run_id=run_id,
        payload={"run_id": run_id, "reason": "manual_regenerate"},
        idempotency_key=f"aggregate_run_results:{run_id}:manual:{new_id('regen')}",
    )
    row = await session.get(RunResultModel, run_id)
    if row is None:
        row = RunResultModel(
            run_id=run_id,
            status="pending",
            classifications={},
            branch_clusters=[],
            universe_outcomes=[],
            timeline_highlights=[],
            metrics={},
        )
        session.add(row)
    row.status = "pending"
    row.error = None
    row.job_id = envelope.job_id
    await session.commit()
    await enqueue(envelope)
    return RunResultsRegenerateResponse(
        run_id=run_id,
        job_id=envelope.job_id,
        status="queued",
        enqueued=True,
    )


# ---------------------------------------------------------------------------
# PATCH /api/runs/{run_id}
# ---------------------------------------------------------------------------


@router.patch("/{run_id}", response_model=RunDetail)
async def patch_run(
    run_id: str, body: PatchRunRequest, session: DbSession
) -> RunDetail:
    """Partially update safe metadata fields for a run.

    Only display_name, description, tags, favorite, and archived are allowed.
    Extra fields cause a 422 from Pydantic (extra="forbid").
    """
    run = await _get_run_or_404(run_id, session)
    meta = dict(run.safe_edit_metadata or {})

    if body.display_name is not None:
        run.display_name = body.display_name
    if body.description is not None:
        meta["description"] = body.description
    if body.tags is not None:
        meta["tags"] = body.tags
    if body.favorite is not None:
        meta["favorite"] = body.favorite
    if body.archived is not None:
        meta["archived"] = body.archived

    run.safe_edit_metadata = meta
    await session.commit()
    await session.refresh(run)

    return await get_run(run_id, session)


# ---------------------------------------------------------------------------
# POST /api/runs/{run_id}/archive
# ---------------------------------------------------------------------------


@router.post("/{run_id}/archive", status_code=204)
async def archive_run(run_id: str, session: DbSession) -> None:
    """Mark a run as archived."""
    run = await _get_run_or_404(run_id, session)
    meta = dict(run.safe_edit_metadata or {})
    meta["archived"] = True
    run.safe_edit_metadata = meta
    await session.commit()


# ---------------------------------------------------------------------------
# POST /api/runs/{run_id}/duplicate
# ---------------------------------------------------------------------------


@router.post("/{run_id}/duplicate", status_code=201)
async def duplicate_run(run_id: str, session: DbSession) -> dict:
    """Clone a run row.  Does NOT enqueue initialization; user must POST /init."""
    original = await _get_run_or_404(run_id, session)

    new_big_bang_id = new_id("run")
    new_root_universe_id = new_id("uni")
    original_meta = dict(original.safe_edit_metadata or {})
    new_meta: dict = {k: v for k, v in original_meta.items() if k != "idempotency_key"}

    clone = BigBangRunModel(
        big_bang_id=new_big_bang_id,
        display_name=f"Copy of {original.display_name}",
        scenario_text=original.scenario_text,
        input_file_ids=list(original.input_file_ids or []),
        status="draft",
        time_horizon_label=original.time_horizon_label,
        tick_duration_minutes=original.tick_duration_minutes,
        max_ticks=original.max_ticks,
        max_schedule_horizon_ticks=original.max_schedule_horizon_ticks,
        source_of_truth_version=original.source_of_truth_version,
        source_of_truth_snapshot_path="",
        provider_snapshot_id=original.provider_snapshot_id,
        root_universe_id=new_root_universe_id,
        run_folder_path="",
        safe_edit_metadata=new_meta,
    )
    session.add(clone)
    await session.commit()

    return {"run_id": new_big_bang_id, "root_universe_id": new_root_universe_id, "status": "draft"}


# ---------------------------------------------------------------------------
# POST /api/runs/{run_id}/export
# ---------------------------------------------------------------------------


@router.post("/{run_id}/export", status_code=202)
async def export_run(run_id: str, session: DbSession) -> dict:
    """Enqueue an export_run job and return the job_id."""
    await _get_run_or_404(run_id, session)

    envelope = make_envelope(
        job_type="export_run",
        run_id=run_id,
        payload={"run_id": run_id},
    )
    job_id = envelope.job_id
    enqueued = False
    enqueue_error: str | None = None
    try:
        await enqueue(envelope)
        enqueued = True
    except Exception as exc:
        enqueue_error = str(exc)
        await mark_failed(job_id, f"export enqueue failed: {enqueue_error}")

    if not enqueued:
        return {
            "job_id": job_id,
            "status": "failed",
            "enqueued": False,
            "error": enqueue_error,
        }

    return {"job_id": job_id, "status": "queued", "enqueued": True}


# ---------------------------------------------------------------------------
# GET /api/runs/{run_id}/source-of-truth
# ---------------------------------------------------------------------------


@router.get("/{run_id}/source-of-truth", response_model=SoTBundleResponse)
async def get_source_of_truth(run_id: str, session: DbSession) -> SoTBundleResponse:
    """Return the SoT snapshot bundle for a run.

    First attempts to read from the run's ledger snapshot directory.
    Falls back to the live source_of_truth/ directory if ledger is not yet
    initialised (draft/initializing runs).
    """
    run = await _get_run_or_404(run_id, session)

    from backend.app.storage.sot_loader import SoTBundle, load_sot

    sot: SoTBundle | None = None

    # Try ledger snapshot path.
    if run.source_of_truth_snapshot_path:
        from pathlib import Path

        snapshot_path = Path(run.source_of_truth_snapshot_path)
        if snapshot_path.exists():
            try:
                sot = load_sot(snapshot_path)
            except Exception:
                sot = None

    if sot is None:
        try:
            sot = load_sot()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=f"Source-of-truth not available: {exc}",
            ) from exc

    return SoTBundleResponse(
        version=sot.version,
        emotions=sot.emotions,
        behavior_axes=sot.behavior_axes,
        ideology_axes=sot.ideology_axes,
        expression_scale=sot.expression_scale,
        issue_stance_axes=sot.issue_stance_axes,
        event_types=sot.event_types,
        social_action_tools=sot.social_action_tools,
        channel_types=sot.channel_types,
        actor_types=sot.actor_types,
        sociology_parameters=sot.sociology_parameters,
    )
