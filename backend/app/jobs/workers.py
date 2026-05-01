from __future__ import annotations

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.domains.jobs.executor import JobNotRunnableError, execute_job
from app.db import models


redis_broker = RedisBroker(url=getattr(get_settings(), "redis_url", "redis://localhost:6379/0"))
dramatiq.set_broker(redis_broker)


@dramatiq.actor
def run_job(job_id: str) -> None:
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
