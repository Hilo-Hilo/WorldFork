from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models

TERMINAL_MULTIVERSE_STATUSES = {"completed", "terminated"}
ACTIVE_JOB_STATUSES = {"queued", "running", "interrupt_requested", "paused"}


def build_report_status(db: Session, *, big_bang: models.BigBang) -> dict[str, Any]:
    multiverses = list(
        db.scalars(
            select(models.Multiverse)
            .where(models.Multiverse.big_bang_id == big_bang.id)
            .order_by(models.Multiverse.ui_label)
        ).all()
    )
    reports = list(
        db.scalars(
            select(models.Report)
            .where(models.Report.big_bang_id == big_bang.id)
            .order_by(models.Report.created_at)
        ).all()
    )
    latest_versions = _latest_report_versions(db, reports)
    jobs = list(
        db.scalars(
            select(models.Job)
            .where(models.Job.big_bang_id == big_bang.id)
            .where(
                models.Job.job_type.in_(
                    [
                        "run_big_bang_until_complete",
                        "generate_multiverse_report",
                        "generate_final_big_bang_report",
                    ]
                )
            )
            .order_by(models.Job.created_at.desc())
            .limit(12)
        ).all()
    )
    latest_llm = _latest_llm_call(db, big_bang=big_bang, statuses=None)
    active_llm = _latest_llm_call(db, big_bang=big_bang, statuses={"created", "running"})
    failed_llm = _latest_llm_call(db, big_bang=big_bang, statuses={"failed"})
    latest_failed_job = next((job for job in jobs if job.status == "failed"), None)

    report_by_multiverse_id = {
        str(report.multiverse_id): report
        for report in reports
        if report.report_type == "multiverse" and report.multiverse_id is not None
    }
    multiverse_items = [
        _multiverse_report_status_item(multiverse, report_by_multiverse_id.get(str(multiverse.id)), latest_versions)
        for multiverse in multiverses
    ]
    final_report = next(
        (report for report in reports if report.report_type == "final_big_bang" and report.multiverse_id is None),
        None,
    )
    final_item = _final_report_status_item(final_report, latest_versions)
    stage = _derive_stage(
        big_bang=big_bang,
        multiverses=multiverses,
        multiverse_items=multiverse_items,
        final_item=final_item,
        active_llm=active_llm,
        failed_job=latest_failed_job,
        failed_llm=failed_llm,
    )
    return {
        "big_bang": {
            "id": str(big_bang.id),
            "name": big_bang.name,
            "status": big_bang.status,
            "updated_at": _iso(big_bang.updated_at),
        },
        "stage": stage,
        "message": _stage_message(stage, multiverse_items=multiverse_items, final_item=final_item),
        "multiverse_reports": {
            "total": len(multiverse_items),
            "completed": sum(1 for item in multiverse_items if item["report"]["has_version"]),
            "items": multiverse_items,
        },
        "final_report": final_item,
        "active_job": _job_payload(next((job for job in jobs if job.status in ACTIVE_JOB_STATUSES), None)),
        "latest_failed_job": _job_payload(latest_failed_job),
        "latest_llm_call": _llm_payload(latest_llm),
        "active_llm_call": _llm_payload(active_llm),
        "latest_failed_llm_call": _llm_payload(failed_llm),
        "jobs": [_job_payload(job) for job in jobs],
    }


def _latest_report_versions(
    db: Session, reports: list[models.Report]
) -> dict[str, models.ReportVersion]:
    if not reports:
        return {}
    report_ids = [report.id for report in reports]
    versions = list(
        db.scalars(
            select(models.ReportVersion)
            .where(models.ReportVersion.report_id.in_(report_ids))
            .order_by(models.ReportVersion.report_id, models.ReportVersion.version.desc())
        ).all()
    )
    latest: dict[str, models.ReportVersion] = {}
    for version in versions:
        latest.setdefault(str(version.report_id), version)
    return latest


def _multiverse_report_status_item(
    multiverse: models.Multiverse,
    report: models.Report | None,
    latest_versions: dict[str, models.ReportVersion],
) -> dict[str, Any]:
    version = latest_versions.get(str(report.id)) if report is not None else None
    return {
        "multiverse": {
            "id": str(multiverse.id),
            "ui_label": multiverse.ui_label,
            "status": multiverse.status,
            "report_status": multiverse.report_status,
            "path_probability": _float_or_none(multiverse.path_probability),
            "branch_probability": _float_or_none(multiverse.branch_probability),
            "branch_reason": multiverse.branch_reason,
            "updated_at": _iso(multiverse.updated_at),
        },
        "report": _report_payload(report, version),
    }


