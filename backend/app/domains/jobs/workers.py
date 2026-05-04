from __future__ import annotations

from app.db import models
from app.db.session import SessionLocal
from app.domains.jobs.executor import JobNotRunnableError, execute_job
from backend.app.workers.celery_app import celery_app


@celery_app.task(name="worldfork.execute_job", bind=True, acks_late=True, max_retries=0)
def run_job(self, job_id: str) -> None:  # type: ignore[no-untyped-def]
    db = SessionLocal()
    try:
        job = db.get(models.Job, job_id)
        if job:
            try:
                execute_job(db, job, commit_running=True)
            except JobNotRunnableError:
                db.rollback()
                return
            db.commit()
    finally:
        db.close()
