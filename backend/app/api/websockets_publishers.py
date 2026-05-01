"""High-level pub/sub publisher wrappers used by the tick runner and branching engine.

Each helper publishes to the relevant channel(s) so that all connected
WebSocket clients (any uvicorn worker) receive the event immediately.

Celery tasks (sync) must wrap these with ``asyncio.run(publish_*(...))``.
"""
from __future__ import annotations

from app.domains.legacy.pubsub import (
    jobs_channel,
    publish,
    run_channel,
    universe_channel,
)

# ---------------------------------------------------------------------------
# Tick events
# ---------------------------------------------------------------------------


async def publish_tick_completed(
    *,
    run_id: str,
    universe_id: str,
    tick: int,
    metrics: dict,
) -> None:
    """Publish tick.completed to both the run and universe channels."""
    payload = {"run_id": run_id, "universe_id": universe_id, "tick": tick, "metrics": metrics}
    await publish(universe_channel(universe_id), "tick.completed", payload)
    await publish(run_channel(run_id), "tick.completed", payload)


async def publish_tick_started(
    *,
    run_id: str,
    universe_id: str,
    tick: int,
) -> None:
    """Publish tick.started to both the run and universe channels."""
    payload = {"run_id": run_id, "universe_id": universe_id, "tick": tick}
    await publish(universe_channel(universe_id), "tick.started", payload)
    await publish(run_channel(run_id), "tick.started", payload)


# ---------------------------------------------------------------------------
# Branch events
# ---------------------------------------------------------------------------


async def publish_branch_created(
    *,
    run_id: str,
    parent_universe_id: str,
    child_universe_id: str,
    branch_from_tick: int,
    depth: int,
) -> None:
    """Publish branch.created to the parent universe and run channels."""
    payload = {
        "parent_universe_id": parent_universe_id,
        "child_universe_id": child_universe_id,
        "run_id": run_id,
        "branch_from_tick": branch_from_tick,
        "depth": depth,
    }
    await publish(universe_channel(parent_universe_id), "branch.created", payload)
    await publish(run_channel(run_id), "branch.created", payload)


async def publish_branch_frozen(
    *,
    run_id: str,
    universe_id: str,
    frozen_at_tick: int,
) -> None:
    """Publish branch.frozen to the universe and run channels."""
    payload = {"run_id": run_id, "universe_id": universe_id, "frozen_at_tick": frozen_at_tick}
    await publish(universe_channel(universe_id), "branch.frozen", payload)
    await publish(run_channel(run_id), "branch.frozen", payload)


async def publish_branch_killed(
    *,
    run_id: str,
    universe_id: str,
    killed_at_tick: int,
    reason: str = "",
) -> None:
    """Publish branch.killed to the universe and run channels."""
    payload = {
        "universe_id": universe_id,
        "run_id": run_id,
        "killed_at_tick": killed_at_tick,
        "reason": reason,
    }
    await publish(universe_channel(universe_id), "branch.killed", payload)
    await publish(run_channel(run_id), "branch.killed", payload)


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------


async def publish_run_status_changed(
    *,
    run_id: str,
    status: str,
    universe_id: str | None = None,
) -> None:
    """Publish run.status_changed to the run channel."""
    payload: dict = {"run_id": run_id, "status": status}
    if universe_id:
        payload["universe_id"] = universe_id
    await publish(run_channel(run_id), "run.status_changed", payload)


async def publish_metrics_updated(
    *,
    run_id: str,
    universe_id: str,
    tick: int,
    metrics: dict,
) -> None:
    """Publish metrics.updated to both the run and universe channels."""
    payload = {"run_id": run_id, "universe_id": universe_id, "tick": tick, "metrics": metrics}
    await publish(universe_channel(universe_id), "metrics.updated", payload)
    await publish(run_channel(run_id), "metrics.updated", payload)


# ---------------------------------------------------------------------------
# Social / sociology events
# ---------------------------------------------------------------------------


async def publish_social_post_created(
    *,
    universe_id: str,
    post_id: str,
    author_id: str,
    tick: int,
    content_summary: str = "",
) -> None:
    """Publish social_post.created to the universe channel."""
    payload = {
        "post_id": post_id,
        "author_id": author_id,
        "tick": tick,
        "content_summary": content_summary,
    }
    await publish(universe_channel(universe_id), "social_post.created", payload)


