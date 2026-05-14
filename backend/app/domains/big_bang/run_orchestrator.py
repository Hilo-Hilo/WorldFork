from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import models
from app.domains.multiverse.statuses import TERMINAL_MULTIVERSE_STATUSES
from app.domains.report.engine import generate_final_big_bang_report, generate_multiverse_reports_parallel
from app.domains.tick.tick_runner import UNFINISHED_TICK_STATUSES, run_next_tick


def simulate_ticks(
    db: Session,
    *,
    multiverse: models.Multiverse,
    count: int,
    raise_on_domain_error: bool = False,
    queue_job: models.Job | None = None,
) -> list[models.TickSnapshot]:
    ticks = []
    seen_tick_ids = {
        tick_id
        for tick_id, in db.execute(
            select(models.TickSnapshot.id).where(models.TickSnapshot.multiverse_id == multiverse.id)
        )
    }
    for _ in range(max(0, count)):
        if multiverse.status not in {"active", "candidate"}:
            break
        if _job_interrupt_requested(db, queue_job):
            break
        try:
            tick = run_next_tick(db, multiverse=multiverse, queue_job=queue_job)
        except ValueError:
            if raise_on_domain_error:
                raise
            break
        if tick.status in UNFINISHED_TICK_STATUSES:
            break
        if tick.id in seen_tick_ids:
            break
        ticks.append(tick)
        seen_tick_ids.add(tick.id)
    return ticks


def _job_interrupt_requested(db: Session, queue_job: models.Job | None) -> bool:
    if queue_job is None:
        return False
    db.flush()
    status = db.scalar(select(models.Job.status).where(models.Job.id == queue_job.id))
    if status == "interrupt_requested":
        return True
    return getattr(queue_job, "status", None) == "interrupt_requested"


def run_big_bang_until_complete(db: Session, *, big_bang: models.BigBang, max_total_ticks: int = 24) -> dict:
    if big_bang.status == "archived":
        raise ValueError("big bang is archived")
    if big_bang.status == "paused":
        raise ValueError("big bang is paused")

    ticks_run: list[models.TickSnapshot] = []
    for _ in range(max_total_ticks):
        active = db.scalars(
            select(models.Multiverse)
            .where(models.Multiverse.big_bang_id == big_bang.id, models.Multiverse.status == "active")
            .order_by(models.Multiverse.ui_label)
        ).all()
        if not active:
            break
        made_progress = False
        for multiverse in active:
            try:
                tick = run_next_tick(db, multiverse=multiverse)
            except ValueError:
                continue
            if tick.status in UNFINISHED_TICK_STATUSES:
                continue
            if any(existing.id == tick.id for existing in ticks_run):
                continue
            ticks_run.append(tick)
            made_progress = True
        if not made_progress:
            break
    multiverses = db.scalars(
        select(models.Multiverse)
        .where(models.Multiverse.big_bang_id == big_bang.id)
        .order_by(models.Multiverse.ui_label)
    ).all()
    unfinished_ticks = db.scalars(
        select(models.TickSnapshot).where(
            models.TickSnapshot.big_bang_id == big_bang.id,
            models.TickSnapshot.status.in_(UNFINISHED_TICK_STATUSES),
        )
    ).all()
    non_terminal = [item for item in multiverses if item.status not in TERMINAL_MULTIVERSE_STATUSES]
    if unfinished_ticks or non_terminal:
        raise ValueError("big bang has active or unfinished timelines")

    report_versions = generate_multiverse_reports_parallel(db, multiverses=list(multiverses))
    big_bang.status = "completed"
    final_report = generate_final_big_bang_report(db, big_bang=big_bang)
    return {
        "ticks_run": len(ticks_run),
        "multiverse_count": len(multiverses),
        "report_versions": [str(item.id) for item in report_versions],
        "final_report_version_id": str(final_report.id),
    }
