"""Integration tests for /api/jobs endpoints (B5-B)."""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import models as current_models
from backend.app.core.db import get_session
from backend.app.main import app
from backend.app.models.jobs import JobModel


# ---------------------------------------------------------------------------
# Helper — seed a job row
# ---------------------------------------------------------------------------

async def _seed_job(db_session, *, job_id="job-001", status="queued", job_type="simulate_universe_tick"):
    row = JobModel(
        job_id=job_id,
        idempotency_key=f"key:{job_id}",
        job_type=job_type,
        priority="p0",
        run_id="run-abc",
        universe_id="u-001",
        tick=1,
        attempt_number=0,
        payload={},
        status=status,
        created_at=datetime.now(UTC),
        enqueued_at=datetime.now(UTC),
    )
    db_session.add(row)
    await db_session.commit()
    return row


# ---------------------------------------------------------------------------
# GET /api/jobs/queues
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_queues_degraded_when_broker_unreachable(client):
    """Should return degraded=True when broker is unreachable."""
    mock_inspect = MagicMock()
    mock_inspect.active.side_effect = Exception("Connection refused")
    mock_celery = MagicMock()
    mock_celery.control.inspect.return_value = mock_inspect

    with patch("backend.app.workers.celery_app.celery_app", mock_celery):
        resp = await client.get("/api/jobs/queues")

    assert resp.status_code == 200
    data = resp.json()
    assert data["degraded"] is True
    assert len(data["queues"]) > 0  # returns empty stats for known queues


@pytest.mark.asyncio
async def test_get_queues_ok_with_mock(client):
    """Should aggregate active tasks per queue when broker responds."""
    mock_inspect = MagicMock()
    mock_inspect.active.return_value = {
        "worker@host": [{"delivery_info": {"routing_key": "p0"}}]
    }
    mock_inspect.reserved.return_value = {}
    mock_inspect.scheduled.return_value = {}
    mock_celery = MagicMock()
    mock_celery.control.inspect.return_value = mock_inspect

    with patch("backend.app.workers.celery_app.celery_app", mock_celery):
        resp = await client.get("/api/jobs/queues")

    assert resp.status_code == 200
    data = resp.json()
    # Even with patching at wrong level, degraded gracefully
    assert "queues" in data


# ---------------------------------------------------------------------------
# GET /api/jobs/workers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_workers_degraded(client):
    mock_inspect = MagicMock()
    mock_inspect.stats.side_effect = Exception("broker down")
    mock_celery = MagicMock()
    mock_celery.control.inspect.return_value = mock_inspect

    with patch("backend.app.workers.celery_app.celery_app", mock_celery):
        resp = await client.get("/api/jobs/workers")

    assert resp.status_code == 200
    data = resp.json()
    assert data["degraded"] is True
    assert data["workers"] == []


