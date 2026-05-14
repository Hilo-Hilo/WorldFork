from __future__ import annotations

import time
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models
from app.db.session import get_db
from app.domains.costs.service import CostEstimateRequest, estimate_big_bang_cost, summarize_big_bang_cost
from app.domains.jobs.queues import JOB_TYPES
from app.domains.tick.timing import run_timing_payload
from app.domains.big_bang.scenario_bank import COVERAGE_MATRIX, list_scenarios
from app.domains.tick.tick_bundles import (
    TickBundleHydrationContext,
    hydrate_tick_snapshot_for_read,
)

router = APIRouter(prefix="/agent", tags=["agent"])

VERBOSITY_TIERS = ("summary", "normal", "full")
DEFAULT_VERBOSITY = "summary"
_TICK_ARG_MISSING = object()

PROJECTION_SCHEMAS: dict[str, dict[str, list[str] | None]] = {
    "run": {
        "summary": ["id", "name", "status", "created_at", "multiverse_count"],
        "normal": [
            "id",
            "name",
            "description",
            "status",
            "created_at",
            "updated_at",
            "multiverse_count",
            "active_multiverse_count",
        ],
        "full": None,
    },
    "multiverse": {
        "summary": ["id", "ui_label", "status", "depth", "fork_tick_index", "path_probability"],
        "normal": [
            "id",
            "ui_label",
            "status",
            "depth",
            "fork_tick_index",
            "parent_multiverse_id",
            "branch_reason",
            "branch_probability",
            "path_probability",
            "report_status",
        ],
        "full": None,
    },
    "tick": {
        "summary": ["id", "tick_index", "status", "summary"],
        "normal": ["id", "tick_index", "ui_label", "status", "summary", "created_at", "updated_at"],
        "full": None,
    },
    "actor": {
        "summary": ["actor_id", "actor_kind", "name"],
        "normal": ["actor_id", "actor_kind", "name", "state", "state_delta"],
        "full": None,
    },
    "job": {
        "summary": ["id", "job_type", "status", "big_bang_id", "created_at"],
        "normal": ["id", "job_type", "status", "big_bang_id", "error", "result", "created_at", "updated_at"],
        "full": None,
    },
    "log": {
        "summary": ["id", "source", "status", "message", "created_at"],
        "normal": ["id", "source", "status", "message", "big_bang_id", "provider", "model", "created_at"],
        "full": None,
    },
}


class AgentWaitRequest(BaseModel):
    timeout_seconds: float = Field(default=30, ge=0, le=3600)
    poll_interval_seconds: float = Field(default=1, gt=0, le=30)


class AgentModelPatch(BaseModel):
    default_model: str | None = None
    agent_models: dict[str, str] = Field(default_factory=dict)


def _ok(data: Any, **meta: Any) -> dict[str, Any]:
    return {"ok": True, "data": jsonable_encoder(data), "meta": meta}


def _fields(value: str | None) -> list[str] | None:
    if not value:
        return None
    keys = [item.strip() for item in value.split(",") if item.strip()]
    return keys or None


def _projection_keys(surface: str, verbosity: str, fields: str | None) -> list[str] | None:
    explicit = _fields(fields)
    if explicit is not None:
        return explicit
    return PROJECTION_SCHEMAS.get(surface, {}).get(verbosity)


def _project(row: dict[str, Any], keys: list[str] | None) -> dict[str, Any]:
    if keys is None:
        return row
    return {key: row[key] for key in keys if key in row}


def _project_rows(rows: list[dict[str, Any]], surface: str, verbosity: str, fields: str | None) -> list[dict[str, Any]]:
    keys = _projection_keys(surface, verbosity, fields)
    return [_project(row, keys) for row in rows]


def _row(model: Any) -> dict[str, Any]:
    return jsonable_encoder(model)


def _tick_row(
    db: Session,
    tick: models.TickSnapshot,
    *,
    context: TickBundleHydrationContext | None = None,
) -> dict[str, Any]:
    return _row(hydrate_tick_snapshot_for_read(db, tick, context=context))


def _require_verbosity(value: str) -> str:
    if value not in VERBOSITY_TIERS:
        raise HTTPException(status_code=422, detail=f"verbosity must be one of {', '.join(VERBOSITY_TIERS)}")
    return value