def _final_report_status_item(
    report: models.Report | None,
    latest_versions: dict[str, models.ReportVersion],
) -> dict[str, Any]:
    version = latest_versions.get(str(report.id)) if report is not None else None
    return _report_payload(report, version)


def _report_payload(report: models.Report | None, version: models.ReportVersion | None) -> dict[str, Any]:
    if report is None:
        return {
            "id": None,
            "status": "missing",
            "current_version": 0,
            "has_version": False,
            "version_id": None,
            "title": None,
            "updated_at": None,
        }
    return {
        "id": str(report.id),
        "status": report.status,
        "current_version": report.current_version,
        "has_version": bool(version),
        "version_id": str(version.id) if version else None,
        "title": version.title if version else None,
        "updated_at": _iso(report.updated_at),
    }


def _derive_stage(
    *,
    big_bang: models.BigBang,
    multiverses: list[models.Multiverse],
    multiverse_items: list[dict[str, Any]],
    final_item: dict[str, Any],
    active_llm: models.LLMCall | None,
    failed_job: models.Job | None,
    failed_llm: models.LLMCall | None,
) -> str:
    if final_item["has_version"]:
        return "ready"
    if active_llm is not None:
        purpose = active_llm.purpose or ""
        if purpose.startswith("report_agent_final_big_bang"):
            return "final_report"
        if purpose.startswith("predicate_"):
            return "predicate_resolution"
        if purpose.startswith("report_agent_multiverse"):
            return "single_reports"
    if failed_job is not None or failed_llm is not None:
        return "failed"
    if any(multiverse.status not in TERMINAL_MULTIVERSE_STATUSES for multiverse in multiverses):
        return "simulation"
    if multiverses and any(not item["report"]["has_version"] for item in multiverse_items):
        return "single_reports"
    if final_item["status"] in {"draft", "running"}:
        return "final_report"
    if big_bang.status == "completed":
        return "ready" if final_item["has_version"] else "final_report"
    return "waiting"


def _stage_message(
    stage: str, *, multiverse_items: list[dict[str, Any]], final_item: dict[str, Any]
) -> str:
    completed = sum(1 for item in multiverse_items if item["report"]["has_version"])
    total = len(multiverse_items)
    if stage == "simulation":
        return "Simulation is still producing timelines; reports begin after terminal timelines are available."
    if stage == "single_reports":
        return f"Generating single-universe reports ({completed}/{total} ready)."
    if stage == "predicate_resolution":
        return "Resolving the primary question across timelines before the final multiverse report."
    if stage == "final_report":
        if final_item["status"] == "missing":
            return "Preparing the final multiverse report after branch reports finish."
        return "Generating the final multiverse report from retained timeline path mass."
    if stage == "ready":
        return "Final multiverse report is ready."
    if stage == "failed":
        return "Report generation hit an error. Inspect the failed job or LLM call, then retry."
    return "Waiting for report generation to start."


def _latest_llm_call(
    db: Session, *, big_bang: models.BigBang, statuses: set[str] | None
) -> models.LLMCall | None:
    stmt = (
        select(models.LLMCall)
        .where(models.LLMCall.big_bang_id == big_bang.id)
        .where(
            models.LLMCall.purpose.like("report_agent_%")
            | models.LLMCall.purpose.like("predicate_%")
        )
    )
    if statuses:
        stmt = stmt.where(models.LLMCall.status.in_(statuses))
    return db.scalar(stmt.order_by(models.LLMCall.created_at.desc()).limit(1))


def _job_payload(job: models.Job | None) -> dict[str, Any] | None:
    if job is None:
        return None
    return {
        "id": str(job.id),
        "job_type": job.job_type,
        "status": job.status,
        "error": job.error,
        "result": job.result or {},
        "created_at": _iso(job.created_at),
        "updated_at": _iso(job.updated_at),
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
    }


def _llm_payload(call: models.LLMCall | None) -> dict[str, Any] | None:
    if call is None:
        return None
    return {
        "id": str(call.id),
        "purpose": call.purpose,
        "status": call.status,
        "provider": call.provider,
        "model": call.model,
        "created_at": _iso(call.created_at),
        "updated_at": _iso(call.updated_at),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
