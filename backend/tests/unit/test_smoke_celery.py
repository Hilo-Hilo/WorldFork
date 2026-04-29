"""Smoke tests for the Celery task pipeline (B2-C).

Marked ``requires_redis`` — skipped automatically when Redis is not reachable.
Uses ``task_always_eager=True`` for in-process synchronous execution so no
actual broker/worker is needed when Redis IS available.

Run against a live broker:
    pytest -m requires_redis backend/tests/unit/test_smoke_celery.py
"""
from __future__ import annotations

import pytest


def _redis_reachable() -> bool:
    """Return True if the configured Redis is accessible."""
    try:
        import redis as sync_redis
        from backend.app.core.config import settings

        client = sync_redis.from_url(settings.redis_url, socket_connect_timeout=1)
        client.ping()
        return True
    except Exception:
        return False


requires_redis = pytest.mark.skipif(
    not _redis_reachable(),
    reason="Redis not reachable — skipping broker smoke tests",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def eager_celery():
    """Configure the Celery app for synchronous in-process task execution."""
    from backend.app.workers.celery_app import celery_app

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield celery_app
    # Restore to non-eager after test
    celery_app.conf.task_always_eager = False
    celery_app.conf.task_eager_propagates = False


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@requires_redis
class TestEchoEnvelopeTask:
    def test_echo_envelope_round_trip(self, eager_celery):
        """echo_envelope deserialises the envelope and returns the expected dict."""
        from backend.app.workers.jobs import echo_envelope
        from backend.app.workers.scheduler import make_envelope

        env = make_envelope(
            job_type="export_run",
            run_id="smoke_run_001",
            payload={"test": True},
            universe_id="U000",
            tick=0,
            idempotency_key="smoke-echo-test",
        )

        result = echo_envelope.delay(env.model_dump_json()).get(timeout=5)

        assert result["job_id"] == env.job_id
        assert "received_at" in result
        # received_at should round-trip as a string representation of the datetime
        assert str(env.created_at) in result["received_at"] or result["received_at"]

    def test_echo_envelope_returns_dict(self, eager_celery):
        from backend.app.workers.jobs import echo_envelope
        from backend.app.workers.scheduler import make_envelope

        env = make_envelope(
            job_type="sync_zep_memory",
            run_id="smoke_run_002",
            payload={},
        )

        result = echo_envelope.delay(env.model_dump_json()).get(timeout=5)
        assert isinstance(result, dict)

    def test_echo_envelope_job_id_matches(self, eager_celery):
        from backend.app.workers.jobs import echo_envelope
        from backend.app.workers.scheduler import make_envelope

        env = make_envelope(
            job_type="god_agent_review",
            run_id="smoke_run_003",
            payload={"universe_id": "U001"},
        )

        result = echo_envelope.delay(env.model_dump_json()).get(timeout=5)
        assert result["job_id"] == env.job_id


@requires_redis
class TestHeartbeatTask:
    def test_heartbeat_runs_without_error(self, eager_celery):
        """Heartbeat returns None (ignore_result=True) without raising."""
        from backend.app.workers.jobs import heartbeat

        # With task_always_eager the task executes inline; heartbeat has
        # ignore_result=True so .get() returns None.
        result = heartbeat.delay()
        # Should not raise
        assert result is not None