def _run_counts(db: Session, run_ids: list[UUID]) -> tuple[dict[str, int], dict[str, int]]:
    if not run_ids:
        return {}, {}
    total_rows = db.execute(
        select(models.Multiverse.big_bang_id, func.count(models.Multiverse.id))
        .where(models.Multiverse.big_bang_id.in_(run_ids))
        .group_by(models.Multiverse.big_bang_id)
    ).all()
    active_rows = db.execute(
        select(models.Multiverse.big_bang_id, func.count(models.Multiverse.id))
        .where(models.Multiverse.big_bang_id.in_(run_ids), models.Multiverse.status == "active")
        .group_by(models.Multiverse.big_bang_id)
    ).all()
    return (
        {str(run_id): int(count) for run_id, count in total_rows},
        {str(run_id): int(count) for run_id, count in active_rows},
    )


def _run_row(run: models.BigBang, total_counts: dict[str, int], active_counts: dict[str, int]) -> dict[str, Any]:
    run_id = str(run.id)
    row = _row(run)
    row["multiverse_count"] = total_counts.get(run_id, 0)
    row["active_multiverse_count"] = active_counts.get(run_id, 0)
    return row


def _latest_tick(db: Session, multiverse_id: UUID) -> models.TickSnapshot | None:
    return db.scalar(
        select(models.TickSnapshot)
        .where(models.TickSnapshot.multiverse_id == multiverse_id)
        .order_by(models.TickSnapshot.tick_index.desc())
        .limit(1)
    )


def _state_at_tick(
    db_or_tick: Session | models.TickSnapshot | None,
    tick: models.TickSnapshot | None | object = _TICK_ARG_MISSING,
) -> dict[str, Any]:
    if tick is _TICK_ARG_MISSING:
        db = None
        tick_row = db_or_tick
    else:
        db = db_or_tick
        tick_row = tick
    if tick_row is None:
        return {}
    if not isinstance(tick_row, models.TickSnapshot):
        return {}
    if isinstance(db, Session):
        hydrated = hydrate_tick_snapshot_for_read(db, tick_row)
        bundle = hydrated.final_bundle or hydrated.provisional_bundle or {}
    else:
        bundle = tick_row.final_bundle or tick_row.provisional_bundle or {}
    if isinstance(bundle, dict):
        for key in ("state_after", "state", "universe_state_after"):
            value = bundle.get(key)
            if isinstance(value, dict):
                return value
        sociology_result = bundle.get("sociology_result")
        if isinstance(sociology_result, dict):
            cohorts = sociology_result.get("cohort_state_updates")
            heroes = sociology_result.get("hero_state_updates")
            state: dict[str, Any] = {}
            if isinstance(cohorts, list):
                state["cohorts"] = cohorts
            if isinstance(heroes, list):
                state["heroes"] = heroes
            if state:
                return state
    return {}


