"""Queue names and job-type-to-queue mapping for WorldFork workers."""
from __future__ import annotations

from enum import StrEnum

from backend.app.schemas.jobs import JobType


class Queues(StrEnum):
    P0 = "p0"
    P1 = "p1"
    P2 = "p2"
    P3 = "p3"
    DEAD_LETTER = "dead_letter"


# Maps every JobType literal to its target queue.
# Mirrors celery_app.conf.task_routes.
QUEUE_FOR_JOB: dict[str, Queues] = {
    "simulate_universe_tick": Queues.P0,
    "branch_universe": Queues.P0,
    "apply_tick_results": Queues.P0,
    "actor_deliberation_call": Queues.P1,
    "social_propagation": Queues.P1,
    "execute_due_events": Queues.P1,
    "sociology_update": Queues.P1,
    "god_agent_review": Queues.P1,
    "initialize_big_bang": Queues.P1,
    "build_review_index": Queues.P2,
    "aggregate_run_results": Queues.P2,
    "force_deviation": Queues.P0,
    "export_run": Queues.P3,
}


def queue_for_job(job_type: JobType) -> Queues:
    """Return the queue for a given job type.

    Falls back to P1 (the default queue) if the job type is not explicitly
    mapped — this keeps things safe as new job types are added by B3/B4.
    """
    return QUEUE_FOR_JOB.get(job_type, Queues.P1)
