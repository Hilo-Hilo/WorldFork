from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.schemas import BigBangCreate
from app.api.utils import commit_or_500, raise_llm_unavailable
from app.db.session import get_db
from app.llm.audit import LLMCallError
from app.domains.big_bang.initializer import create_big_bang
from app.domains.big_bang.scenario_bank import COVERAGE_MATRIX, SCENARIO_FORMAT, get_scenario, list_scenarios, scenario_to_big_bang_payload

router = APIRouter(prefix="/scenario-bank", tags=["scenario-bank"])


@router.get("")
def scenarios(category: str | None = None, test: str | None = None):
    return {"format": SCENARIO_FORMAT, "scenarios": list_scenarios(category=category, test=test)}


@router.get("/coverage-matrix")
def coverage_matrix():
    return COVERAGE_MATRIX


@router.get("/{scenario_id}")
def scenario(scenario_id: str):
    item = get_scenario(scenario_id)
    if not item:
        raise HTTPException(status_code=404, detail="scenario not found")
    return item


@router.get("/{scenario_id}/big-bang-payload")
def big_bang_payload(scenario_id: str):
    payload = scenario_to_big_bang_payload(scenario_id)
    if not payload:
        raise HTTPException(status_code=404, detail="scenario not found")
    return payload


@router.post("/{scenario_id}/big-bang")
def create_big_bang_from_scenario(scenario_id: str, db: Session = Depends(get_db)):
    payload = scenario_to_big_bang_payload(scenario_id)
    if not payload:
        raise HTTPException(status_code=404, detail="scenario not found")
    try:
        big_bang = create_big_bang(db, BigBangCreate(**payload))
    except LLMCallError as exc:
        db.rollback()
        raise_llm_unavailable(exc)
    commit_or_500(db)
    return {"big_bang_id": str(big_bang.id), "scenario_id": scenario_id}