def _actor_rows_from_state(state: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for actor_kind, key, id_key in (
        ("cohort", "cohorts", "cohort_id"),
        ("hero", "heroes", "hero_id"),
        ("actor", "actors", "actor_id"),
    ):
        values = state.get(key)
        if not isinstance(values, list):
            continue
        for index, item in enumerate(values):
            if not isinstance(item, dict):
                continue
            actor_id = str(item.get(id_key) or item.get("actor_id") or item.get("id") or f"{actor_kind}:{index}")
            rows.append(
                {
                    "actor_id": actor_id,
                    "actor_kind": actor_kind,
                    "name": item.get("name") or item.get("actor_name") or actor_id,
                    "state": item,
                    "state_delta": {},
                }
            )
    return rows


@router.get("/status")
def status(db: Session = Depends(get_db)):
    settings = get_settings()
    run_count = db.scalar(select(func.count()).select_from(models.BigBang)) or 0
    job_count = db.scalar(select(func.count()).select_from(models.Job)) or 0
    return _ok(
        {
            "status": "ok",
            "app_name": settings.app_name,
            "api_prefix": settings.api_prefix,
            "schema_version": "agent-v1",
            "run_count": run_count,
            "job_count": job_count,
        }
    )


@router.get("/discover")
def discover():
    settings = get_settings()
    return _ok(
        {
            "root_command": "worldfork",
            "api_prefix": settings.api_prefix,
            "verbosity_tiers": list(VERBOSITY_TIERS),
            "default_verbosity": DEFAULT_VERBOSITY,
            "job_types": sorted(JOB_TYPES),
            "scenario_bank": {
                "scenarios": list_scenarios(),
                "coverage_matrix": COVERAGE_MATRIX,
            },
            "recommended_flow": [
                "worldfork agent discover",
                "worldfork status",
                "worldfork setup",
                "worldfork settings llm",
                "worldfork init --name <name> --scenario-file <path>",
                "worldfork watch big-bang <big-bang-id>",
                "worldfork runs list",
                "worldfork runs workspace <run-id>",
                "worldfork runs delete <run-id>",
                "worldfork jobs list --status failed",
                "worldfork logs list --status failed",
            ],
            "llm_config": {
                "endpoint": f"{settings.api_prefix}/settings/llm",
                "providers_endpoint": f"{settings.api_prefix}/settings/providers",
                "model_routing_endpoint": f"{settings.api_prefix}/settings/model-routing",
                "rate_limits_endpoint": f"{settings.api_prefix}/settings/rate-limits",
                "cli": "worldfork settings llm",
                "setup_cli": "worldfork setup",
            },
        }
    )


@router.get("/runs")
def runs(
    db: Session = Depends(get_db),
    status: str | None = None,
    q: str | None = None,
    include_archived: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    verbosity: str = DEFAULT_VERBOSITY,
    fields: str | None = None,
):
    verbosity = _require_verbosity(verbosity)
    stmt = select(models.BigBang)
    if status:
        stmt = stmt.where(models.BigBang.status == status)
    elif not include_archived:
        stmt = stmt.where(models.BigBang.status != "archived")
    if q:
        stmt = stmt.where(models.BigBang.name.ilike(f"%{q}%"))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(models.BigBang.created_at.desc()).limit(limit).offset(offset)).all()
    total_counts, active_counts = _run_counts(db, [row.id for row in rows])
    data = _project_rows([_run_row(row, total_counts, active_counts) for row in rows], "run", verbosity, fields)
    return _ok(data, total=total, limit=limit, offset=offset, verbosity=verbosity)


