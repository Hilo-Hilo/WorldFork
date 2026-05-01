from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.utils import require
from app.db import models
from app.db.session import get_db

router = APIRouter(tags=["actors"])


@router.get("/big-bangs/{big_bang_id}/actors")
def list_actors(big_bang_id: UUID, db: Session = Depends(get_db)):
    require(db, models.BigBang, big_bang_id)
    return db.scalars(select(models.Actor).where(models.Actor.big_bang_id == big_bang_id).order_by(models.Actor.name)).all()


@router.get("/actors/{actor_id}")
def get(actor_id: UUID, db: Session = Depends(get_db)):
    return require(db, models.Actor, actor_id)


@router.get("/actors/{actor_id}/timeline")
def timeline(actor_id: UUID, db: Session = Depends(get_db)):
    actor = require(db, models.Actor, actor_id)
    return {"actor": actor, "cohort_states": db.scalars(select(models.CohortState).where(models.CohortState.actor_id == actor_id)).all(), "hero_states": db.scalars(select(models.HeroState).where(models.HeroState.actor_id == actor_id)).all()}


@router.get("/actors/{actor_id}/events")
def events(actor_id: UUID, db: Session = Depends(get_db)):
    require(db, models.Actor, actor_id)
    return db.scalars(select(models.Event).where(models.Event.creator_actor_id == actor_id)).all()


@router.get("/actors/{actor_id}/graphs")
def graphs(actor_id: UUID, db: Session = Depends(get_db)):
    require(db, models.Actor, actor_id)
    return db.scalars(select(models.GraphEdge).where((models.GraphEdge.source_actor_id == actor_id) | (models.GraphEdge.target_actor_id == actor_id))).all()


@router.get("/actors/{actor_id}/sociology-signals")
def sociology(actor_id: UUID, db: Session = Depends(get_db)):
    require(db, models.Actor, actor_id)
    return db.scalars(select(models.SociologySignal).where(models.SociologySignal.actor_id == actor_id)).all()
