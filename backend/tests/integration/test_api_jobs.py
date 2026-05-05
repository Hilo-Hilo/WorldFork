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


def _seed_big_bang_with_multiverse(db: Session) -> tuple[models.BigBang, models.Multiverse]:
    big_bang = models.BigBang(name="Job route run", scenario_input={}, status="running", current_config_version=1)
    db.add(big_bang)
    db.flush()
    multiverse = models.Multiverse(
        big_bang_id=big_bang.id,
        parent_multiverse_id=None,
        fork_tick_index=None,
        ui_label="M1",
        depth=0,
        status="active",
        branch_reason="Root",
        state={},
        report_status="not_ready",
    )
    db.add(multiverse)
    db.commit()
    db.refresh(big_bang)
    db.refresh(multiverse)
    return big_bang, multiverse


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
    monkeypatch.setattr("app.domains.jobs.routes.enqueue_job", lambda job_id: enqueued.append(str(job_id)))

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


def test_long_running_multiverse_routes_create_jobs(monkeypatch):
    enqueued: list[str] = []
    monkeypatch.setattr("app.domains.jobs.routes.enqueue_job", lambda job_id: enqueued.append(str(job_id)))
    with override_db() as db:
        big_bang, multiverse = _seed_big_bang_with_multiverse(db)
        big_bang_id = str(big_bang.id)
        multiverse_id = str(multiverse.id)
        tick_response = client.post(
            f"/api/multiverses/{multiverse_id}/simulate-next-tick/jobs",
            json={"idempotency_key": "tick-key", "force": True},
        )
        many_response = client.post(f"/api/multiverses/{multiverse_id}/simulate-ticks/jobs", json={"count": 3})
        report_response = client.post(
            f"/api/multiverses/{multiverse_id}/report/jobs",
            json={"title": "Branch report", "summary": "Queued"},
        )

    assert tick_response.status_code == 200, tick_response.text
    assert many_response.status_code == 200, many_response.text
    assert report_response.status_code == 200, report_response.text
    tick_job = tick_response.json()
    assert tick_job["job_type"] == "run_multiverse_tick"
    assert tick_job["big_bang_id"] == big_bang_id
    assert tick_job["payload"] == {
        "multiverse_id": multiverse_id,
        "idempotency_key": "tick-key",
        "force": True,
    }
    assert many_response.json()["job_type"] == "simulate_multiverse_ticks"
    assert many_response.json()["payload"] == {"multiverse_id": multiverse_id, "count": 3}
    assert report_response.json()["job_type"] == "generate_multiverse_report"
    assert len(enqueued) == 3


def test_long_running_big_bang_routes_create_jobs(monkeypatch):
    enqueued: list[str] = []
    monkeypatch.setattr("app.domains.jobs.routes.enqueue_job", lambda job_id: enqueued.append(str(job_id)))
    with override_db() as db:
        big_bang, _multiverse = _seed_big_bang_with_multiverse(db)
        big_bang_id = str(big_bang.id)
        final_response = client.post(
            f"/api/big-bangs/{big_bang_id}/reports/final/jobs",
            json={"title": "Final report", "summary": "Queued"},
        )
        run_response = client.post(
            f"/api/big-bangs/{big_bang_id}/run-until-complete/jobs",
            json={"max_total_ticks": 30, "stop_when_endpoint_ledger_resolved": True},
        )

    assert final_response.status_code == 200, final_response.text
    assert run_response.status_code == 200, run_response.text
    final_job = final_response.json()
    assert final_job["job_type"] == "generate_final_big_bang_report"
    assert final_job["big_bang_id"] == big_bang_id
    assert final_job["payload"] == {"title": "Final report", "summary": "Queued"}
    run_job = run_response.json()
    assert run_job["job_type"] == "run_big_bang_until_complete"
    assert run_job["payload"] == {"max_total_ticks": 30, "stop_when_endpoint_ledger_resolved": True}
    assert len(enqueued) == 2


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

    with patch("app.domains.jobs.routes.get_redis_client", return_value=mock_redis):
        pause_response = client.post("/api/jobs/queues/multiverse_ticks/pause")
        resume_response = client.post("/api/jobs/queues/multiverse_ticks/resume")

    assert pause_response.status_code == 200
    assert pause_response.json() == {"queue": "multiverse_ticks", "paused": True}
    assert resume_response.status_code == 200
    assert resume_response.json() == {"queue": "multiverse_ticks", "paused": False}
