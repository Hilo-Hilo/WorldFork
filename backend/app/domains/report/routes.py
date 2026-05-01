from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import ReportRenderRequest, ReportVersionOut, ReportVersionPatch
from app.api.schemas import (
    TimelineAdjudicationDetailOut,
    TimelineAdjudicationRequest,
    TimelineAdjudicationVersionOut,
)
from app.api.utils import commit_or_500, require
from app.db import models
from app.db.session import get_db
from app.domains.report.adjudication import (
    evaluate_timeline_adjudication,
    latest_timeline_adjudication,
    timeline_adjudication_detail,
)
from app.domains.report.evidence_pack import build_report_evidence_pack
from app.domains.report.engine import (
    render_report_version_ephemeral,
    render_report_version_to_markdown,
)

router = APIRouter(tags=["reports"])


@router.get("/big-bangs/{big_bang_id}/report-evidence-pack")
def report_evidence_pack(big_bang_id: UUID, mode: str = "standard", db: Session = Depends(get_db)):
    big_bang = require(db, models.BigBang, big_bang_id)
    return build_report_evidence_pack(db, big_bang=big_bang, mode=mode)


@router.get(
    "/big-bangs/{big_bang_id}/timeline-adjudications/latest",
    response_model=TimelineAdjudicationDetailOut,
)
def latest_adjudication(big_bang_id: UUID, db: Session = Depends(get_db)):
    require(db, models.BigBang, big_bang_id)
    adjudication = latest_timeline_adjudication(db, big_bang_id=big_bang_id)
    if adjudication is None:
        raise HTTPException(status_code=404, detail="timeline adjudication not found")
    return timeline_adjudication_detail(db, adjudication)


@router.post(
    "/big-bangs/{big_bang_id}/timeline-adjudications/evaluate",
    response_model=TimelineAdjudicationVersionOut,
)
def evaluate_adjudication(
    big_bang_id: UUID,
    payload: TimelineAdjudicationRequest | None = None,
    db: Session = Depends(get_db),
):
    request = payload or TimelineAdjudicationRequest()
    big_bang = require(db, models.BigBang, big_bang_id)
    adjudication = evaluate_timeline_adjudication(
        db,
        big_bang=big_bang,
        source_type=request.source_type,
        summary=request.summary,
        created_by="api",
    )
    commit_or_500(db)
    return adjudication


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


@router.post(
    "/report-versions/{report_version_id}/render",
    response_class=Response,
    responses={
        200: {
            "description": "Ephemeral report render streamed without backend artifact persistence.",
            "content": {
                "application/pdf": {"schema": {"type": "string", "format": "binary"}},
                "text/markdown": {"schema": {"type": "string"}},
            },
        }
    },
)
def render(
    report_version_id: UUID,
    payload: ReportRenderRequest | None = None,
    db: Session = Depends(get_db),
):
    request = payload or ReportRenderRequest()
    report_version = require(db, models.ReportVersion, report_version_id)
    try:
        rendered = render_report_version_ephemeral(
            db,
            report_version=report_version,
            output_format=request.format,
            force=request.force,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return Response(
        content=rendered.body,
        media_type=rendered.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{rendered.filename}"',
            "X-WorldFork-Render-Mode": "ephemeral",
        },
    )
