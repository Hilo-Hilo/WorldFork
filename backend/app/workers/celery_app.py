"""Celery application factory for WorldFork workers."""
from __future__ import annotations

from celery import Celery
from kombu import Queue

from backend.app.core.config import settings
from backend.app.core.logging import configure_logging

celery_app = Celery(
    "worldfork",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=[
        "backend.app.workers.jobs",  # populated by B3-C/B4-*
        "app.domains.jobs.workers",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    result_expires=86400,
    task_default_queue="p1",
    task_queues=(
        Queue("p0"),
        Queue("p1"),
        Queue("p2"),
        Queue("p3"),
        Queue("dead_letter"),
    ),
    task_routes={
        "simulate_universe_tick": {"queue": "p0"},
        "branch_universe": {"queue": "p0"},
        "apply_tick_results": {"queue": "p0"},
        "actor_deliberation_call": {"queue": "p1"},
        "social_propagation": {"queue": "p1"},
        "execute_due_events": {"queue": "p1"},
        "sociology_update": {"queue": "p1"},
        "god_agent_review": {"queue": "p1"},
        "force_deviation": {"queue": "p0"},
        "sync_zep_memory": {"queue": "p2"},
        "build_review_index": {"queue": "p2"},
        "aggregate_run_results": {"queue": "p2"},
        "export_run": {"queue": "p3"},
        "initialize_big_bang": {"queue": "p1"},
        "run_canonical_job": {"queue": "p1"},
        "worldfork.execute_job": {"queue": "p1"},
    },
    task_default_retry_delay=10,
    task_time_limit=600,
    task_soft_time_limit=540,
)


@celery_app.on_after_configure.connect
def setup_logging(sender, **kwargs):
    configure_logging()


# NOTE: We DO NOT register LLM providers in worker_process_init.
# Each Celery task uses its own asyncio.run() (fresh event loop), and any cached
# redis.asyncio client / async resource bound to the previous loop will fail with
# "Future attached to a different loop". Each task body calls
# `await ensure_providers_in_loop()` from backend.app.providers instead, which
# (re-)builds per-loop adapters as needed.
try:
    from backend.app.workers import beat_schedule as _beat_schedule  # noqa: F401
except Exception:
    pass

import app.domains.jobs.workers as _db_job_workers  # noqa: E402,F401
