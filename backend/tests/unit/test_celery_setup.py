"""Non-broker unit tests for the Celery scaffolding layer (B2-C).

All assertions run without a live Redis / broker connection.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from backend.app.schemas.jobs import JobEnvelope
from backend.app.workers.celery_app import celery_app
from backend.app.workers.queues import Queues, queue_for_job
from backend.app.workers.scheduler import make_envelope


# ---------------------------------------------------------------------------
# celery_app configuration
# ---------------------------------------------------------------------------

class TestCeleryAppConfig:
    def test_task_serializer_is_json(self):
        assert celery_app.conf.task_serializer == "json"

    def test_accept_content_is_json_only(self):
        assert celery_app.conf.accept_content == ["json"]

    def test_result_serializer_is_json(self):
        assert celery_app.conf.result_serializer == "json"

    def test_acks_late_enabled(self):
        assert celery_app.conf.task_acks_late is True

    def test_reject_on_worker_lost(self):
        assert celery_app.conf.task_reject_on_worker_lost is True

    def test_timezone_utc(self):
        assert celery_app.conf.timezone == "UTC"

    def test_enable_utc(self):
        assert celery_app.conf.enable_utc is True

    def test_prefetch_multiplier_one(self):
        assert celery_app.conf.worker_prefetch_multiplier == 1

    def test_result_expires(self):
        assert celery_app.conf.result_expires == 86400

    def test_default_queue(self):
        assert celery_app.conf.task_default_queue == "p1"

    def test_default_retry_delay(self):
        assert celery_app.conf.task_default_retry_delay == 10

    def test_time_limit(self):
        assert celery_app.conf.task_time_limit == 3600

    def test_soft_time_limit(self):
        assert celery_app.conf.task_soft_time_limit == 3300


# ---------------------------------------------------------------------------
# task_routes — critical queue assignments
# ---------------------------------------------------------------------------

class TestTaskRoutes:
    def test_simulate_universe_tick_routes_to_p0(self):
        assert celery_app.conf.task_routes["simulate_universe_tick"]["queue"] == "p0"

    def test_branch_universe_routes_to_p0(self):
        assert celery_app.conf.task_routes["branch_universe"]["queue"] == "p0"

    def test_apply_tick_results_routes_to_p0(self):
        assert celery_app.conf.task_routes["apply_tick_results"]["queue"] == "p0"

    def test_actor_deliberation_call_routes_to_p1(self):
        assert celery_app.conf.task_routes["actor_deliberation_call"]["queue"] == "p1"

    def test_legacy_agent_deliberation_batch_route_is_removed(self):
        assert "agent_deliberation_batch" not in celery_app.conf.task_routes

    def test_social_propagation_routes_to_p1(self):
        assert celery_app.conf.task_routes["social_propagation"]["queue"] == "p1"

    def test_execute_due_events_routes_to_p1(self):
        assert celery_app.conf.task_routes["execute_due_events"]["queue"] == "p1"

    def test_sociology_update_routes_to_p1(self):
        assert celery_app.conf.task_routes["sociology_update"]["queue"] == "p1"

    def test_god_agent_review_routes_to_p1(self):
        assert celery_app.conf.task_routes["god_agent_review"]["queue"] == "p1"

    def test_sync_zep_memory_routes_to_p2(self):
        assert celery_app.conf.task_routes["sync_zep_memory"]["queue"] == "p2"

    def test_build_review_index_routes_to_p2(self):
        assert celery_app.conf.task_routes["build_review_index"]["queue"] == "p2"

    def test_export_run_routes_to_p3(self):
        assert celery_app.conf.task_routes["export_run"]["queue"] == "p3"

    def test_initialize_big_bang_routes_to_p1(self):
        assert celery_app.conf.task_routes["initialize_big_bang"]["queue"] == "p1"


# ---------------------------------------------------------------------------
# task_queues — all priority queues declared
# ---------------------------------------------------------------------------

class TestTaskQueues:
    def _queue_names(self):
        return [q.name for q in celery_app.conf.task_queues]

    def test_p0_declared(self):
        assert "p0" in self._queue_names()

    def test_p1_declared(self):
        assert "p1" in self._queue_names()

    def test_p2_declared(self):
        assert "p2" in self._queue_names()

    def test_p3_declared(self):
        assert "p3" in self._queue_names()

    def test_dead_letter_declared(self):
        assert "dead_letter" in self._queue_names()


# ---------------------------------------------------------------------------
# Queues enum
# ---------------------------------------------------------------------------

class TestQueuesEnum:
    def test_p0_value(self):
        assert Queues.P0 == "p0"

    def test_p1_value(self):
        assert Queues.P1 == "p1"

    def test_p2_value(self):
        assert Queues.P2 == "p2"

    def test_p3_value(self):
        assert Queues.P3 == "p3"

    def test_dead_letter_value(self):
        assert Queues.DEAD_LETTER == "dead_letter"


# ---------------------------------------------------------------------------
# queue_for_job — full mapping coverage
# ---------------------------------------------------------------------------

_EXPECTED_MAPPING: dict[str, Queues] = {
    "simulate_universe_tick": Queues.P0,
    "branch_universe": Queues.P0,
    "apply_tick_results": Queues.P0,
    "actor_deliberation_call": Queues.P1,
    "social_propagation": Queues.P1,
    "execute_due_events": Queues.P1,
    "sociology_update": Queues.P1,
    "god_agent_review": Queues.P1,
    "initialize_big_bang": Queues.P1,
    "sync_zep_memory": Queues.P2,
    "build_review_index": Queues.P2,
    "export_run": Queues.P3,
}


class TestQueueForJob:
    @pytest.mark.parametrize("job_type,expected_queue", list(_EXPECTED_MAPPING.items()))
    def test_queue_mapping(self, job_type, expected_queue):
        # Cast to the literal type via the runtime value
        result = queue_for_job(job_type)  # type: ignore[arg-type]
        assert result == expected_queue

    def test_queue_for_job_returns_queues_instance(self):
        result = queue_for_job("simulate_universe_tick")
        assert isinstance(result, Queues)

    def test_legacy_agent_deliberation_batch_rejected_by_envelope_contract(self):
        with pytest.raises(ValidationError):
            JobEnvelope.model_validate(
                {
                    "job_id": "job_legacy",
                    "job_type": "agent_deliberation_batch",
                    "priority": "p1",
                    "run_id": "run_test",
                    "attempt_number": 1,
                    "idempotency_key": "legacy",
                    "payload": {},
                    "created_at": datetime.now(UTC),
                }
            )


# ---------------------------------------------------------------------------
# make_envelope — stable redis_key across attempts when key is reused
# ---------------------------------------------------------------------------

class TestMakeEnvelope:
    def _make(self, idem_key: str | None = None, attempt: int = 1) -> "JobEnvelope":  # noqa: F821

        return make_envelope(
            job_type="export_run",
            run_id="run_test_001",
            payload={"foo": "bar"},
            universe_id="U000",
            tick=3,
            idempotency_key=idem_key,
            attempt_number=attempt,
        )

    def test_stable_redis_key_across_attempts_when_idem_key_is_reused(self):
        env1 = self._make(idem_key="my-fixed-key", attempt=1)
        env2 = self._make(idem_key="my-fixed-key", attempt=2)
        # The redis_key is keyed on idempotency_key, not attempt_number.
        assert env1.redis_key() == env2.redis_key()

    def test_different_idem_keys_produce_different_redis_keys(self):
        env1 = self._make(idem_key="key-alpha")
        env2 = self._make(idem_key="key-beta")
        assert env1.redis_key() != env2.redis_key()

    def test_auto_idem_key_is_deterministic(self):
        """Same args → same idempotency_key (and thus same redis_key)."""
        env1 = self._make()
        env2 = self._make()
        assert env1.idempotency_key == env2.idempotency_key
        assert env1.redis_key() == env2.redis_key()

    def test_envelope_priority_matches_queue(self):
        env = self._make()
        # export_run maps to P3
        assert env.priority == "p3"

    def test_envelope_priority_override(self):

        env = make_envelope(
            job_type="export_run",
            run_id="run_test_001",
            payload={},
            priority_override=Queues.P0,
        )
        assert env.priority == "p0"

    def test_envelope_fields_populated(self):
        env = self._make(idem_key="explicit-key")
        assert env.run_id == "run_test_001"
        assert env.universe_id == "U000"
        assert env.tick == 3
        assert env.attempt_number == 1
        assert env.idempotency_key == "explicit-key"
        assert env.job_type == "export_run"
        assert env.created_at is not None
