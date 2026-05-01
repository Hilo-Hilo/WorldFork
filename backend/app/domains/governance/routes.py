from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import GodAgentReviewOut, GodRegenerateSummaryOut, ToolCallOut
from app.api.utils import require
from app.db import models
from app.db.session import get_db

router = APIRouter(tags=["god-agent"])


@router.get("/god-reviews/{god_review_id}", response_model=GodAgentReviewOut)
def get(god_review_id: UUID, db: Session = Depends(get_db)):
    return require(db, models.GodAgentReview, god_review_id)


@router.get("/god-reviews/{god_review_id}/tool-calls", response_model=list[ToolCallOut])
def tool_calls(god_review_id: UUID, db: Session = Depends(get_db)):
    require(db, models.GodAgentReview, god_review_id)
    return db.scalars(select(models.ToolCall).where(models.ToolCall.god_review_id == god_review_id)).all()


@router.post("/god-reviews/{god_review_id}/regenerate-summary", response_model=GodRegenerateSummaryOut)
def regenerate_summary(god_review_id: UUID, db: Session = Depends(get_db)):
    review = require(db, models.GodAgentReview, god_review_id)
    return {"god_review_id": review.id, "status": "queued", "message": "Summary regeneration job stub recorded."}
