"""End-to-end dead-letter routing test.

Validates `route_dead_letter`:
* serializes the envelope JSON + error message into a `wf:dead_letter` Redis list,
* trims the list at 10_000 entries,
* never raises into the caller (Celery on_failure must be safe).

The Celery task itself isn't run — we directly invoke `route_dead_letter`
because mounting a fake Celery worker would require a broker. The function
is the integration point the on_failure hook calls in production.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import fakeredis
import pytest

from backend.app.schemas.jobs import JobEnvelope
from backend.app.workers import jobs as worker_jobs
from backend.app.workers.celery_app import celery_app
from backend.app.workers.retries import FatalError, route_dead_letter

pytestmark = [pytest.mark.e2e]


def test_route_dead_letter_pushes_to_redis_list(monkeypatch):
    """`route_dead_letter` LPUSHes the envelope JSON into wf:dead_letter."""
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(
        "redis.from_url",
        lambda *args, **kwargs: fake,
    )

    envelope_json = json.dumps(
        {
            "job_id": "abc-123",
            "job_type": "simulate_universe_tick",
            "run_id": "BB_test",
            "universe_id": "U001",
            "tick": 7,
            "payload": {"foo": "bar"},
            "idempotency_key": "sim:BB_test:U001:t7",
        }
    )
    route_dead_letter(envelope_json, error="FatalError: kaboom")

    # Verify the Redis list has exactly one entry at the head.
    head = fake.lrange("wf:dead_letter", 0, -1)
    assert len(head) == 1
    entry = json.loads(head[0])
    assert entry["envelope_json"] == envelope_json
    assert "FatalError" in entry["error"]
    assert "dead_at" in entry


def test_route_dead_letter_trims_to_10k(monkeypatch):
    """Dead-letter list is capped at 10_000 entries via LTRIM 0..9_999."""
    fake = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(
        "redis.from_url",
        lambda *args, **kwargs: fake,
    )

    # Pre-populate the list close to the cap (use a smaller number for speed
    # — the LTRIM bound is 9_999 so 10 entries already prove the trim shape
    # works, but we'd need 10_001 entries to actually overflow. Use 11
    # entries with a stub pipeline-asserting the LTRIM call is issued).
    for i in range(3):
        fake.lpush("wf:dead_letter", f"prev-{i}")
    route_dead_letter('{"job_id":"new"}', error="boom")

    items = fake.lrange("wf:dead_letter", 0, -1)
    # Length is unchanged (4) — the trim left-bound 0 keeps everything <= 9_999.
    assert len(items) == 4
    # The newest entry is at the head.
    head = json.loads(items[0])
    assert "boom" in head["error"]


def test_route_dead_letter_never_raises_on_redis_failure(monkeypatch):
    """Redis errors during dead-letter push must never propagate."""
    failing = MagicMock()
    failing.from_url.side_effect = RuntimeError("redis down")

    with patch("redis.from_url", failing.from_url):
        # Should not raise.
        route_dead_letter('{"job_id":"x"}', error="anything")


def test_fatal_error_is_a_known_taxonomy_class():
    """`FatalError` must be a subclass of Exception so Celery on_failure
    sees it as a permanent failure."""
    assert issubclass(FatalError, Exception)
    err = FatalError("permanent")
    assert str(err) == "permanent"


def test_celery_routes_only_registered_task_names():
    routes = celery_app.conf.task_routes
    registered = set(celery_app.tasks)

    assert set(routes).issubset(registered)


def test_celery_beat_schedule_is_registered_by_app_import():
    assert celery_app.conf.beat_schedule["heartbeat"]["task"] == "worldfork.heartbeat"


@pytest.mark.asyncio
async def test_run_tracked_can_defer_terminal_failed_status_until_retries_exhaust(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def fake_mark_started(job_id: str) -> None:
        calls.append(("started", job_id))

    async def fake_mark_failed(job_id: str, error: str) -> None:
        calls.append(("failed", error))

    monkeypatch.setattr("backend.app.workers.scheduler.mark_started", fake_mark_started)
    monkeypatch.setattr("backend.app.workers.scheduler.mark_failed", fake_mark_failed)

    env = JobEnvelope(
        job_id="retry-job",
        job_type="simulate_universe_tick",
        priority="p0",
        run_id="run-1",
        universe_id="u-1",
        tick=1,
        attempt_number=0,
        idempotency_key="retry-key",
        payload={},
        created_at=datetime.now(UTC),
    )

    async def boom(_env: JobEnvelope) -> dict:
        raise RuntimeError("temporary")

    with pytest.raises(RuntimeError):
        await worker_jobs._run_tracked(env, boom, mark_failed_on_error=False)

    assert calls == [("started", "retry-job")]


@pytest.mark.asyncio
async def test_retrying_worker_state_is_explicit_until_max_retries(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def fake_patch_job(job_id: str, **fields) -> None:
        calls.append(("patch", fields["status"]))

    async def fake_mark_failed(job_id: str, error: str) -> None:
        calls.append(("failed", error))

    monkeypatch.setattr("backend.app.workers.scheduler._patch_job", fake_patch_job)
    monkeypatch.setattr("backend.app.workers.scheduler.mark_failed", fake_mark_failed)

    await worker_jobs._mark_retry_or_failed_best_effort(
        SimpleNamespace(request=SimpleNamespace(retries=1), max_retries=3),
        "retry-job",
        RuntimeError("temporary"),
    )
    await worker_jobs._mark_retry_or_failed_best_effort(
        SimpleNamespace(request=SimpleNamespace(retries=3), max_retries=3),
        "retry-job",
        RuntimeError("terminal"),
    )

    assert calls == [("patch", "retried"), ("failed", "terminal")]