@router.get("/runs/{run_id}/workspace")
def workspace(
    run_id: UUID,
    db: Session = Depends(get_db),
    verbosity: str = DEFAULT_VERBOSITY,
    fields: str | None = None,
):
    verbosity = _require_verbosity(verbosity)
    run = db.get(models.BigBang, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    multiverses = db.scalars(
        select(models.Multiverse).where(models.Multiverse.big_bang_id == run_id).order_by(models.Multiverse.ui_label)
    ).all()
    ticks = db.scalars(
        select(models.TickSnapshot)
        .where(models.TickSnapshot.big_bang_id == run_id)
        .order_by(models.TickSnapshot.created_at.desc())
        .limit(50)
    ).all()
    jobs = db.scalars(
        select(models.Job).where(models.Job.big_bang_id == run_id).order_by(models.Job.created_at.desc()).limit(25)
    ).all()
    total_counts, active_counts = _run_counts(db, [run.id])
    tick_keys = _projection_keys("tick", verbosity, None)
    needs_tick_hydration = tick_keys is None or bool({"provisional_bundle", "final_bundle"} & set(tick_keys))
    tick_context = TickBundleHydrationContext()
    latest_tick_rows = [
        _tick_row(db, item, context=tick_context) if needs_tick_hydration else _row(item)
        for item in ticks
    ]
    data = {
        "run": _project(_run_row(run, total_counts, active_counts), _projection_keys("run", verbosity, fields)),
        "multiverses": _project_rows([_row(item) for item in multiverses], "multiverse", verbosity, None),
        "latest_ticks": _project_rows(latest_tick_rows, "tick", verbosity, None),
        "recent_jobs": _project_rows([_row(item) for item in jobs], "job", verbosity, None),
    }
    return _ok(data, verbosity=verbosity)


@router.get("/runs/{run_id}/timing")
def run_timing(run_id: UUID, db: Session = Depends(get_db)):
    run = db.get(models.BigBang, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return _ok(run_timing_payload(db, run))


@router.get("/runs/{run_id}/cost")
def run_cost(
    run_id: UUID,
    include_calls: bool = False,
    include_non_openrouter: bool = True,
    db: Session = Depends(get_db),
):
    run = db.get(models.BigBang, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return _ok(
        summarize_big_bang_cost(
            db,
            big_bang=run,
            include_calls=include_calls,
            include_non_openrouter=include_non_openrouter,
        )
    )


@router.post("/runs/{run_id}/cost-estimate")
def run_cost_estimate(
    run_id: UUID,
    payload: CostEstimateRequest | None = None,
    db: Session = Depends(get_db),
):
    run = db.get(models.BigBang, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return _ok(estimate_big_bang_cost(db, big_bang=run, request=payload or CostEstimateRequest()))


@router.get("/universes/{multiverse_id}/trace")
def trace(
    multiverse_id: UUID,
    db: Session = Depends(get_db),
    tick: int | None = None,
    actor_id: str | None = None,
    actor_kind: str | None = None,
    verbosity: str = DEFAULT_VERBOSITY,
    fields: str | None = None,
):
    verbosity = _require_verbosity(verbosity)
    multiverse = db.get(models.Multiverse, multiverse_id)
    if multiverse is None:
        raise HTTPException(status_code=404, detail=f"universe {multiverse_id} not found")
    stmt = select(models.TickSnapshot).where(models.TickSnapshot.multiverse_id == multiverse_id)
    if tick is not None:
        stmt = stmt.where(models.TickSnapshot.tick_index == tick)
    tick_row = db.scalar(stmt.order_by(models.TickSnapshot.tick_index.desc()).limit(1))
    state = _state_at_tick(db, tick_row)
    actors = _actor_rows_from_state(state)
    if actor_id:
        actors = [row for row in actors if row["actor_id"] == actor_id]
    if actor_kind:
        actors = [row for row in actors if row["actor_kind"] == actor_kind]
    data = {
        "universe_id": str(multiverse_id),
        "tick": tick_row.tick_index if tick_row else tick,
        "actor_count": len(actors),
        "actors": _project_rows(actors, "actor", verbosity, fields),
    }
    if verbosity == "full":
        data["state"] = state
        data["tick_snapshot"] = _tick_row(db, tick_row) if tick_row else None
    return _ok(data, verbosity=verbosity)


@router.get("/cohorts/{cohort_id}/transcript")
def cohort_transcript(
    cohort_id: str,
    multiverse_id: UUID,
    db: Session = Depends(get_db),
    from_tick: int = Query(default=0, ge=0),
    to_tick: int = Query(default=10, ge=0),
    verbosity: str = DEFAULT_VERBOSITY,
    fields: str | None = None,
):
    verbosity = _require_verbosity(verbosity)
    if from_tick > to_tick:
        raise HTTPException(status_code=422, detail="from_tick must be <= to_tick")
    rows = db.scalars(
        select(models.TickSnapshot)
        .where(
            models.TickSnapshot.multiverse_id == multiverse_id,
            models.TickSnapshot.tick_index >= from_tick,
            models.TickSnapshot.tick_index <= to_tick,
        )
        .order_by(models.TickSnapshot.tick_index)
    ).all()
    transcript: list[dict[str, Any]] = []
    for tick_row in rows:
        match = next(
            (
                row
                for row in _actor_rows_from_state(_state_at_tick(db, tick_row))
                if row["actor_id"] == cohort_id
            ),
            None,
        )
        transcript.append({"tick": tick_row.tick_index, **(match or {"missing": True})})
    keys = _projection_keys("actor", verbosity, fields)
    projected = []
    for row in transcript:
        projected.append({"tick": row["tick"], **_project(row, keys)})
    return _ok(projected, verbosity=verbosity, from_tick=from_tick, to_tick=to_tick)


@router.get("/jobs")
def jobs(
    db: Session = Depends(get_db),
    run_id: UUID | None = None,
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    verbosity: str = DEFAULT_VERBOSITY,
    fields: str | None = None,
):
    verbosity = _require_verbosity(verbosity)
    stmt = select(models.Job)
    if run_id is not None:
        stmt = stmt.where(models.Job.big_bang_id == run_id)
    if status:
        stmt = stmt.where(models.Job.status == status)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(models.Job.created_at.desc()).limit(limit).offset(offset)).all()
    data = _project_rows([_row(row) for row in rows], "job", verbosity, fields)
    return _ok(data, total=total, limit=limit, offset=offset, verbosity=verbosity)


@router.post("/jobs/{job_id}/wait")
def wait_for_job(job_id: UUID, body: AgentWaitRequest, db: Session = Depends(get_db)):
    deadline = time.monotonic() + body.timeout_seconds
    terminal = {"succeeded", "completed", "failed", "cancelled", "dead_lettered", "interrupted"}
    while True:
        job = db.get(models.Job, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"job {job_id} not found")
        if job.status in terminal:
            return _ok(_row(job), terminal=True, timed_out=False)
        if time.monotonic() >= deadline:
            return _ok(_row(job), terminal=False, timed_out=True)
        db.rollback()
        time.sleep(body.poll_interval_seconds)
        db.expire_all()


@router.get("/logs")
def logs(
    db: Session = Depends(get_db),
    run_id: UUID | None = None,
    status: str | None = None,
    source: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    verbosity: str = DEFAULT_VERBOSITY,
    fields: str | None = None,
):
    verbosity = _require_verbosity(verbosity)
    rows: list[dict[str, Any]] = []
    per_source_limit = offset + limit
    if source in (None, "job"):
        stmt = select(models.Job)
        if run_id is not None:
            stmt = stmt.where(models.Job.big_bang_id == run_id)
        if status:
            stmt = stmt.where(models.Job.status == status)
        for job in db.scalars(stmt.order_by(models.Job.created_at.desc()).limit(per_source_limit)).all():
            rows.append(
                {
                    "id": str(job.id),
                    "source": "job",
                    "status": job.status,
                    "message": job.error or job.job_type,
                    "big_bang_id": str(job.big_bang_id) if job.big_bang_id else None,
                    "created_at": job.created_at,
                }
            )
    if source in (None, "llm"):
        stmt = select(models.LLMCall)
        if run_id is not None:
            stmt = stmt.where(models.LLMCall.big_bang_id == run_id)
        if status:
            stmt = stmt.where(models.LLMCall.status == status)
        for call in db.scalars(stmt.order_by(models.LLMCall.created_at.desc()).limit(per_source_limit)).all():
            rows.append(
                {
                    "id": str(call.id),
                    "source": "llm",
                    "status": call.status,
                    "message": call.purpose,
                    "big_bang_id": str(call.big_bang_id) if call.big_bang_id else None,
                    "provider": call.provider,
                    "model": call.model,
                    "created_at": call.created_at,
                }
            )
    rows.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    page = rows[offset : offset + limit]
    return _ok(_project_rows(page, "log", verbosity, fields), limit=limit, offset=offset, verbosity=verbosity)


@router.get("/models")
def models_route():
    settings = get_settings()
    return _ok(
        {
            "default_model": settings.default_model,
            "default_provider": settings.default_llm_provider,
            "agent_models": {
                "initializer_agent": settings.initializer_agent_model,
                "god_agent": settings.god_agent_model,
                "cohort_agent": settings.cohort_agent_model,
                "hero_agent": settings.hero_agent_model,
                "event_summary": settings.event_summary_model,
                "predicate_extractor": settings.default_model,
                "predicate_resolver": settings.default_model,
                "single_report_agent": settings.default_model,
                "final_report_agent": settings.final_report_agent_model,
                "report_agent": settings.report_agent_model,
            },
            "mutable": True,
            "settings_endpoint": f"{settings.api_prefix}/settings/llm",
            "model_routing_endpoint": f"{settings.api_prefix}/settings/model-routing",
            "note": "Mutable provider/model routing is available through /api/settings/model-routing.",
        }
    )


@router.patch("/models")
def patch_models(_body: AgentModelPatch):
    raise HTTPException(
        status_code=409,
        detail="patch /api/settings/model-routing to update mutable provider/model routing",
    )
