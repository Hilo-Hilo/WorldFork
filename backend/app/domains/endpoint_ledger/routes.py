from __future__ import annotations

from uuid import UUID, uuid4

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.jobs import create_job_record
from app.api.schemas import (
    EndpointLedgerDetailOut,
    EndpointLedgerEvaluateOut,
    EndpointLedgerEvaluateRequest,
    EndpointLedgerVersionOut,
    JobCreate,
)
from app.api.utils import commit_or_500, raise_llm_unavailable, require
from app.db import models
from app.db.session import get_db
from app.llm.audit import LLMCallError
from app.domains.endpoint_ledger.service import endpoint_ledger_detail, evaluate_endpoint_ledger

router = APIRouter(tags=["endpoint-ledgers"])


@router.get("/big-bangs/{big_bang_id}/endpoint-ledgers", response_model=list[EndpointLedgerVersionOut])
def list_big_bang_ledgers(big_bang_id: UUID, db: Session = Depends(get_db)):
    require(db, models.BigBang, big_bang_id)
    return db.scalars(
        select(models.EndpointLedgerVersion)
        .where(models.EndpointLedgerVersion.big_bang_id == big_bang_id)
        .order_by(models.EndpointLedgerVersion.created_at.desc())
    ).all()


@router.get("/multiverses/{multiverse_id}/endpoint-ledgers", response_model=list[EndpointLedgerVersionOut])
def list_multiverse_ledgers(multiverse_id: UUID, db: Session = Depends(get_db)):
    require(db, models.Multiverse, multiverse_id)
    return db.scalars(
        select(models.EndpointLedgerVersion)
        .where(models.EndpointLedgerVersion.multiverse_id == multiverse_id)
        .order_by(models.EndpointLedgerVersion.version.desc(), models.EndpointLedgerVersion.created_at.desc())
    ).all()


@router.get("/endpoint-ledgers/{ledger_version_id}", response_model=EndpointLedgerDetailOut)
def get_ledger(ledger_version_id: UUID, db: Session = Depends(get_db)):
    ledger = require(db, models.EndpointLedgerVersion, ledger_version_id)
    return endpoint_ledger_detail(db, ledger)


@router.post("/big-bangs/{big_bang_id}/endpoint-ledgers/evaluate", response_model=EndpointLedgerEvaluateOut)
def evaluate_big_bang_ledger(
    big_bang_id: UUID,
    payload: EndpointLedgerEvaluateRequest | None = None,
    db: Session = Depends(get_db),
):
    request = payload or EndpointLedgerEvaluateRequest()
    big_bang = require(db, models.BigBang, big_bang_id)
    if request.run_inline:
        try:
            ledger = evaluate_endpoint_ledger(
                db,
                big_bang=big_bang,
                source_type="posthoc_api_inline",
                candidate_endpoint=request.candidate_endpoint,
            )
        except LLMCallError as exc:
            db.rollback()
            raise_llm_unavailable(exc)
        commit_or_500(db)
        return {"job_id": None, "status": "completed", "ledger_version_id": ledger.id}
    job = create_job_record(
        JobCreate(
            job_type="evaluate_endpoint_ledger",
            big_bang_id=big_bang_id,
            payload={
                "big_bang_id": str(big_bang_id),
                "scope": "big_bang",
                "candidate_endpoint": request.candidate_endpoint,
            },
            idempotency_key=request.idempotency_key or f"endpoint-ledger:big-bang:{big_bang_id}:{uuid4()}",
        ),
        db=db,
    )
    return {"job_id": job.id, "status": job.status, "ledger_version_id": None}


@router.post("/multiverses/{multiverse_id}/endpoint-ledgers/evaluate", response_model=EndpointLedgerEvaluateOut)
def evaluate_multiverse_ledger(
    multiverse_id: UUID,
    payload: EndpointLedgerEvaluateRequest | None = None,
    db: Session = Depends(get_db),
):
    request = payload or EndpointLedgerEvaluateRequest()
    multiverse = require(db, models.Multiverse, multiverse_id)
    big_bang = require(db, models.BigBang, multiverse.big_bang_id)
    if request.run_inline:
        try:
            ledger = evaluate_endpoint_ledger(
                db,
                big_bang=big_bang,
                multiverse=multiverse,
                source_type="posthoc_api_inline",
                candidate_endpoint=request.candidate_endpoint,
            )
        except LLMCallError as exc:
            db.rollback()
            raise_llm_unavailable(exc)
        commit_or_500(db)
        return {"job_id": None, "status": "completed", "ledger_version_id": ledger.id}
    job = create_job_record(
        JobCreate(
            job_type="evaluate_endpoint_ledger",
            big_bang_id=multiverse.big_bang_id,
            payload={
                "big_bang_id": str(multiverse.big_bang_id),
                "multiverse_id": str(multiverse.id),
                "scope": "multiverse",
                "candidate_endpoint": request.candidate_endpoint,
            },
            idempotency_key=request.idempotency_key or f"endpoint-ledger:multiverse:{multiverse.id}:{uuid4()}",
        ),
        db=db,
    )
    return {"job_id": job.id, "status": job.status, "ledger_version_id": None}
