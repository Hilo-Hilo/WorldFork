from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    BigBangCreate,
    BigBangOut,
    BigBangPatch,
    ReportOut,
    ReportRequest,
    ReportVersionOut,
    RunUntilCompleteOut,
    RunUntilCompleteRequest,
)
from app.api.utils import commit_or_500, raise_llm_unavailable, require
from app.db import models
from app.db.session import get_db
from app.llm.audit import LLMCallError
from app.domains.big_bang.initializer import create_big_bang
from app.domains.report.engine import generate_final_big_bang_report
from app.domains.big_bang.run_orchestrator import run_big_bang_until_complete
from app.domains.tick.tick_runner import TERMINAL_MULTIVERSE_STATUSES

router = APIRouter(prefix="/big-bangs", tags=["big-bangs"])


@router.get("", response_model=list[BigBangOut])
def list_big_bangs(db: Session = Depends(get_db)):
    return db.scalars(select(models.BigBang).order_by(models.BigBang.created_at.desc())).all()


@router.post("", response_model=BigBangOut, status_code=201)
def create(payload: BigBangCreate, db: Session = Depends(get_db)):
    try:
        big_bang = create_big_bang(db, payload)
    except LLMCallError as exc:
        db.rollback()
        raise_llm_unavailable(exc)
    commit_or_500(db)
    return big_bang


@router.get("/{big_bang_id}", response_model=BigBangOut)
def get(big_bang_id: UUID, db: Session = Depends(get_db)):
    return require(db, models.BigBang, big_bang_id)


@router.delete("/{big_bang_id}", response_model=BigBangOut)
def archive(big_bang_id: UUID, db: Session = Depends(get_db)):
    big_bang = require(db, models.BigBang, big_bang_id)
    big_bang.status = "archived"
    now = datetime.now(timezone.utc)
    multiverses = db.scalars(
        select(models.Multiverse).where(models.Multiverse.big_bang_id == big_bang_id)
    ).all()
    terminated_ids: list[str] = []
    for multiverse in multiverses:
        if multiverse.status not in TERMINAL_MULTIVERSE_STATUSES:
            multiverse.status = "terminated"
            multiverse.report_status = "ready"
            multiverse.ended_at = now
            terminated_ids.append(str(multiverse.id))
    db.add(
        models.OperationLog(
            big_bang_id=big_bang.id,
            event_type="big_bang_archived",
            level="info",
            body={
                "archived_multiverses": len(multiverses),
                "terminated_non_terminal_multiverses": terminated_ids,
            },
        )
    )
    commit_or_500(db)
    return big_bang


@router.patch("/{big_bang_id}", response_model=BigBangOut)
def patch(big_bang_id: UUID, payload: BigBangPatch, db: Session = Depends(get_db)):
    big_bang = require(db, models.BigBang, big_bang_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(big_bang, key, value)
    commit_or_500(db)
    return big_bang


@router.post("/{big_bang_id}/start", response_model=BigBangOut)
def start(big_bang_id: UUID, db: Session = Depends(get_db)):
    big_bang = require(db, models.BigBang, big_bang_id)
    big_bang.status = "running"
    commit_or_500(db)
    return big_bang


@router.post("/{big_bang_id}/pause", response_model=BigBangOut)
def pause(big_bang_id: UUID, db: Session = Depends(get_db)):
    big_bang = require(db, models.BigBang, big_bang_id)
    big_bang.status = "paused"
    commit_or_500(db)
    return big_bang


@router.post("/{big_bang_id}/resume", response_model=BigBangOut)
def resume(big_bang_id: UUID, db: Session = Depends(get_db)):
    big_bang = require(db, models.BigBang, big_bang_id)
    big_bang.status = "running"
    commit_or_500(db)
    return big_bang


@router.get("/{big_bang_id}/reports", response_model=list[ReportOut])
def reports(big_bang_id: UUID, db: Session = Depends(get_db)):
    require(db, models.BigBang, big_bang_id)
    return db.scalars(
        select(models.Report).where(models.Report.big_bang_id == big_bang_id).order_by(models.Report.created_at)
    ).all()


@router.post("/{big_bang_id}/reports/final", response_model=ReportVersionOut)
def final_report(big_bang_id: UUID, payload: ReportRequest | None = None, db: Session = Depends(get_db)):
    request = payload or ReportRequest()
    big_bang = require(db, models.BigBang, big_bang_id)
    reject_archived_big_bang(big_bang)
    reject_non_terminal_multiverses(db, big_bang)
    try:
        version = generate_final_big_bang_report(db, big_bang=big_bang, title=request.title, summary=request.summary)
    except LLMCallError as exc:
        db.rollback()
        raise_llm_unavailable(exc)
    big_bang.status = "completed"
    commit_or_500(db)
    return version


@router.post("/{big_bang_id}/run-until-complete", response_model=RunUntilCompleteOut)
def run_until_complete(big_bang_id: UUID, payload: RunUntilCompleteRequest | None = None, db: Session = Depends(get_db)):
    request = payload or RunUntilCompleteRequest()
    big_bang = require(db, models.BigBang, big_bang_id)
    reject_archived_big_bang(big_bang)
    try:
        result = run_big_bang_until_complete(db, big_bang=big_bang, max_total_ticks=request.max_total_ticks)
    except LLMCallError as exc:
        db.rollback()
        raise_llm_unavailable(exc)
    except ValueError as exc:
        db.rollback()
        raise_domain_conflict(exc)
    commit_or_500(db)
    return result


def reject_non_terminal_multiverses(db: Session, big_bang: models.BigBang) -> None:
    non_terminal = db.scalars(
        select(models.Multiverse)
        .where(
            models.Multiverse.big_bang_id == big_bang.id,
            models.Multiverse.status.notin_(TERMINAL_MULTIVERSE_STATUSES),
        )
        .order_by(models.Multiverse.ui_label)
    ).all()
    if non_terminal:
        labels = ", ".join(item.ui_label for item in non_terminal[:5])
        suffix = f": {labels}" if labels else ""
        raise_domain_conflict(ValueError(f"final report requires terminal multiverses{suffix}"))


def reject_archived_big_bang(big_bang: models.BigBang) -> None:
    if getattr(big_bang, "status", None) == "archived":
        raise_domain_conflict(ValueError("big bang is archived"))


def raise_domain_conflict(exc: ValueError):
    raise HTTPException(status_code=409, detail=str(exc) or "request conflicts with simulation state") from exc
