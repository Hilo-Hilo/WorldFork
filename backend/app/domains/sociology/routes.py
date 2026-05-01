from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import SociologyPromptInfluenceOut, SociologySignalOut
from app.api.utils import require
from app.db import models
from app.db.session import get_db

router = APIRouter(tags=["sociology"])


@router.get("/multiverses/{multiverse_id}/sociology-signals", response_model=list[SociologySignalOut])
def multiverse_signals(multiverse_id: UUID, db: Session = Depends(get_db)):
    require(db, models.Multiverse, multiverse_id)
    return db.scalars(select(models.SociologySignal).where(models.SociologySignal.multiverse_id == multiverse_id)).all()


@router.get("/actors/{actor_id}/sociology-prompt-influences", response_model=list[SociologyPromptInfluenceOut])
def actor_influences(actor_id: UUID, db: Session = Depends(get_db)):
    require(db, models.Actor, actor_id)
    return db.scalars(select(models.SociologyPromptInfluence).where(models.SociologyPromptInfluence.actor_id == actor_id)).all()
