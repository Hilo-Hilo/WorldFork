from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import models
from app.domains.jobs.executor import claim_job_for_execution, pause_job, queue_health_snapshot, requeue_job, resume_job


def test_multiverse_queue_lifecycle_round_trip():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    db = Session(engine)
    models.Base.metadata.create_all(engine)
    try:
        job = models.Job(
            job_type="run_multiverse_tick",
            status="queued",
            payload={"multiverse_id": str(uuid4())},
            idempotency_key="queue-e2e",
            queue_name="multiverse_ticks",
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        pause_job(db, job)
        assert job.status == "paused"

        resume_job(db, job)
        assert job.status == "queued"

        assert claim_job_for_execution(db, job, lease_owner="test-worker") is True
        assert job.status == "running"

        pause_job(db, job)
        assert job.status == "interrupt_requested"
        assert job.lease_owner == "test-worker"

        job.status = "interrupted"
        job.interrupted_at = datetime.now(timezone.utc)
        db.add(job)
        db.commit()
        db.refresh(job)

        requeue_job(db, job)
        assert job.status == "queued"
        assert job.attempt_number == 1

        snapshot = queue_health_snapshot(db)
        assert snapshot["counts_by_status"]["queued"] >= 1
    finally:
        db.close()
        engine.dispose()
