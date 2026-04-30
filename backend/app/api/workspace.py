from __future__ import annotations

import json
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import WorkspaceState
from app.api.utils import require
from app.db import models
from app.db.session import get_db
from app.simulation.tick_bundles import hydrate_tick_snapshots_for_read

router = APIRouter(prefix="/workspace", tags=["workspace"])


@router.get("/{big_bang_id}/state", response_model=WorkspaceState)
def state(big_bang_id: UUID, db: Session = Depends(get_db)):
    big_bang = require(db, models.BigBang, big_bang_id)
    multiverses = db.scalars(select(models.Multiverse).where(models.Multiverse.big_bang_id == big_bang_id).order_by(models.Multiverse.ui_label)).all()
    latest_ticks = db.scalars(select(models.TickSnapshot).where(models.TickSnapshot.big_bang_id == big_bang_id).order_by(models.TickSnapshot.created_at.desc()).limit(25)).all()
    activity = [{"kind": "tick", "label": tick.ui_label, "status": tick.status} for tick in latest_ticks[:10]]
    return WorkspaceState(
        big_bang=big_bang,
        multiverses=multiverses,
        latest_ticks=hydrate_tick_snapshots_for_read(db, latest_ticks),
        activity=activity,
    )


@router.get("/{big_bang_id}/activity")
def activity(big_bang_id: UUID, db: Session = Depends(get_db)):
    require(db, models.BigBang, big_bang_id)
    ticks = db.scalars(select(models.TickSnapshot).where(models.TickSnapshot.big_bang_id == big_bang_id).order_by(models.TickSnapshot.created_at.desc()).limit(50)).all()
    tools = db.scalars(select(models.ToolCall).where(models.ToolCall.big_bang_id == big_bang_id).order_by(models.ToolCall.created_at.desc()).limit(50)).all()
    return {"ticks": hydrate_tick_snapshots_for_read(db, ticks), "tool_calls": tools}


@router.get("/{big_bang_id}/stream")
def stream(big_bang_id: UUID, db: Session = Depends(get_db)):
    require(db, models.BigBang, big_bang_id)

    def events():
        yield "event: snapshot\n"
        yield f"data: {json.dumps({'big_bang_id': str(big_bang_id), 'status': 'connected'})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
