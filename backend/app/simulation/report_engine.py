from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.storage.artifact_store import ArtifactStore, _cleanup_artifact_path
from app.storage.pdf_store import render_markdown_pdf


def _cleanup_report_artifacts(*artifacts: models.Artifact | None) -> None:
    for artifact in artifacts:
        if artifact is not None:
            _cleanup_artifact_path(Path(artifact.path))


def generate_multiverse_report(db: Session, *, multiverse: models.Multiverse, title: str | None = None, summary: str | None = None) -> models.ReportVersion:
    db.execute(select(models.Multiverse).where(models.Multiverse.id == multiverse.id).with_for_update()).scalar_one()
    report = db.scalar(
        select(models.Report)
        .where(models.Report.multiverse_id == multiverse.id, models.Report.report_type == "multiverse")
        .with_for_update()
    )
    if not report:
        report = models.Report(big_bang_id=multiverse.big_bang_id, multiverse_id=multiverse.id, report_type="multiverse", status="draft", current_version=0)
        db.add(report)
        db.flush()
    version = report.current_version + 1
    ticks = db.scalars(
        select(models.TickSnapshot)
        .where(models.TickSnapshot.multiverse_id == multiverse.id)
        .order_by(models.TickSnapshot.tick_index)
    ).all()
    god_reviews = db.scalars(
        select(models.GodAgentReview)
        .where(models.GodAgentReview.multiverse_id == multiverse.id)
        .order_by(models.GodAgentReview.created_at)
    ).all()
    title_text = title or f"Multiverse {multiverse.ui_label} Report"
    body = "\n".join(
        [
            f"# {title_text}",
            "",
            summary or f"Report for {multiverse.ui_label} with {len(ticks)} tick snapshots.",
            "",
            "## Timeline",
            *[f"- {tick.ui_label}: {tick.summary or tick.status}" for tick in ticks],
            "",
            "## God Agent Decisions",
            *[f"- {review.decision}: {review.rationale}" for review in god_reviews],
            "",
            "## Evidence Appendix",
            f"- Multiverse ID: {multiverse.id}",
            f"- Status: {multiverse.status}",
        ]
    )
    report.current_version = version
    report.status = "completed"
    multiverse.report_status = "completed"
    report_version = models.ReportVersion(
        report_id=report.id,
        version=version,
        title=title_text,
        summary=summary,
    )
    db.add(report_version)
    db.flush()
    artifact: models.Artifact | None = None
    pdf_artifact: models.Artifact | None = None
    try:
        artifact = ArtifactStore().write_text(
            db,
            big_bang_id=multiverse.big_bang_id,
            relative_path=f"big_bang_{multiverse.big_bang_id}/multiverses/{multiverse.ui_label}/reports/{report_version.id}/report_v{version}.md",
            body=body,
            kind="report_markdown",
            content_type="text/markdown",
        )
        pdf_artifact = render_markdown_pdf(
            db,
            big_bang_id=multiverse.big_bang_id,
            relative_path=f"big_bang_{multiverse.big_bang_id}/multiverses/{multiverse.ui_label}/reports/{report_version.id}/report_v{version}.pdf",
            title=title_text,
            markdown=body,
        )
        report_version.markdown_artifact_id = artifact.id
        report_version.pdf_artifact_id = pdf_artifact.id
        db.flush()
    except Exception:
        _cleanup_report_artifacts(artifact, pdf_artifact)
        raise
    return report_version


def generate_final_big_bang_report(db: Session, *, big_bang: models.BigBang, title: str | None = None, summary: str | None = None) -> models.ReportVersion:
    db.execute(select(models.BigBang).where(models.BigBang.id == big_bang.id).with_for_update()).scalar_one()
    report = db.scalar(
        select(models.Report)
        .where(
            models.Report.big_bang_id == big_bang.id,
            models.Report.report_type == "final_big_bang",
            models.Report.multiverse_id.is_(None),
        )
        .with_for_update()
    )
    if not report:
        report = models.Report(big_bang_id=big_bang.id, report_type="final_big_bang", status="draft", current_version=0)
        db.add(report)
        db.flush()
    version = report.current_version + 1
    multiverses = db.scalars(
        select(models.Multiverse)
        .where(models.Multiverse.big_bang_id == big_bang.id)
        .order_by(models.Multiverse.ui_label)
    ).all()
    report.current_version = version
    report.status = "completed"
    reports = db.scalars(select(models.Report).where(models.Report.big_bang_id == big_bang.id)).all()
    title_text = title or f"{big_bang.name} Final Big Bang Report"
    body = "\n".join(
        [
            f"# {title_text}",
            "",
            summary or f"Final condensation across {len(multiverses)} multiverse timelines.",
            "",
            "## Multiverse Comparison",
            *[f"- {item.ui_label}: {item.status}, report={item.report_status}" for item in multiverses],
            "",
            "## Report Inventory",
            *[f"- {item.report_type}: {item.status} v{item.current_version}" for item in reports],
            "",
            "## Evidence Appendix",
            f"- Big Bang ID: {big_bang.id}",
        ]
    )
    report_version = models.ReportVersion(
        report_id=report.id,
        version=version,
        title=title_text,
        summary=summary,
    )
    db.add(report_version)
    db.flush()
    artifact: models.Artifact | None = None
    pdf_artifact: models.Artifact | None = None
    try:
        artifact = ArtifactStore().write_text(
            db,
            big_bang_id=big_bang.id,
            relative_path=f"big_bang_{big_bang.id}/reports/{report_version.id}/final_big_bang_report_v{version}.md",
            body=body,
            kind="report_markdown",
            content_type="text/markdown",
        )
        pdf_artifact = render_markdown_pdf(
            db,
            big_bang_id=big_bang.id,
            relative_path=f"big_bang_{big_bang.id}/reports/{report_version.id}/final_big_bang_report_v{version}.pdf",
            title=title_text,
            markdown=body,
        )
        report_version.markdown_artifact_id = artifact.id
        report_version.pdf_artifact_id = pdf_artifact.id
        db.flush()
    except Exception:
        _cleanup_report_artifacts(artifact, pdf_artifact)
        raise
    return report_version