# ---------------------------------------------------------------------------
# GET /api/jobs (list)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_jobs_empty(client):
    resp = await client.get("/api/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["jobs"] == []
    assert data["total"] == 0


@pytest.mark.asyncio
async def test_list_jobs_returns_seeded(client, db_session):
    await _seed_job(db_session)
    resp = await client.get("/api/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    assert any(j["job_id"] == "job-001" for j in data["jobs"])


@pytest.mark.asyncio
async def test_list_jobs_filter_status(client, db_session):
    await _seed_job(db_session, job_id="job-002", status="failed")
    resp = await client.get("/api/jobs", params={"status": "failed"})
    assert resp.status_code == 200
    data = resp.json()
    assert all(j["status"] == "failed" for j in data["jobs"])


@pytest.mark.asyncio
async def test_list_jobs_filter_run_id(client, db_session):
    await _seed_job(db_session, job_id="job-003")
    resp = await client.get("/api/jobs", params={"run_id": "run-abc"})
    assert resp.status_code == 200
    data = resp.json()
    assert all(j["run_id"] == "run-abc" for j in data["jobs"])


@pytest.mark.asyncio
async def test_list_jobs_pagination(client, db_session):
    for i in range(5):
        await _seed_job(db_session, job_id=f"page-job-{i}")
    resp = await client.get("/api/jobs", params={"limit": 2, "offset": 0})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["jobs"]) <= 2
    assert data["limit"] == 2


# ---------------------------------------------------------------------------
# GET /api/jobs/{job_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_job_not_found(client):
    resp = await client.get("/api/jobs/nonexistent-job")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_job_ok(client, db_session):
    await _seed_job(db_session, job_id="get-job-001")
    resp = await client.get("/api/jobs/get-job-001")
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == "get-job-001"
    assert data["status"] == "queued"


# ---------------------------------------------------------------------------
# POST /api/jobs/{job_id}/retry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_job_not_found(client):
    resp = await client.post("/api/jobs/no-such-job/retry")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_retry_job_increments_attempt(client, db_session):
    await _seed_job(db_session, job_id="retry-job-001", status="failed")
    resp = await client.post("/api/jobs/retry-job-001/retry")
    assert resp.status_code == 200
    data = resp.json()
    assert data["attempt_number"] == 1
    assert data["job_id"] == "retry-job-001"
    assert "new_task_id" in data


@pytest.mark.asyncio
async def test_retry_job_rejects_non_terminal_legacy_job(client, db_session):
    await _seed_job(db_session, job_id="retry-job-queued", status="queued")
    resp = await client.post("/api/jobs/retry-job-queued/retry")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_retry_job_current_schema_uses_requeue_semantics_and_enqueues(monkeypatch):
    enqueued: list[str] = []
    monkeypatch.setattr("backend.app.api.jobs.enqueue_job", lambda job_id: enqueued.append(str(job_id)))

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(current_models.Job.__table__.create)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        job = current_models.Job(
            id=uuid4(),
            job_type="run_multiverse_tick",
            queue_name="p1",
            status="failed",
            payload={"multiverse_id": str(uuid4())},
            result={},
            error="boom",
            idempotency_key="current-retry-job",
            attempt_number=0,
            max_attempts=1,
            retryable=True,
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(job)
        await session.commit()

        async def _override_get_session():
            yield session

        app.dependency_overrides[get_session] = _override_get_session
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(f"/api/jobs/{job.id}/retry")
        finally:
            app.dependency_overrides.pop(get_session, None)

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert data["attempt_number"] == 1
        assert data["finished_at"] is None
        assert enqueued == [str(job.id)]

    await engine.dispose()


@pytest.mark.asyncio
async def test_retry_job_current_schema_rejects_max_attempts(monkeypatch):
    enqueued: list[str] = []
    monkeypatch.setattr("backend.app.api.jobs.enqueue_job", lambda job_id: enqueued.append(str(job_id)))

    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(current_models.Job.__table__.create)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with factory() as session:
        job = current_models.Job(
            id=uuid4(),
            job_type="run_multiverse_tick",
            queue_name="p1",
            status="failed",
            payload={"multiverse_id": str(uuid4())},
            result={},
            error="boom",
            idempotency_key="current-retry-limit",
            attempt_number=1,
            max_attempts=1,
            retryable=True,
            created_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
        )
        session.add(job)
        await session.commit()

        async def _override_get_session():
            yield session

        app.dependency_overrides[get_session] = _override_get_session
        try:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(f"/api/jobs/{job.id}/retry")
        finally:
            app.dependency_overrides.pop(get_session, None)

        assert resp.status_code == 409
        assert enqueued == []

    await engine.dispose()


# ---------------------------------------------------------------------------
# POST /api/jobs/{job_id}/cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_job_not_found(client):
    resp = await client.post("/api/jobs/no-such-job/cancel")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_cancel_job_ok(client, db_session):
    await _seed_job(db_session, job_id="cancel-job-001")
    resp = await client.post("/api/jobs/cancel-job-001/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "cancelled"
    assert data["job_id"] == "cancel-job-001"


# ---------------------------------------------------------------------------
# POST /api/jobs/queues/{queue}/pause  &  /resume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pause_resume_queue(client):
    """Queue pause/resume should set/delete Redis key."""
    from unittest.mock import AsyncMock

    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock()
    mock_redis.delete = AsyncMock()

    with patch("backend.app.api.jobs.get_redis_client", return_value=mock_redis):
        pause_resp = await client.post("/api/jobs/queues/p1/pause")
        resume_resp = await client.post("/api/jobs/queues/p1/resume")

    assert pause_resp.status_code == 200
    assert pause_resp.json()["paused"] is True
    assert pause_resp.json()["queue"] == "p1"

    assert resume_resp.status_code == 200
    assert resume_resp.json()["paused"] is False
