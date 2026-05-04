from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.domains.costs.service import CostEstimateRequest, estimate_pre_big_bang_cost

router = APIRouter(prefix="/costs", tags=["costs"])


@router.post("/estimate")
def estimate(payload: CostEstimateRequest | None = None, db: Session = Depends(get_db)):
    return estimate_pre_big_bang_cost(db, request=payload or CostEstimateRequest())
