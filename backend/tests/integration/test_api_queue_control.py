from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import models
from app.db.session import get_db
from app.main import app


client = TestClient(app)


@contextmanager
def override_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    db = Session(engine)
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield db
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()
        engine.dispose()


def _job_payload() -> dict:
    return {"multiverse_id": str(uuid4())}


def test_queue_control_lifecycle(monkeypatch):
    enqueued: list[str] = []
    monkeypatch.setattr("app.api.jobs.enqueue_job", lambda job_id: enqueued.append(str(job_id)))

    with override_db() as db:
        create_response = client.post(
            "/api/jobs",
            json={"job_type": "run_multiverse_tick", "payload": _job_payload()},
        )
        assert create_response.status_code == 200
        created = create_response.json()
        job_id = created["id"]
        assert created["status"] == "queued"

        pause_response = client.post(f"/api/jobs/{job_id}/pause")
        assert pause_response.status_code == 200
        paused = pause_response.json()
        assert paused["status"] == "paused"
        assert paused["paused_at"] is not None

        resume_response = client.post(f"/api/jobs/{job_id}/resume")
        assert resume_response.status_code == 200
        resumed = resume_response.json()
        assert resumed["status"] == "queued"
        assert resumed["paused_at"] is None

        claim_response = client.post(f"/api/jobs/{job_id}/claim")
        assert claim_response.status_code == 200
        claimed = claim_response.json()
        assert claimed["status"] == "running"
        assert claimed["lease_expires_at"] is not None
        assert claimed["started_at"] is not None

        interrupt_response = client.post(f"/api/jobs/{job_id}/interrupt")
        assert interrupt_response.status_code == 200
        interrupted = interrupt_response.json()
        assert interrupted["status"] == "interrupt_requested"
        assert interrupted["interrupt_requested_at"] is not None

        queued_job = db.get(models.Job, job_id)
        queued_job.status = "interrupted"
        queued_job.interrupted_at = datetime.now(timezone.utc)
        queued_job.interrupt_requested_at = datetime.now(timezone.utc)
        db.add(queued_job)
        db.commit()

        requeue_response = client.post(f"/api/jobs/{job_id}/requeue")
        assert requeue_response.status_code == 200
        requeued = requeue_response.json()
        assert requeued["status"] == "queued"
        assert requeued["attempt_number"] == 1

        health_response = client.get("/api/jobs/queue-health")
        assert health_response.status_code == 200
        health = health_response.json()
        assert health["counts_by_status"]["queued"] >= 1
        assert health["capacity"]["max_concurrent_jobs"] >= 1

        stored = db.get(models.Job, job_id)
        assert stored is not None
        assert stored.queue_name is not None

    assert enqueued


def test_pause_running_job_requests_interrupt_without_allowing_immediate_resume(monkeypatch):
    monkeypatch.setattr("app.api.jobs.enqueue_job", lambda job_id: None)

    with override_db():
        create_response = client.post(
            "/api/jobs",
            json={"job_type": "run_multiverse_tick", "payload": _job_payload()},
        )
        job_id = create_response.json()["id"]
        claim_response = client.post(f"/api/jobs/{job_id}/claim")
        assert claim_response.status_code == 200

        pause_response = client.post(f"/api/jobs/{job_id}/pause")
        assert pause_response.status_code == 200
        paused = pause_response.json()
        assert paused["status"] == "interrupt_requested"
        assert paused["interrupt_requested_at"] is not None
        assert paused["lease_owner"] == "local-worker"

        resume_response = client.post(f"/api/jobs/{job_id}/resume")
        assert resume_response.status_code == 409


def test_resume_enqueue_failure_keeps_job_queued_without_local_claim(monkeypatch):
    def fail_enqueue(job_id):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr("app.api.jobs.enqueue_job", fail_enqueue)

    with override_db() as db:
        job = models.Job(
            job_type="run_multiverse_tick",
            queue_name="multiverse_ticks",
            status="paused",
            payload=_job_payload(),
            idempotency_key="resume-enqueue-failure",
            paused_at=datetime.now(timezone.utc),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        response = client.post(f"/api/jobs/{job.id}/resume")
        assert response.status_code == 200
        resumed = response.json()
        assert resumed["status"] == "queued"
        assert resumed["error"] == "enqueue failed; job remains queued; fix the queue or run it explicitly"
        assert resumed["started_at"] is None
        assert resumed["lease_owner"] is None

        stored = db.get(models.Job, job.id)
        assert stored.status == "queued"
        assert stored.started_at is None
        assert stored.lease_owner is None


def test_requeue_enforces_max_attempts_and_clears_finished_at(monkeypatch):
    monkeypatch.setattr("app.api.jobs.enqueue_job", lambda job_id: None)

    with override_db() as db:
        job = models.Job(
            job_type="run_multiverse_tick",
            queue_name="multiverse_ticks",
            status="failed",
            payload=_job_payload(),
            idempotency_key="requeue-limit",
            attempt_number=2,
            max_attempts=3,
            finished_at=datetime.now(timezone.utc),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        response = client.post(f"/api/jobs/{job.id}/requeue")
        assert response.status_code == 200
        requeued = response.json()
        assert requeued["status"] == "queued"
        assert requeued["attempt_number"] == 3
        assert requeued["finished_at"] is None

        job.status = "failed"
        job.finished_at = datetime.now(timezone.utc)
        db.add(job)
        db.commit()
        db.refresh(job)

        second_response = client.post(f"/api/jobs/{job.id}/requeue")
        assert second_response.status_code == 409


def test_requeue_enqueue_failure_keeps_job_queued_without_local_claim(monkeypatch):
    def fail_enqueue(job_id):
        raise RuntimeError("broker unavailable")

    monkeypatch.setattr("app.api.jobs.enqueue_job", fail_enqueue)

    with override_db() as db:
        job = models.Job(
            job_type="run_multiverse_tick",
            queue_name="multiverse_ticks",
            status="failed",
            payload=_job_payload(),
            idempotency_key="requeue-enqueue-failure",
            attempt_number=0,
            max_attempts=2,
            finished_at=datetime.now(timezone.utc),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        response = client.post(f"/api/jobs/{job.id}/requeue")
        assert response.status_code == 200
        requeued = response.json()
        assert requeued["status"] == "queued"
        assert requeued["error"] == "enqueue failed; job remains queued; fix the queue or run it explicitly"
        assert requeued["started_at"] is None
        assert requeued["lease_owner"] is None

        stored = db.get(models.Job, job.id)
        assert stored.status == "queued"
        assert stored.started_at is None
        assert stored.lease_owner is None