async def publish_event_scheduled(
    *,
    universe_id: str,
    event_id: str,
    event_type: str,
    scheduled_tick: int,
) -> None:
    """Publish event.scheduled to the universe channel."""
    payload = {
        "event_id": event_id,
        "event_type": event_type,
        "scheduled_tick": scheduled_tick,
    }
    await publish(universe_channel(universe_id), "event.scheduled", payload)


async def publish_cohort_split(
    *,
    universe_id: str,
    parent_cohort_id: str,
    child_cohort_ids: list[str],
    tick: int,
) -> None:
    """Publish cohort.split to the universe channel."""
    payload = {
        "parent_cohort_id": parent_cohort_id,
        "child_cohort_ids": child_cohort_ids,
        "tick": tick,
    }
    await publish(universe_channel(universe_id), "cohort.split", payload)


async def publish_cohort_merge(
    *,
    universe_id: str,
    source_cohort_ids: list[str],
    target_cohort_id: str,
    tick: int,
) -> None:
    """Publish cohort.merge to the universe channel."""
    payload = {
        "source_cohort_ids": source_cohort_ids,
        "target_cohort_id": target_cohort_id,
        "tick": tick,
    }
    await publish(universe_channel(universe_id), "cohort.merge", payload)


async def publish_god_decision(
    *,
    universe_id: str,
    run_id: str,
    tick: int,
    decision: str,
    branch_delta: dict | None = None,
) -> None:
    """Publish god.decision to the universe and run channels."""
    payload: dict = {
        "universe_id": universe_id,
        "run_id": run_id,
        "tick": tick,
        "decision": decision,
    }
    if branch_delta is not None:
        payload["branch_delta"] = branch_delta
    await publish(universe_channel(universe_id), "god.decision", payload)
    await publish(run_channel(run_id), "god.decision", payload)


# ---------------------------------------------------------------------------
# Job queue events
# ---------------------------------------------------------------------------


async def publish_job_enqueued(*, job_id: str, job_type: str, queue: str) -> None:
    """Publish job lifecycle event to the jobs:global channel."""
    payload = {"job_id": job_id, "job_type": job_type, "queue": queue}
    await publish(jobs_channel(), "job.enqueued", payload)


async def publish_job_started(*, job_id: str, job_type: str, worker: str = "") -> None:
    """Publish job.started to jobs:global."""
    payload = {"job_id": job_id, "job_type": job_type, "worker": worker}
    await publish(jobs_channel(), "job.started", payload)


async def publish_job_completed(
    *,
    job_id: str,
    job_type: str,
    duration_ms: float = 0.0,
) -> None:
    """Publish job.completed to jobs:global."""
    payload = {"job_id": job_id, "job_type": job_type, "duration_ms": duration_ms}
    await publish(jobs_channel(), "job.completed", payload)


async def publish_job_failed(
    *,
    job_id: str,
    job_type: str,
    error: str = "",
    retrying: bool = False,
) -> None:
    """Publish job.failed to jobs:global."""
    payload = {
        "job_id": job_id,
        "job_type": job_type,
        "error": error,
        "retrying": retrying,
    }
    await publish(jobs_channel(), "job.failed", payload)


async def publish_job_status_changed(
    *,
    job_id: str,
    job_type: str,
    status: str,
    queue: str = "",
    error: str | None = None,
) -> None:
    """Publish a generic job status change to jobs:global."""
    payload = {
        "job_id": job_id,
        "job_type": job_type,
        "status": status,
        "queue": queue,
    }
    if error:
        payload["error"] = error
    await publish(jobs_channel(), "job.status_changed", payload)


async def publish_queue_depth(*, queue: str, depth: int) -> None:
    """Publish a queue depth snapshot to jobs:global."""
    payload = {"queue": queue, "depth": depth}
    await publish(jobs_channel(), "queue.depth", payload)


async def publish_worker_status(
    *,
    worker_id: str,
    status: str,
    active_tasks: int = 0,
) -> None:
    """Publish a worker heartbeat/status update to jobs:global."""
    payload = {"worker_id": worker_id, "status": status, "active_tasks": active_tasks}
    await publish(jobs_channel(), "worker.status", payload)
