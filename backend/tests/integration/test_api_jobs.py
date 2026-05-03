"""Integration tests for canonical /api/jobs endpoints."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
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


def _seed_job(
    db: Session,
    *,
    status: str = "queued",
    idempotency_key: str | None = None,
    attempt_number: int = 0,
    max_attempts: int = 3,
) -> models.Job:
    job = models.Job(
        job_type="run_multiverse_tick",
        queue_name="multiverse_ticks",
        status=status,
        payload={"multiverse_id": str(uuid4())},
        result={},
        idempotency_key=idempotency_key or f"job-{uuid4()}",
        attempt_number=attempt_number,
        max_attempts=max_attempts,
        retryable=True,
        created_at=datetime.now(timezone.utc),
        queued_at=datetime.now(timezone.utc),
    )
    if status in {"failed", "interrupted", "completed", "cancelled"}:
        job.finished_at = datetime.now(timezone.utc)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def test_list_jobs_supports_status_filter_and_pagination():
    with override_db() as db:
        _seed_job(db, status="queued")
        failed = _seed_job(db, status="failed")

        response = client.get("/api/jobs", params={"status": "failed", "limit": 1})

    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == str(failed.id)
    assert data[0]["status"] == "failed"


def test_get_queues_degrades_when_broker_is_unreachable():
    mock_inspect = MagicMock()
    mock_inspect.active.side_effect = Exception("Connection refused")
    mock_celery = MagicMock()
    mock_celery.control.inspect.return_value = mock_inspect

    with patch("backend.app.workers.celery_app.celery_app", mock_celery):
        response = client.get("/api/jobs/queues")

    assert response.status_code == 200
    data = response.json()
    assert data["degraded"] is True
    assert data["queues"]


def test_get_workers_degrades_when_broker_is_unreachable():
    mock_inspect = MagicMock()
    mock_inspect.stats.side_effect = Exception("broker down")
    mock_celery = MagicMock()
    mock_celery.control.inspect.return_value = mock_inspect

    with patch("backend.app.workers.celery_app.celery_app", mock_celery):
        response = client.get("/api/jobs/workers")

    assert response.status_code == 200
    data = response.json()
    assert data["degraded"] is True
    assert data["workers"] == []


def test_retry_requeues_failed_current_job(monkeypatch):
    enqueued: list[str] = []
    monkeypatch.setattr("app.api.jobs.enqueue_job", lambda job_id: enqueued.append(str(job_id)))

    with override_db() as db:
        job = _seed_job(db, status="failed")
        response = client.post(f"/api/jobs/{job.id}/retry")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "queued"
    assert data["attempt_number"] == 1
    assert data["finished_at"] is None
    assert enqueued == [str(job.id)]


def test_cancel_marks_job_terminal():
    with override_db() as db:
        job = _seed_job(db, status="queued")
        response = client.post(f"/api/jobs/{job.id}/cancel")

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "cancelled"
    assert data["finished_at"] is not None


def test_cancel_refuses_to_rewrite_terminal_job():
    with override_db() as db:
        job = _seed_job(db, status="succeeded")
        response = client.post(f"/api/jobs/{job.id}/cancel")
        db.expire_all()
        persisted = db.get(models.Job, job.id)

    assert response.status_code == 409, response.text
    assert persisted.status == "succeeded"


def test_pause_resume_queue_sets_redis_control_key():
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock()
    mock_redis.delete = AsyncMock()

    with patch("app.api.jobs.get_redis_client", return_value=mock_redis):
        pause_response = client.post("/api/jobs/queues/multiverse_ticks/pause")
        resume_response = client.post("/api/jobs/queues/multiverse_ticks/resume")

    assert pause_response.status_code == 200
    assert pause_response.json() == {"queue": "multiverse_ticks", "paused": True}
    assert resume_response.status_code == 200
    assert resume_response.json() == {"queue": "multiverse_ticks", "paused": False}
