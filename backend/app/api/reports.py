from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import ReportRenderOut, ReportRenderRequest, ReportVersionOut, ReportVersionPatch
from app.api.utils import commit_or_500, require
from app.db import models
from app.db.session import get_db
from app.simulation.report_engine import (
    render_report_version_artifact,
    render_report_version_to_markdown,
)

router = APIRouter(tags=["reports"])


@router.get("/reports/{report_id}/versions", response_model=list[ReportVersionOut])
def list_versions(report_id: UUID, db: Session = Depends(get_db)):
    require(db, models.Report, report_id)
    return db.scalars(
        select(models.ReportVersion)
        .where(models.ReportVersion.report_id == report_id)
        .order_by(models.ReportVersion.version.desc())
    ).all()


@router.get("/report-versions/{report_version_id}", response_model=ReportVersionOut)
def get_version(report_version_id: UUID, db: Session = Depends(get_db)):
    return require(db, models.ReportVersion, report_version_id)


@router.patch("/report-versions/{report_version_id}", response_model=ReportVersionOut)
def patch_version(
    report_version_id: UUID,
    payload: ReportVersionPatch,
    db: Session = Depends(get_db),
):
    report_version = require(db, models.ReportVersion, report_version_id)
    changes = payload.model_dump(exclude_unset=True)
    render_affecting = False
    if "title" in changes:
        report_version.title = changes["title"]
        content = dict(report_version.content or {})
        content["title"] = changes["title"]
        report_version.content = content
        render_affecting = True
    if "summary" in changes:
        report_version.summary = changes["summary"]
        content = dict(report_version.content or {})
        content["summary"] = changes["summary"]
        report_version.content = content
        render_affecting = True
    if "content" in changes:
        content = dict(changes["content"] or {})
        content.setdefault("schema_version", (report_version.content or {}).get("schema_version"))
        content.setdefault("title", report_version.title)
        content.setdefault("summary", report_version.summary)
        report_version.content = content
        render_affecting = True
    if "generation_metadata" in changes:
        report_version.generation_metadata = changes["generation_metadata"] or {}

    metadata = dict(report_version.generation_metadata or {})
    metadata["last_mutated_at"] = datetime.now(timezone.utc).isoformat()
    metadata["mutable_storage"] = "report_versions.content"
    if render_affecting:
        metadata["render_cache_invalidated_at"] = metadata["last_mutated_at"]
        report_version.markdown_artifact_id = None
        report_version.pdf_artifact_id = None
    report_version.generation_metadata = metadata
    commit_or_500(db)
    return report_version


@router.get("/report-versions/{report_version_id}/markdown", response_class=PlainTextResponse)
def markdown(report_version_id: UUID, db: Session = Depends(get_db)):
    report_version = require(db, models.ReportVersion, report_version_id)
    return PlainTextResponse(render_report_version_to_markdown(report_version), media_type="text/markdown")


@router.post("/report-versions/{report_version_id}/render", response_model=ReportRenderOut)
def render(
    report_version_id: UUID,
    payload: ReportRenderRequest | None = None,
    db: Session = Depends(get_db),
):
    request = payload or ReportRenderRequest()
    report_version = require(db, models.ReportVersion, report_version_id)
    try:
        artifact = render_report_version_artifact(
            db,
            report_version=report_version,
            output_format=request.format,
            force=request.force,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    commit_or_500(db)
    return {
        "report_version_id": report_version.id,
        "format": request.format,
        "artifact_id": artifact.id,
        "content_type": artifact.content_type,
        "path": artifact.path,
    }
