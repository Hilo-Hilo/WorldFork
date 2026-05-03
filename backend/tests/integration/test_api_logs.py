"""Integration tests for /api/logs endpoints (B5-B)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import models as current_models
from backend.app.domains.logs.routes import get_request_logs, get_trace
from backend.app.models.jobs import JobModel
from backend.app.models.llm_calls import LLMCallModel


# ---------------------------------------------------------------------------
# Helper — seed rows
# ---------------------------------------------------------------------------

async def _seed_llm_call(
    db_session,
    *,
    call_id="call-001",
    provider="openrouter",
    status="succeeded",
    error=None,
    run_id="run-123",
    created_at=None,
):
    row = LLMCallModel(
        call_id=call_id,
        provider=provider,
        model_used="openai/gpt-4o",
        job_type="god_agent_review",
        prompt_packet_path="/tmp/prompt.json",
        prompt_hash="deadbeef",
        response_path="/tmp/response.json",
        prompt_tokens=100,
        completion_tokens=200,
        total_tokens=300,
        cost_usd=0.001,
        latency_ms=250,
        repaired_once=False,
        status=status,
        error=error,
        created_at=created_at or datetime.now(UTC),
        run_id=run_id,
    )
    db_session.add(row)
    await db_session.commit()
    return row


async def _seed_failed_job(db_session, *, job_id="fail-job-001", run_id="run-123", created_at=None):
    timestamp = created_at or datetime.now(UTC)
    row = JobModel(
        job_id=job_id,
        idempotency_key=f"key:{job_id}",
        job_type="simulate_universe_tick",
        priority="p0",
        run_id=run_id,
        universe_id="u-001",
        tick=1,
        attempt_number=0,
        payload={},
        status="failed",
        error="Something went wrong",
        created_at=timestamp,
        enqueued_at=timestamp,
    )
    db_session.add(row)
    await db_session.commit()
    return row


@pytest_asyncio.fixture
async def current_schema_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(current_models.LLMCall.__table__.create)
        await conn.run_sync(current_models.Job.__table__.create)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


# ---------------------------------------------------------------------------
# GET /api/logs/requests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_request_logs_empty(client):
    resp = await client.get("/api/logs/requests")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_get_request_logs_returns_data(client, db_session):
    await _seed_llm_call(db_session)
    resp = await client.get("/api/logs/requests")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    first = data[0]
    assert first["call_id"] == "call-001"
    assert first["provider"] == "openrouter"
    assert first["status"] == "succeeded"


@pytest.mark.asyncio
async def test_get_request_logs_filter_provider(client, db_session):
    await _seed_llm_call(db_session, call_id="call-p1", provider="openai", run_id="run-456")
    resp = await client.get("/api/logs/requests", params={"provider": "openai"})
    assert resp.status_code == 200
    data = resp.json()
    assert all(r["provider"] == "openai" for r in data)


@pytest.mark.asyncio
async def test_get_request_logs_filter_run_id(client, db_session):
    await _seed_llm_call(db_session, call_id="call-r1", run_id="specific-run")
    resp = await client.get("/api/logs/requests", params={"run_id": "specific-run"})
    assert resp.status_code == 200
    data = resp.json()
    assert all(r["run_id"] == "specific-run" for r in data)


@pytest.mark.asyncio
async def test_get_request_logs_filter_status(client, db_session):
    await _seed_llm_call(db_session, call_id="call-failed", status="failed", error="timeout", run_id="run-789")
    resp = await client.get("/api/logs/requests", params={"status": "failed"})
    assert resp.status_code == 200
    data = resp.json()
    assert all(r["status"] == "failed" for r in data)


@pytest.mark.asyncio
async def test_get_request_logs_pagination(client, db_session):
    for i in range(5):
        await _seed_llm_call(db_session, call_id=f"page-call-{i}", run_id="paged-run")
    resp = await client.get("/api/logs/requests", params={"limit": 2, "offset": 0, "run_id": "paged-run"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) <= 2


@pytest.mark.asyncio
async def test_current_schema_request_logs_filter_universe_and_preserve_tick_zero(current_schema_session):
    matching = current_models.LLMCall(
        id=uuid4(),
        big_bang_id=None,
        provider="openrouter",
        model="openai/gpt-4o",
        purpose="god_agent_review",
        status="succeeded",
        meta={"universe_id": "u-keep", "tick": 0},
        created_at=datetime.now(UTC),
    )
    other = current_models.LLMCall(
        id=uuid4(),
        big_bang_id=None,
        provider="openrouter",
        model="openai/gpt-4o",
        purpose="god_agent_review",
        status="succeeded",
        meta={"universe_id": "u-drop", "tick": 9},
        created_at=datetime.now(UTC),
    )
    current_schema_session.add_all([matching, other])
    await current_schema_session.commit()

    data = await get_request_logs(
        current_schema_session,
        provider=None,
        status=None,
        run_id=None,
        universe_id="u-keep",
        limit=100,
        offset=0,
    )

    assert [item.call_id for item in data] == [str(matching.id)]
    assert data[0].tick == 0


@pytest.mark.asyncio
async def test_current_schema_trace_matches_universe_in_meta_and_payload(current_schema_session):
    universe_id = str(uuid4())
    llm = current_models.LLMCall(
        id=uuid4(),
        big_bang_id=None,
        provider="openrouter",
        model="openai/gpt-4o",
        purpose="god_agent_review",
        status="failed",
        meta={"universe_id": universe_id, "tick_index": 0, "error": "bad"},
        created_at=datetime.now(UTC),
    )
    job = current_models.Job(
        id=uuid4(),
        job_type="run_multiverse_tick",
        queue_name="p1",
        status="failed",
        big_bang_id=None,
        payload={
            "multiverse_id": universe_id,
            "tick": 0,
            "scenario_text": "raw private scenario",
            "model_config": {"api_key": "secret"},
        },
        result={},
        error="boom",
        idempotency_key="trace-job-key",
        attempt_number=0,
        max_attempts=3,
        retryable=True,
        created_at=datetime.now(UTC),
    )
    current_schema_session.add_all([llm, job])
    await current_schema_session.commit()

    data = await get_trace(universe_id, current_schema_session)

    assert [item.call_id for item in data.llm_calls] == [str(llm.id)]
    assert data.llm_calls[0].tick == 0
    assert [item.job_id for item in data.jobs] == [str(job.id)]
    assert data.jobs[0].tick == 0
    assert "scenario_text" not in data.jobs[0].payload
    assert data.jobs[0].payload["scenario_text_present"] is True
    assert data.jobs[0].payload["model_config"] == "[REDACTED]"


# ---------------------------------------------------------------------------
# GET /api/logs/webhooks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_webhook_logs_returns_list(client, caplog):
    """Should return empty list (or real data if table exists)."""
    with caplog.at_level(logging.WARNING):
        resp = await client.get("/api/logs/webhooks")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert "webhook_events table not available" not in caplog.text


# ---------------------------------------------------------------------------
# GET /api/logs/errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_error_logs_empty(client):
    resp = await client.get("/api/logs/errors")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_get_error_logs_includes_failed_jobs(client, db_session):
    await _seed_failed_job(db_session)
    resp = await client.get("/api/logs/errors")
    assert resp.status_code == 200
    data = resp.json()
    assert any(e["source"] == "job" and e["status"] == "failed" for e in data)


@pytest.mark.asyncio
async def test_get_error_logs_includes_llm_errors(client, db_session):
    await _seed_llm_call(db_session, call_id="call-err-1", status="failed", error="provider timeout", run_id="run-err")
    resp = await client.get("/api/logs/errors")
    assert resp.status_code == 200
    data = resp.json()
    assert any(e["source"] == "llm_call" and e["error"] == "provider timeout" for e in data)


@pytest.mark.asyncio
async def test_get_error_logs_filter_run_id(client, db_session):
    await _seed_failed_job(db_session, job_id="fail-filtered", run_id="filter-run")
    resp = await client.get("/api/logs/errors", params={"run_id": "filter-run"})
    assert resp.status_code == 200
    data = resp.json()
    assert all(e["run_id"] == "filter-run" for e in data)


@pytest.mark.asyncio
async def test_get_error_logs_applies_pagination_after_merge(client, db_session):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(3):
        await _seed_failed_job(
            db_session,
            job_id=f"merge-job-{i}",
            run_id="merge-run",
            created_at=base + timedelta(minutes=10 - i),
        )
    for i in range(3):
        await _seed_llm_call(
            db_session,
            call_id=f"merge-call-{i}",
            status="failed",
            error="provider failed",
            run_id="merge-run",
            created_at=base + timedelta(minutes=i),
        )

    resp = await client.get("/api/logs/errors", params={"run_id": "merge-run", "limit": 1, "offset": 2})

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "merge-job-2"


# ---------------------------------------------------------------------------
# GET /api/logs/audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_audit_logs_placeholder(client):
    """Audit log returns lifecycle entries when jobs have been persisted."""
    resp = await client.get("/api/logs/audit")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert all(item["resource"].startswith("job:") for item in data)


# ---------------------------------------------------------------------------
# GET /api/logs/traces/{trace_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_trace_returns_empty_for_unknown(client):
    resp = await client.get("/api/logs/traces/unknown-trace-id")
    assert resp.status_code == 200
    data = resp.json()
    assert data["trace_id"] == "unknown-trace-id"
    assert data["llm_calls"] == []
    assert data["jobs"] == []


@pytest.mark.asyncio
async def test_get_trace_joins_llm_calls_and_jobs(client, db_session):
    trace_id = "trace-run-xyz"
    await _seed_llm_call(db_session, call_id="trace-call-1", run_id=trace_id)
    await _seed_failed_job(db_session, job_id="trace-job-1", run_id=trace_id)

    resp = await client.get(f"/api/logs/traces/{trace_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["trace_id"] == trace_id
    assert len(data["llm_calls"]) >= 1
    assert len(data["jobs"]) >= 1
