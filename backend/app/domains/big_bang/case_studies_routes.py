from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.utils import commit_or_500
from app.db.session import get_db
from app.domains.big_bang.case_studies import (
    TRANSIT_SHOCK_EXPECTATIONS,
    diff_case_study_actuals,
    load_case_study_run,
    run_transit_shock_case_study,
)

router = APIRouter(prefix="/case-studies", tags=["case-studies"])


class CaseStudyRunRequest(BaseModel):
    max_total_ticks: int = Field(default=5, ge=1, le=20)


@router.get("/transit-shock/expectations")
def transit_shock_expectations():
    return TRANSIT_SHOCK_EXPECTATIONS


@router.post("/transit-shock/run")
def run_transit_shock(payload: CaseStudyRunRequest | None = None, db: Session = Depends(get_db)):
    request = payload or CaseStudyRunRequest()
    result = run_transit_shock_case_study(db, max_total_ticks=request.max_total_ticks)
    commit_or_500(db)
    return result


@router.get("/{run_id}")
def case_study_run(run_id: str):
    result = load_case_study_run(run_id)
    if not result:
        raise HTTPException(status_code=404, detail="case study run not found")
    return result


@router.get("/{run_id}/expectations")
def case_study_expectations(run_id: str):
    result = load_case_study_run(run_id)
    if not result:
        raise HTTPException(status_code=404, detail="case study run not found")
    return result["expectations"]


@router.get("/{run_id}/actuals")
def case_study_actuals(run_id: str):
    result = load_case_study_run(run_id)
    if not result:
        raise HTTPException(status_code=404, detail="case study run not found")
    return result["actuals"]


@router.get("/{run_id}/diff")
def case_study_diff(run_id: str):
    result = load_case_study_run(run_id)
    if not result:
        raise HTTPException(status_code=404, detail="case study run not found")
    return result["diff"]


@router.post("/{run_id}/iterate")
def case_study_iteration_plan(run_id: str):
    result = load_case_study_run(run_id)
    if not result:
        raise HTTPException(status_code=404, detail="case study run not found")
    current_diff = diff_case_study_actuals(result["actuals"], result["expectations"])
    return {
        "run_id": run_id,
        "passed": current_diff["passed"],
        "next_iteration_actions": current_diff["next_iteration_actions"],
        "failures": current_diff["failures"],
    }
