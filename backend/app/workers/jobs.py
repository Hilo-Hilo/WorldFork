"""Celery task definitions for WorldFork workers.

All tasks follow the §A.5 pattern:

* sync Celery wrapper that decodes the :class:`JobEnvelope`
* delegates to an async ``_async_*`` helper via ``asyncio.run``
* opens its own DB session + ledger inside the helper (never share state
  across task boundaries)
* re-raises into ``self.retry`` for transient failures

Task naming matches the JobType literals in :mod:`backend.app.schemas.jobs`
exactly so :data:`celery_app.conf.task_routes` can route by name.

Tasks
-----
- ``initialize_big_bang`` (P1)
- ``simulate_universe_tick`` (P0)  — the §11.1 loop entry
  - ``apply_tick_results`` (P0)      — split-task callback
- ``actor_deliberation_call`` (P1) — one actor packet → one parsed dict
- ``branch_universe`` (P0)
- ``sync_zep_memory`` (P2)
- ``aggregate_run_results`` (P2)
- ``export_run`` (P3)
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from backend.app.core.logging import get_logger
from backend.app.schemas.jobs import JobEnvelope
from backend.app.workers.celery_app import celery_app

logger = get_logger(__name__)
_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Heartbeat — registered for Celery Beat (beat_schedule.py)
# ---------------------------------------------------------------------------

@celery_app.task(name="worldfork.heartbeat", bind=True, ignore_result=True)
def heartbeat(self):  # type: ignore[no-untyped-def]
    """Periodic liveness probe.  Runs every 30 s via Beat."""
    logger.info("heartbeat", task_id=self.request.id)


# ---------------------------------------------------------------------------
# Echo envelope — smoke test for end-to-end serialization round-trip
# ---------------------------------------------------------------------------

@celery_app.task(name="echo_envelope", bind=True)
def echo_envelope(self, envelope_json: str) -> dict:  # type: ignore[no-untyped-def]
    """Deserialize a JobEnvelope and echo key fields.

    Useful for manual serialization diagnostics without spinning up a real broker.
    """
    env = JobEnvelope.model_validate_json(envelope_json)
    logger.info("echo", job_id=env.job_id, job_type=env.job_type)
    return {
        "job_id": env.job_id,
        "received_at": str(env.created_at),
    }


# ---------------------------------------------------------------------------
# Shared infrastructure helpers
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="run_canonical_job",
    acks_late=True,
    max_retries=0,
    soft_time_limit=3300,
    time_limit=3600,
)
def run_canonical_job_task(self, job_id: str):  # type: ignore[no-untyped-def]
    """Execute one canonical row from the ``jobs`` table.

    The public /api/jobs control plane stores durable job rows. This task is a
    bridge from that table-backed queue into the running Celery worker pool.
    """
    from app.db import models
    from app.db.session import SessionLocal
    from app.domains.jobs.executor import JobNotRunnableError, execute_job

    db = SessionLocal()
    try:
        job = db.get(models.Job, job_id)
        if job is None:
            return {"job_id": job_id, "status": "missing"}
        try:
            execute_job(db, job, commit_running=True)
        except JobNotRunnableError:
            db.rollback()
            db.refresh(job)
            return {"job_id": str(job.id), "status": job.status, "error": job.error}
        db.commit()
        return {
            "job_id": str(job.id),
            "job_type": job.job_type,
            "status": job.status,
            "result": job.result or {},
            "error": job.error,
        }
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def _open_session():
    """Async-context-managed DB session for the current task lifecycle."""
    from backend.app.core.db import SessionLocal

    return SessionLocal()


def _open_ledger(run_id: str):
    """Open a :class:`Ledger` for ``run_id`` from the configured run root."""
    from backend.app.core.config import settings as _cfg
    from backend.app.storage.ledger import Ledger

    run_root = _cfg.run_root
    if run_root.name == "runs":
        run_root = run_root.parent
    return Ledger.open(run_root, run_id)


async def _build_routing_and_limiter(session):
    """Build a RoutingTable + ProviderRateLimiter for one task lifecycle."""
    from backend.app.core.redis_client import get_redis_client
    from backend.app.providers.routing import RoutingTable, build_provider_rate_limiter

    try:
        routing = await RoutingTable.from_db(session)
    except Exception:
        routing = RoutingTable.defaults()

    redis = get_redis_client()
    limiter = await build_provider_rate_limiter(session, redis, provider="openrouter")
    return routing, limiter


async def _run_tracked(
    env: JobEnvelope,
    impl: Callable[[JobEnvelope], Awaitable[dict]],
    *,
    mark_failed_on_error: bool = True,
) -> dict:
    """Run one envelope and mirror lifecycle state into the jobs table."""
    from backend.app.workers import scheduler

    await scheduler.mark_started(env.job_id)
    try:
        result = await impl(env)
    except Exception as exc:
        if env.job_type == "simulate_universe_tick" and env.universe_id and env.tick is not None:
            try:
                await scheduler.clear_running(
                    f"sim:{env.run_id}:{env.universe_id}:t{env.tick}:a{env.attempt_number}"
                )
            except Exception:
                pass
        if mark_failed_on_error:
            await scheduler.mark_failed(env.job_id, str(exc))
        raise

    if isinstance(result, dict):
        status = str(result.get("status") or "").lower()
        if status == "failed" or status.startswith("no_") or status.endswith("_failure"):
            idem_key = result.get("idempotency_key")
            if isinstance(idem_key, str):
                try:
                    await scheduler.clear_running(idem_key)
                except Exception:
                    pass
            await scheduler.mark_failed(env.job_id, str(result.get("error") or status))
            return result

    artifact_path: str | None = None
    maybe_result: Any = result.get("result") if isinstance(result, dict) else None
    if isinstance(maybe_result, str):
        artifact_path = maybe_result
    await scheduler.mark_succeeded(
        env.job_id,
        result_summary=result if isinstance(result, dict) else {"result": result},
        artifact_path=artifact_path,
    )
    return result


# ---------------------------------------------------------------------------
# initialize_big_bang
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="initialize_big_bang",
    queue="p1",
    acks_late=True,
    max_retries=3,
)
def initialize_big_bang_task(self, envelope_json: str):  # type: ignore[no-untyped-def]
    """Run the Big Bang initializer for a new run.

    The envelope payload must contain at least ``scenario_text`` and
    ``display_name``.  After successful initialisation we enqueue the
    first tick on P0.
    """
    env = JobEnvelope.model_validate_json(envelope_json)
    try:
        return asyncio.run(_run_tracked(env, _initialize_big_bang_impl, mark_failed_on_error=False))
    except Exception as exc:  # noqa: BLE001
        asyncio.run(_mark_retry_or_failed_best_effort(self, env.job_id, exc))
        raise self.retry(exc=exc, countdown=30)  # noqa: B904


async def _initialize_big_bang_impl(env: JobEnvelope) -> dict:
    """Async impl of :func:`initialize_big_bang_task`."""
    from backend.app.providers import ensure_providers_in_loop
    from backend.app.core.config import settings as _settings
    await ensure_providers_in_loop(_settings)
    from backend.app.domains.big_bang.initializer import (
        InitializerInput,
        initialize_big_bang,
    )
    from backend.app.workers import scheduler

    payload = env.payload or {}
    uploaded_docs = list(payload.get("uploaded_docs") or [])
    if not uploaded_docs:
        uploaded_docs = [
            {
                "name": str(doc_id),
                "content_text": "",
                "content_type": "uploaded_doc_id",
            }
            for doc_id in list(payload.get("uploaded_doc_ids") or [])
        ]
    init_input = InitializerInput(
        scenario_text=str(payload.get("scenario_text", "")),
        display_name=str(payload.get("display_name", "Untitled")),
        uploaded_docs=uploaded_docs,
        time_horizon_label=str(payload.get("time_horizon_label", "1 month")),
        tick_duration_minutes=int(payload.get("tick_duration_minutes", 60)),
        max_ticks=int(payload.get("max_ticks", 30)),
        max_schedule_horizon_ticks=int(
            payload.get("max_schedule_horizon_ticks", 5)
        ),
        provider_snapshot_id=payload.get("provider_snapshot_id"),
        created_by_user_id=payload.get("created_by_user_id"),
        big_bang_id=env.run_id,
        root_universe_id=payload.get("root_universe_id"),
    )

    session_cm = await _open_session()
    async with session_cm as session:
        routing, limiter = await _build_routing_and_limiter(session)
        result = await initialize_big_bang(
            init_input,
            session=session,
            sot=None,
            provider_rate_limiter=limiter,
            run_root=None,
            routing=routing,
        )

        # Enqueue tick=1 on the new run.
        try:
            envelope = scheduler.make_envelope(
                job_type="simulate_universe_tick",
                run_id=result.big_bang_run.big_bang_id,
                universe_id=result.root_universe.universe_id,
                tick=1,
                payload={
                    "run_id": result.big_bang_run.big_bang_id,
                    "universe_id": result.root_universe.universe_id,
                    "tick": 1,
                },
            )
            await scheduler.enqueue(envelope)
        except Exception as exc:
            _log.debug("first-tick enqueue skipped: %s", exc)

    return {
        "run_id": result.big_bang_run.big_bang_id,
        "root_universe_id": result.root_universe.universe_id,
        "status": result.big_bang_run.status,
    }


# ---------------------------------------------------------------------------
# simulate_universe_tick
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="simulate_universe_tick",
    queue="p0",
    acks_late=True,
    max_retries=3,
)
def simulate_universe_tick_task(self, envelope_json: str):  # type: ignore[no-untyped-def]
    """Run the §11.1 tick loop for one (universe, tick) pair."""
    env = JobEnvelope.model_validate_json(envelope_json)
    try:
        return asyncio.run(_run_tracked(env, _simulate_universe_tick_impl, mark_failed_on_error=False))
    except Exception as exc:  # noqa: BLE001
        asyncio.run(_mark_retry_or_failed_best_effort(self, env.job_id, exc))
        raise self.retry(exc=exc, countdown=10)  # noqa: B904


async def _simulate_universe_tick_impl(env: JobEnvelope) -> dict:
    """Async impl of :func:`simulate_universe_tick_task`."""
    from backend.app.providers import ensure_providers_in_loop
    from backend.app.core.config import settings as _settings
    await ensure_providers_in_loop(_settings)
    from app.db import models as current_models
    from app.db.session import SessionLocal
    from backend.app.domains.tick.tick_runner import run_next_tick

    if env.universe_id is None or env.tick is None:
        raise ValueError("simulate_universe_tick envelope requires universe_id + tick")

    requested_tick = int(env.tick)
    attempt_number = int(env.attempt_number or 1)

    def _run() -> dict:
        try:
            multiverse_id = UUID(str(env.universe_id))
        except ValueError as exc:
            raise ValueError(f"multiverse {env.universe_id!r} is not a canonical UUID") from exc

        db = SessionLocal()
        try:
            multiverse = db.get(current_models.Multiverse, multiverse_id)
            if multiverse is None:
                raise ValueError(f"multiverse {env.universe_id!r} not found")
            tick = run_next_tick(
                db,
                multiverse=multiverse,
                idempotency_key=f"{env.universe_id}:tick:{requested_tick}:attempt:{attempt_number}",
            )
            if tick.tick_index != requested_tick:
                raise ValueError(
                    f"requested tick {requested_tick} but canonical runner returned tick {tick.tick_index}"
                )
            db.commit()
            return {
                "status": "completed" if tick.status == "final" else tick.status,
                "tick_snapshot_id": str(tick.id),
                "tick": tick.tick_index,
                "ui_label": tick.ui_label,
            }
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    return await asyncio.to_thread(_run)


# ---------------------------------------------------------------------------
# apply_tick_results — chord callback
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="apply_tick_results",
    queue="p0",
    acks_late=True,
    max_retries=3,
)
def apply_tick_results_task(  # type: ignore[no-untyped-def]
    self,
    results: list[dict],
    run_id: str,
    universe_id: str,
    tick: int,
):
    """Chord callback — receives parsed dicts from N actor deliberation children
    children and resumes the §11.1 apply phase.

    The canonical runtime applies tick results inside the main tick runner; this
    callback keeps split-task deployments observable without mutating state.
    """
    logger.info(
        "apply_tick_results",
        run_id=run_id,
        universe_id=universe_id,
        tick=tick,
        results=len(results),
    )
    return {
        "run_id": run_id,
        "universe_id": universe_id,
        "tick": tick,
        "result_count": len(results),
    }


# ---------------------------------------------------------------------------
# actor_deliberation_call
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="actor_deliberation_call",
    queue="p1",
    acks_late=True,
    max_retries=3,
)
def actor_deliberation_call_task(self, envelope_json: str):  # type: ignore[no-untyped-def]
    """Run one cohort/hero deliberation through the provider policy.

    The envelope payload carries ``actor_id``, ``actor_kind``, and the
    pre-built ``prompt_packet`` JSON.  Returns the parsed decision dict
    so the chord callback can fan it back into the apply phase.
    """
    return _run_actor_deliberation_task(self, envelope_json)


def _run_actor_deliberation_task(task_self, envelope_json: str):  # type: ignore[no-untyped-def]
    env = JobEnvelope.model_validate_json(envelope_json)
    try:
        return asyncio.run(_run_tracked(env, _actor_deliberation_call_impl, mark_failed_on_error=False))
    except Exception as exc:  # noqa: BLE001
        asyncio.run(_mark_retry_or_failed_best_effort(task_self, env.job_id, exc))
        raise task_self.retry(exc=exc, countdown=5)  # noqa: B904


async def _actor_deliberation_call_impl(env: JobEnvelope) -> dict:
    from backend.app.providers import call_with_policy, ensure_providers_in_loop
    from backend.app.core.config import settings as _settings
    await ensure_providers_in_loop(_settings)
    from backend.app.schemas.llm import PromptPacket

    payload = env.payload or {}
    packet_dict = payload.get("prompt_packet") or {}
    actor_id = str(payload.get("actor_id") or "")
    actor_kind = str(payload.get("actor_kind") or "cohort")

    try:
        packet = PromptPacket.model_validate(packet_dict)
    except Exception as exc:
        raise ValueError(f"invalid prompt_packet in envelope: {exc}") from exc

    session_cm = await _open_session()
    async with session_cm as session:
        routing, limiter = await _build_routing_and_limiter(session)

        result = await call_with_policy(
            job_type="actor_deliberation_call",
            prompt=packet,
            routing=routing,
            limiter=limiter,
            ledger=None,
            run_id=env.run_id,
            universe_id=env.universe_id,
            tick=env.tick,
        )

    return {
        "actor_id": actor_id,
        "actor_kind": actor_kind,
        "parsed": dict(result.parsed_json or {}),
        "call_id": result.call_id,
    }


# ---------------------------------------------------------------------------
# Registered phase placeholders
# ---------------------------------------------------------------------------

async def _unsupported_phase_impl(env: JobEnvelope) -> dict:
    """Fail registered-but-unimplemented split-phase jobs as normal job errors."""
    raise NotImplementedError(
        f"{env.job_type} is not implemented as a standalone Celery task"
    )


def _run_unsupported_phase(envelope_json: str) -> dict:
    env = JobEnvelope.model_validate_json(envelope_json)
    try:
        return asyncio.run(_run_tracked(env, _unsupported_phase_impl))
    except Exception as exc:  # noqa: BLE001
        asyncio.run(_mark_failed_best_effort(env.job_id, exc))
        return {"status": "failed", "error": str(exc)}


@celery_app.task(bind=True, name="social_propagation", queue="p1")
def social_propagation_task(self, envelope_json: str):  # type: ignore[no-untyped-def]
    return _run_unsupported_phase(envelope_json)


@celery_app.task(bind=True, name="execute_due_events", queue="p1")
def execute_due_events_task(self, envelope_json: str):  # type: ignore[no-untyped-def]
    return _run_unsupported_phase(envelope_json)


@celery_app.task(bind=True, name="sociology_update", queue="p1")
def sociology_update_task(self, envelope_json: str):  # type: ignore[no-untyped-def]
    return _run_unsupported_phase(envelope_json)


@celery_app.task(bind=True, name="god_agent_review", queue="p1")
def god_agent_review_task(self, envelope_json: str):  # type: ignore[no-untyped-def]
    return _run_unsupported_phase(envelope_json)


@celery_app.task(bind=True, name="build_review_index", queue="p2")
def build_review_index_task(self, envelope_json: str):  # type: ignore[no-untyped-def]
    return _run_unsupported_phase(envelope_json)


# ---------------------------------------------------------------------------
# branch_universe
# ---------------------------------------------------------------------------


@celery_app.task(
    bind=True,
    name="branch_universe",
    queue="p0",
    acks_late=True,
    max_retries=3,
)
def branch_universe_task(self, envelope_json: str):  # type: ignore[no-untyped-def]
    """Commit a child universe via :func:`commit_branch`."""
    env = JobEnvelope.model_validate_json(envelope_json)
    try:
        return asyncio.run(_run_tracked(env, _branch_universe_impl, mark_failed_on_error=False))
    except Exception as exc:  # noqa: BLE001
        asyncio.run(_mark_retry_or_failed_best_effort(self, env.job_id, exc))
        raise self.retry(exc=exc, countdown=15)  # noqa: B904


async def _branch_universe_impl(env: JobEnvelope) -> dict:
    from backend.app.providers import ensure_providers_in_loop
    from backend.app.core.config import settings as _settings
    await ensure_providers_in_loop(_settings)
    from backend.app.branching.branch_universe_task import run_branch_universe

    payload = env.payload or {}
    parent_universe_id = str(
        payload.get("parent_universe_id") or env.universe_id or ""
    )
    branch_from_tick = int(payload.get("branch_from_tick", env.tick or 0))
    delta_payload = payload.get("delta") or {}
    reason = str(payload.get("reason") or "auto-branch")
    policy_decision = str(payload.get("policy_decision") or "approve")

    if not delta_payload:
        return {
            "status": "skipped",
            "reason": "no delta provided in envelope",
        }

    session_cm = await _open_session()
    async with session_cm as session:
        ledger = _open_ledger(env.run_id)
        result = await run_branch_universe(
            session=session,
            parent_universe_id=parent_universe_id,
            branch_from_tick=branch_from_tick,
            delta_payload=delta_payload,
            reason=reason,
            policy_decision=policy_decision,
            ledger=ledger,
            enqueue_first_tick=False,
        )
        await session.commit()
        if result.status == "active":
            from backend.app.branching.branch_engine import enqueue_first_child_tick

            result.enqueued = await enqueue_first_child_tick(
                run_id=env.run_id,
                child_universe_id=result.child_universe_id,
                tick=result.branch_from_tick + 1,
            )

    return {
        "child_universe_id": result.child_universe_id,
        "parent_universe_id": result.parent_universe_id,
        "branch_from_tick": result.branch_from_tick,
        "status": result.status,
        "enqueued": result.enqueued,
    }


# ---------------------------------------------------------------------------
# sync_zep_memory (P2)
# ---------------------------------------------------------------------------


@celery_app.task(bind=True, name="sync_zep_memory", queue="p2")
def sync_zep_memory_task(self, envelope_json: str):  # type: ignore[no-untyped-def]
    """Sync recent memory writes to Zep (best-effort)."""
    env = JobEnvelope.model_validate_json(envelope_json)
    try:
        return asyncio.run(_run_tracked(env, _sync_zep_memory_impl))
    except Exception as exc:  # noqa: BLE001
        asyncio.run(_mark_failed_best_effort(env.job_id, exc))
        _log.warning("sync_zep_memory failed: %s", exc)
        return {"status": "failed", "error": str(exc)}


async def _sync_zep_memory_impl(env: JobEnvelope) -> dict:
    from backend.app.memory.factory import get_memory

    payload = env.payload or {}
    actor_id = str(payload.get("actor_id") or "")
    universe_id = str(payload.get("universe_id") or env.universe_id or "")
    tick = int(payload.get("tick") or env.tick or 0)
    summary_text = str(payload.get("summary_text") or f"Tick {tick} sync.")

    try:
        memory = get_memory()
    except Exception as exc:
        return {"status": "no_memory_provider", "error": str(exc)}

    try:
        await memory.end_of_tick_summary(
            actor_id=actor_id,
            universe_id=universe_id,
            tick=tick,
            summary_text=summary_text,
        )
    except Exception as exc:
        return {"status": "memory_failure", "error": str(exc)}

    return {"status": "synced", "actor_id": actor_id, "tick": tick}


# ---------------------------------------------------------------------------
# aggregate_run_results (P2)
# ---------------------------------------------------------------------------


@celery_app.task(bind=True, name="aggregate_run_results", queue="p2")
def aggregate_run_results_task(self, envelope_json: str):  # type: ignore[no-untyped-def]
    """Synthesize the terminal results dashboard for one run."""
    env = JobEnvelope.model_validate_json(envelope_json)
    try:
        return asyncio.run(_run_tracked(env, _aggregate_run_results_impl))
    except Exception as exc:  # noqa: BLE001
        asyncio.run(_mark_failed_best_effort(env.job_id, exc))
        return {"status": "failed", "error": str(exc)}


async def _aggregate_run_results_impl(env: JobEnvelope) -> dict:
    from backend.app.providers import ensure_providers_in_loop
    from backend.app.core.config import settings as _settings
    await ensure_providers_in_loop(_settings)
    from backend.app.models.results import RunResultModel
    from backend.app.models.runs import BigBangRunModel
    from backend.app.results.aggregator import aggregate_run_results, ledger_for_run_folder

    session_cm = await _open_session()
    async with session_cm as session:
        run = await session.get(BigBangRunModel, env.run_id)
        if run is None:
            return {"status": "failed", "error": f"run {env.run_id!r} not found"}
        result_row = await session.get(RunResultModel, env.run_id)
        if result_row is not None:
            result_row.job_id = env.job_id
            result_row.status = "running"
            result_row.error = None
            await session.commit()

        routing, limiter = await _build_routing_and_limiter(session)
        ledger = ledger_for_run_folder(run.run_folder_path, env.run_id)
        result = await aggregate_run_results(
            session=session,
            run_id=env.run_id,
            routing=routing,
            limiter=limiter,
            ledger=ledger,
        )
        result_row = await session.get(RunResultModel, env.run_id)
        if result_row is not None:
            result_row.job_id = env.job_id
            await session.commit()
        return result


# ---------------------------------------------------------------------------
# force_deviation (P0)
# ---------------------------------------------------------------------------


@celery_app.task(bind=True, name="force_deviation", queue="p0")
def force_deviation_task(self, envelope_json: str):  # type: ignore[no-untyped-def]
    """Queued forced-deviation task.

    The API commits forced deviations inline so users get the child id
    immediately. Deliberately enqueued force-deviation jobs return the payload
    and let the API remain the single state-mutating path.
    """
    env = JobEnvelope.model_validate_json(envelope_json)
    try:
        return asyncio.run(_run_tracked(env, _force_deviation_impl))
    except Exception as exc:  # noqa: BLE001
        asyncio.run(_mark_failed_best_effort(env.job_id, exc))
        return {"status": "failed", "error": str(exc)}


async def _force_deviation_impl(env: JobEnvelope) -> dict:
    return {"status": "no_op_inline_api_owns_commit", "payload": env.payload}


# ---------------------------------------------------------------------------
# export_run (P3)
# ---------------------------------------------------------------------------


@celery_app.task(bind=True, name="export_run", queue="p3")
def export_run_task(self, envelope_json: str):  # type: ignore[no-untyped-def]
    """Export the run folder as a verifiable zip.

    Best-effort — failures are not retried (the user can re-trigger).
    """
    env = JobEnvelope.model_validate_json(envelope_json)
    try:
        return asyncio.run(_run_tracked(env, _export_run_impl))
    except Exception as exc:  # noqa: BLE001
        asyncio.run(_mark_failed_best_effort(env.job_id, exc))
        return {"status": "failed", "error": str(exc)}


async def _mark_failed_best_effort(job_id: str, exc: Exception) -> None:
    from backend.app.workers import scheduler

    await scheduler.mark_failed(job_id, str(exc))


async def _mark_retry_or_failed_best_effort(task, job_id: str, exc: Exception) -> None:
    from backend.app.workers import scheduler

    retries = int(getattr(getattr(task, "request", None), "retries", 0) or 0)
    max_retries = getattr(task, "max_retries", 0)
    if max_retries is None or retries < int(max_retries):
        await scheduler._patch_job(job_id, status="retried", error=str(exc)[:4000], finished_at=None)
        return
    await scheduler.mark_failed(job_id, str(exc))


async def _export_run_impl(env: JobEnvelope) -> dict:
    from pathlib import Path

    payload = env.payload or {}
    output_path = payload.get("output_path")

    try:
        from backend.app.models.runs import BigBangRunModel
        from backend.app.storage.export import export_run_to_zip
    except Exception as exc:
        return {"status": "no_export_module", "error": str(exc)}

    try:
        session_cm = await _open_session()
        async with session_cm as session:
            run = await session.get(BigBangRunModel, env.run_id)
            if run is None:
                return {"status": "failed", "error": f"run {env.run_id!r} not found"}
            if not run.run_folder_path:
                return {"status": "failed", "error": "run has no ledger folder yet"}
            run_folder = Path(run.run_folder_path)

        dest = Path(output_path) if output_path else run_folder / "exports" / f"{env.run_id}.zip"
        result = export_run_to_zip(run_folder=run_folder, dest=dest, verify=True)
    except Exception as exc:
        return {"status": "failed", "error": str(exc)}

    return {"status": "exported", "result": str(result)}


__all__ = [
    "heartbeat",
    "echo_envelope",
    "run_canonical_job_task",
    "initialize_big_bang_task",
    "simulate_universe_tick_task",
    "apply_tick_results_task",
    "actor_deliberation_call_task",
    "social_propagation_task",
    "execute_due_events_task",
    "sociology_update_task",
    "god_agent_review_task",
    "build_review_index_task",
    "branch_universe_task",
    "sync_zep_memory_task",
    "aggregate_run_results_task",
    "force_deviation_task",
    "export_run_task",
]
