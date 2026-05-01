from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import GraphEdgeOut, GraphSnapshotOut
from app.api.utils import require
from app.db import models
from app.db.session import get_db

router = APIRouter(tags=["graphs"])


@router.get("/big-bangs/{big_bang_id}/graphs", response_model=list[GraphSnapshotOut])
def big_bang_graphs(big_bang_id: UUID, db: Session = Depends(get_db)):
    require(db, models.BigBang, big_bang_id)
    return db.scalars(select(models.GraphSnapshot).where(models.GraphSnapshot.big_bang_id == big_bang_id)).all()


@router.get("/multiverses/{multiverse_id}/graphs", response_model=list[GraphSnapshotOut])
def multiverse_graphs(multiverse_id: UUID, db: Session = Depends(get_db)):
    require(db, models.Multiverse, multiverse_id)
    return db.scalars(select(models.GraphSnapshot).where(models.GraphSnapshot.multiverse_id == multiverse_id)).all()


@router.get("/multiverses/{multiverse_id}/graphs/{graph_layer}", response_model=list[GraphSnapshotOut])
def graph_layer(multiverse_id: UUID, graph_layer: str, db: Session = Depends(get_db)):
    require(db, models.Multiverse, multiverse_id)
    return db.scalars(select(models.GraphSnapshot).where(models.GraphSnapshot.multiverse_id == multiverse_id, models.GraphSnapshot.layer == graph_layer)).all()


@router.get("/graph-edges/{edge_id}", response_model=GraphEdgeOut)
def edge(edge_id: UUID, db: Session = Depends(get_db)):
    return require(db, models.GraphEdge, edge_id)
