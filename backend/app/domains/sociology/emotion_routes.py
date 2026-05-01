from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.utils import require
from app.db import models
from app.db.session import get_db

router = APIRouter(tags=["emotion-observability"])


@router.get("/big-bangs/{big_bang_id}/emotion-observability")
def big_bang_emotion(big_bang_id: UUID, db: Session = Depends(get_db)):
    require(db, models.BigBang, big_bang_id)
    return db.scalars(select(models.EmotionGraphSnapshot).where(models.EmotionGraphSnapshot.big_bang_id == big_bang_id)).all()


@router.get("/multiverses/{multiverse_id}/emotion-observability")
def multiverse_emotion(multiverse_id: UUID, db: Session = Depends(get_db)):
    require(db, models.Multiverse, multiverse_id)
    return db.scalars(select(models.EmotionGraphSnapshot).where(models.EmotionGraphSnapshot.multiverse_id == multiverse_id)).all()


@router.get("/actors/{actor_id}/emotion-observability")
def actor_emotion(actor_id: UUID, db: Session = Depends(get_db)):
    require(db, models.Actor, actor_id)
    return db.scalars(select(models.EmotionObservation).where(models.EmotionObservation.actor_id == actor_id)).all()
