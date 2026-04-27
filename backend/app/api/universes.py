"""Universes REST API — §20.2.

Endpoints:
  GET   /api/universes/{universe_id}
  POST  /api/universes/{universe_id}/pause
  POST  /api/universes/{universe_id}/resume
  POST  /api/universes/{universe_id}/step
  POST  /api/universes/{universe_id}/branch-preview
  POST  /api/universes/{universe_id}/branch
  GET   /api/universes/{universe_id}/ticks/{tick}
  GET   /api/universes/{universe_id}/lineage
  GET   /api/universes/{universe_id}/descendants
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.clock import now_utc
from backend.app.core.db import get_session
from backend.app.models.branches import BranchNodeModel
from backend.app.models.cohorts import CohortStateModel, PopulationArchetypeModel
from backend.app.models.runs import BigBangRunModel
from backend.app.models.universes import UniverseModel
from backend.app.schemas.api import (
    BranchPreviewRequest,
    BranchPreviewResponse,
    BranchRequest,
    BranchResponse,
    DescendantsResponse,
    ForceDeviationRequest,
    ForceDeviationResponse,
    LineageResponse,
    StepRequest,
    TickArtifactResponse,
    TickTraceActor,
    TickTraceResponse,
    UniverseDetail,
)
from backend.app.workers.scheduler import enqueue, make_envelope

router = APIRouter(prefix="/api/universes", tags=["universes"])

DbSession = Annotated[AsyncSession, Depends(get_session)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_universe_or_404(universe_id: str, session: AsyncSession) -> UniverseModel:
    result = await session.execute(
        select(UniverseModel).where(UniverseModel.universe_id == universe_id)
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Universe {universe_id!r} not found.")
    return row


def _universe_to_detail(uni: UniverseModel, active_cohort_count: int = 0) -> UniverseDetail:
    return UniverseDetail(
        universe_id=uni.universe_id,
        big_bang_id=uni.big_bang_id,
        parent_universe_id=uni.parent_universe_id,
        child_universe_ids=[],  # populated separately
        branch_from_tick=uni.branch_from_tick or 0,
        branch_depth=uni.branch_depth,
        lineage_path=list(uni.lineage_path or []),
        status=uni.status,
        branch_reason=uni.branch_reason or "",
        current_tick=uni.current_tick,
        latest_metrics=dict(uni.latest_metrics or {}),
        created_at=uni.created_at,
        frozen_at=uni.frozen_at,
        killed_at=uni.killed_at,
        completed_at=uni.completed_at,
        active_cohort_count=active_cohort_count,
        branch_summary={},
    )


async def _get_run_folder(universe: UniverseModel, session: AsyncSession) -> Path | None:
    run_result = await session.execute(
        select(BigBangRunModel).where(BigBangRunModel.big_bang_id == universe.big_bang_id)
    )
    run = run_result.scalar_one_or_none()
    if run and run.run_folder_path:
        p = Path(run.run_folder_path)
        if p.exists():
            return p
    return None


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except Exception:
        return None


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


_SECRET_PATTERNS = (
    re.compile(r"sk-" r"or-v1-[A-Za-z0-9_-]+"),
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{16,}"),
)
_SENSITIVE_KEYS = {"authorization", "api_key", "apikey", "token", "secret", "password"}


def _redact_string(value: str) -> str:
    redacted = value
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(lambda m: f"{m.group(1)}[REDACTED]" if m.lastindex else "[REDACTED]", redacted)
    return redacted


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                out[str(key)] = "[REDACTED]"
            else:
                out[str(key)] = _redact(item)
        return out
    return value


def _actor_state_slice(state_payload: dict, actor_id: str, actor_kind: str) -> dict | None:
    key = "cohorts" if actor_kind == "cohort" else "heroes"
    rows = state_payload.get(key) if isinstance(state_payload, dict) else None
    if not isinstance(rows, list):
        return None
    id_key = "cohort_id" if actor_kind == "cohort" else "hero_id"
    for row in rows:
        if isinstance(row, dict) and row.get(id_key) == actor_id:
            return row
    return None


def _state_delta(before: dict | None, after: dict | None) -> dict:
    if not before or not after:
        return {}
    delta: dict[str, Any] = {}
    for key, after_value in after.items():
        before_value = before.get(key)
        if before_value != after_value:
            delta[key] = {"before": before_value, "after": after_value}
    return delta


def _load_parsed_decisions(tick_dir: Path) -> list[dict]:
    parsed_json = _read_json(tick_dir / "parsed_decisions.json")
    if isinstance(parsed_json, dict) and isinstance(parsed_json.get("decisions"), list):
        return [item for item in parsed_json["decisions"] if isinstance(item, dict)]
    if isinstance(parsed_json, list):
        return [item for item in parsed_json if isinstance(item, dict)]
    return _read_jsonl(tick_dir / "parsed_decisions.jsonl")


def _normalise_tool_call(raw: dict, index: int) -> dict:
    function_payload = raw.get("function") if isinstance(raw.get("function"), dict) else {}
    name = (
        raw.get("name")
        or raw.get("tool_name")
        or function_payload.get("name")
        or raw.get("type")
        or "tool_call"
    )
    args: Any = raw.get("args")
    if args is None:
        args = raw.get("arguments")
    if args is None:
        args = raw.get("input")
    if args is None:
        args = function_payload.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"value": args}
    if not isinstance(args, dict):
        args = {"value": args}
    status = raw.get("status") if raw.get("status") in {"success", "error", "skipped"} else "success"
    return {
        "id": str(raw.get("id") or f"tool-{index}"),
        "name": str(name),
        "status": status,
        "args": args,
    }


def _load_llm_artifacts(tick_dir: Path) -> list[dict]:
    llm_dir = tick_dir / "llm_calls"
    if not llm_dir.exists():
        return []
    artifacts: list[dict] = []
    for path in sorted(llm_dir.glob("*.json")):
        item = _read_json(path)
        if isinstance(item, dict):
            artifacts.append(item)
    return artifacts


def _load_tool_calls(tick_dir: Path, llm_artifacts: list[dict]) -> list[dict]:
    raw_calls: list[dict] = []
    tool_file = _read_json(tick_dir / "tool_calls.json")
    if isinstance(tool_file, dict) and isinstance(tool_file.get("tool_calls"), list):
        raw_calls.extend(item for item in tool_file["tool_calls"] if isinstance(item, dict))
    elif isinstance(tool_file, list):
        raw_calls.extend(item for item in tool_file if isinstance(item, dict))

    for artifact in llm_artifacts:
        result = artifact.get("result") if isinstance(artifact.get("result"), dict) else {}
        calls = result.get("tool_calls") if isinstance(result, dict) else None
        if isinstance(calls, list):
            raw_calls.extend(item for item in calls if isinstance(item, dict))
    return [_normalise_tool_call(call, index) for index, call in enumerate(raw_calls)]


def _resolve_tick_dir(run_folder: Path, universe: UniverseModel, tick: int) -> Path | None:
    """Return this universe's tick directory, falling back to inherited parents."""
    own = run_folder / "universes" / universe.universe_id / "ticks" / f"tick_{tick:03d}"
    if own.exists():
        return own

    lineage = list(universe.lineage_path or [])
    if universe.universe_id not in lineage:
        lineage.append(universe.universe_id)
    for ancestor_id in reversed(lineage[:-1]):
        candidate = run_folder / "universes" / ancestor_id / "ticks" / f"tick_{tick:03d}"
        if candidate.exists():
            return candidate
    return None


def _prompt_summary(
    *,
    universe_id: str,
    tick: int,
    llm_artifacts: list[dict],
    tool_call_count: int,
) -> dict:
    if not llm_artifacts:
        return {
            "promptHash": "none",
            "model": "n/a",
            "cost": 0.0,
            "tokens": {"prompt": 0, "completion": 0},
            "toolCalls": tool_call_count,
            "provider": "local",
            "traceId": f"{universe_id}:t{tick}",
        }

    prompt_hashes: list[str] = []
    prompt_tokens = 0
    completion_tokens = 0
    cost = 0.0
    provider = "unknown"
    model = "unknown"
    trace_id = ""
    for artifact in llm_artifacts:
        result = artifact.get("result") if isinstance(artifact.get("result"), dict) else {}
        prompt_payload = artifact.get("prompt") if isinstance(artifact.get("prompt"), dict) else {}
        if prompt_payload:
            prompt_hashes.append(
                hashlib.sha256(
                    json.dumps(prompt_payload, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest()
            )
        provider = str(result.get("provider") or artifact.get("provider") or provider)
        model = str(result.get("model_used") or artifact.get("model_used") or model)
        trace_id = str(result.get("call_id") or artifact.get("call_id") or trace_id)
        prompt_tokens += int(result.get("prompt_tokens") or 0)
        completion_tokens += int(result.get("completion_tokens") or 0)
        cost += float(result.get("cost_usd") or 0.0)

    combined_hash = hashlib.sha256("".join(prompt_hashes).encode("utf-8")).hexdigest() if prompt_hashes else "none"
    return {
        "promptHash": combined_hash,
        "model": model,
        "cost": cost,
        "tokens": {"prompt": prompt_tokens, "completion": completion_tokens},
        "toolCalls": tool_call_count,
        "provider": provider,
        "traceId": trace_id or f"{universe_id}:t{tick}",
    }


def _emotion_trends_from_state(state_after: dict, tick: int) -> list[dict[str, float]]:
    cohorts = state_after.get("cohorts") if isinstance(state_after, dict) else None
    if not isinstance(cohorts, list):
        return [{"tick": float(tick)}]

    totals: dict[str, float] = {}
    total_weight = 0.0
    for cohort in cohorts:
        if not isinstance(cohort, dict):
            continue
        emotions = cohort.get("emotions")
        if not isinstance(emotions, dict):
            continue
        weight = float(cohort.get("represented_population") or 1.0)
        total_weight += weight
        for key, value in emotions.items():
            if isinstance(value, (int, float)) and math.isfinite(float(value)):
                totals[str(key).lower()] = totals.get(str(key).lower(), 0.0) + float(value) * weight
    row: dict[str, float] = {"tick": float(tick)}
    if total_weight > 0:
        row.update({key: value / total_weight for key, value in totals.items()})
    return [row]


async def _branch_policy_for_universe(
    *,
    universe: UniverseModel,
    session: AsyncSession,
    delta_payload: dict,
    reason: str,
):
    """Validate a branch delta and evaluate the current DB-backed branch policy."""
    from backend.app.branching.branch_policy import MultiverseSnapshot, evaluate_branch_policy
    from backend.app.models.settings import BranchPolicySettingModel
    from backend.app.schemas.branching import BranchDelta, BranchPolicy
    from backend.app.schemas.llm import GodReviewOutput

    try:
        delta = TypeAdapter(BranchDelta).validate_python(delta_payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    policy_row = await session.get(BranchPolicySettingModel, "default")
    if policy_row is None:
        policy = BranchPolicy(
            max_active_universes=50,
            max_total_branches=500,
            max_depth=8,
            max_branches_per_tick=5,
            branch_cooldown_ticks=3,
            min_divergence_score=0.35,
            auto_prune_low_value=True,
        )
    else:
        policy = BranchPolicy(
            max_active_universes=policy_row.max_active_universes,
            max_total_branches=policy_row.max_total_branches,
            max_depth=policy_row.max_depth,
            max_branches_per_tick=policy_row.max_branches_per_tick,
            branch_cooldown_ticks=policy_row.branch_cooldown_ticks,
            min_divergence_score=policy_row.min_divergence_score,
            auto_prune_low_value=policy_row.auto_prune_low_value,
        )

    active_count = (
        await session.execute(
            select(func.count(UniverseModel.universe_id)).where(
                UniverseModel.big_bang_id == universe.big_bang_id,
                UniverseModel.status == "active",
            )
        )
    ).scalar_one()
    total_branches = (
        await session.execute(
            select(func.count(UniverseModel.universe_id)).where(
                UniverseModel.big_bang_id == universe.big_bang_id,
                UniverseModel.parent_universe_id.is_not(None),
            )
        )
    ).scalar_one()
    max_depth = (
        await session.execute(
            select(func.max(UniverseModel.branch_depth)).where(
                UniverseModel.big_bang_id == universe.big_bang_id
            )
        )
    ).scalar_one() or 0
    branches_this_tick = (
        await session.execute(
            select(func.count(UniverseModel.universe_id)).where(
                UniverseModel.big_bang_id == universe.big_bang_id,
                UniverseModel.branch_from_tick == universe.current_tick,
                UniverseModel.parent_universe_id.is_not(None),
            )
        )
    ).scalar_one()

    branch_rows = (
        await session.execute(
            select(BranchNodeModel.parent_universe_id, BranchNodeModel.branch_tick)
            .join(UniverseModel, BranchNodeModel.universe_id == UniverseModel.universe_id)
            .where(UniverseModel.big_bang_id == universe.big_bang_id)
        )
    ).all()
    last_branch_tick: dict[str, int] = {}
    for parent_id, branch_tick in branch_rows:
        if parent_id:
            last_branch_tick[parent_id] = max(
                last_branch_tick.get(parent_id, -1), int(branch_tick)
            )

    god_decision = GodReviewOutput(
        decision="spawn_active",
        branch_delta=delta,
        tick_summary=reason or "Manual branch request",
        rationale={"source": "api"},
    )
    snapshot = MultiverseSnapshot(
        big_bang_id=universe.big_bang_id,
        active_universe_count=int(active_count),
        total_branch_count=int(total_branches),
        max_depth_reached=int(max_depth),
        branches_this_tick=int(branches_this_tick),
        last_branch_tick_per_universe=last_branch_tick,
        parent_metrics_history={universe.universe_id: [dict(universe.latest_metrics or {})]},
    )
    return delta, evaluate_branch_policy(
        parent_universe_id=universe.universe_id,
        parent_current_tick=universe.current_tick,
        proposed_decision=god_decision,
        multiverse=snapshot,
        policy=policy,
    )


# ---------------------------------------------------------------------------
# GET /api/universes/{universe_id}
# ---------------------------------------------------------------------------


@router.get("/{universe_id}", response_model=UniverseDetail)
async def get_universe(universe_id: str, session: DbSession) -> UniverseDetail:
    uni = await _get_universe_or_404(universe_id, session)

    # Count active cohorts from the latest persisted state at or before the
    # universe cursor. Sparse ledgers may not write every tick.
    latest_tick = (
        await session.execute(
            select(func.max(CohortStateModel.tick)).where(
                CohortStateModel.universe_id == universe_id,
                CohortStateModel.tick <= uni.current_tick,
            )
        )
    ).scalar_one()
    cohort_count = 0
    if latest_tick is not None:
        cohort_count = (
            await session.execute(
                select(func.count(CohortStateModel.cohort_id)).where(
                    CohortStateModel.universe_id == universe_id,
                    CohortStateModel.tick == latest_tick,
                    CohortStateModel.is_active == True,  # noqa: E712
                )
            )
        ).scalar_one()

    # Build child universe id list.
    children_result = await session.execute(
        select(UniverseModel.universe_id).where(
            UniverseModel.parent_universe_id == universe_id
        )
    )
    child_ids = [r[0] for r in children_result.all()]

    detail = _universe_to_detail(uni, active_cohort_count=cohort_count)
    detail = detail.model_copy(update={"child_universe_ids": child_ids})
    return detail


# ---------------------------------------------------------------------------
# POST /api/universes/{universe_id}/pause
# ---------------------------------------------------------------------------


@router.post("/{universe_id}/pause", status_code=200)
async def pause_universe(universe_id: str, session: DbSession) -> dict:
    """Flip universe status to 'frozen' and record frozen_at timestamp."""
    uni = await _get_universe_or_404(universe_id, session)

    if uni.status not in ("active", "candidate"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot pause a universe in status '{uni.status}'.",
        )

    uni.status = "frozen"
    uni.frozen_at = now_utc()
    from backend.app.simulation.lifecycle import prepare_results_job_if_run_terminal

    results_envelope = await prepare_results_job_if_run_terminal(
        session=session,
        run_id=uni.big_bang_id,
        reason="operator_freeze_all_terminal",
    )
    await session.commit()
    if results_envelope is not None:
        try:
            await enqueue(results_envelope)
        except Exception:
            pass
    return {"universe_id": universe_id, "status": "frozen"}


# ---------------------------------------------------------------------------
# POST /api/universes/{universe_id}/resume
# ---------------------------------------------------------------------------


@router.post("/{universe_id}/resume", status_code=200)
async def resume_universe(universe_id: str, session: DbSession) -> dict:
    """Flip universe status back to 'active' and enqueue the next tick."""
    uni = await _get_universe_or_404(universe_id, session)

    if uni.status != "frozen":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot resume a universe in status '{uni.status}'.",
        )

    uni.status = "active"
    uni.frozen_at = None
    await session.commit()

    # Enqueue next tick.
    envelope = make_envelope(
        job_type="simulate_universe_tick",
        run_id=uni.big_bang_id,
        universe_id=universe_id,
        tick=uni.current_tick + 1,
        payload={"universe_id": universe_id, "tick": uni.current_tick + 1},
    )
    try:
        await enqueue(envelope)
    except Exception:
        pass

    return {"universe_id": universe_id, "status": "active", "next_tick": uni.current_tick + 1}


@router.post("/{universe_id}/kill", status_code=200)
async def kill_universe(universe_id: str, session: DbSession) -> dict:
    """Mark a universe as killed."""
    uni = await _get_universe_or_404(universe_id, session)
    if uni.status == "killed":
        return {"universe_id": universe_id, "status": "killed"}
    if uni.status == "completed":
        raise HTTPException(status_code=409, detail="Cannot kill a completed universe.")
    uni.status = "killed"
    uni.killed_at = now_utc()
    from backend.app.simulation.lifecycle import prepare_results_job_if_run_terminal

    results_envelope = await prepare_results_job_if_run_terminal(
        session=session,
        run_id=uni.big_bang_id,
        reason="operator_kill_all_terminal",
    )
    await session.commit()
    if results_envelope is not None:
        try:
            await enqueue(results_envelope)
        except Exception:
            pass
    return {"universe_id": universe_id, "status": "killed"}


@router.post("/{universe_id}/replay", status_code=202)
async def replay_universe(universe_id: str, body: StepRequest, session: DbSession) -> dict:
    """Fork a replay branch from the requested historical tick and queue its first tick."""
    uni = await _get_universe_or_404(universe_id, session)
    replay_from_tick = body.tick if body.tick is not None else max(0, uni.current_tick)
    if replay_from_tick > uni.current_tick:
        raise HTTPException(
            status_code=422,
            detail=f"Replay tick {replay_from_tick} is ahead of current_tick {uni.current_tick}.",
        )

    from backend.app.branching.branch_engine import commit_branch, enqueue_first_child_tick
    from backend.app.schemas.branching import BranchDelta, BranchPolicyResult
    from backend.app.workers import scheduler

    delta = TypeAdapter(BranchDelta).validate_python(
        {
            "type": "parameter_shift",
            "target": "replay_from_tick",
            "delta": {"tick": float(replay_from_tick)},
        }
    )
    policy_result = BranchPolicyResult(
        decision="approve",
        reason="Replay branch requested by operator.",
        cost_estimate={"source": "api_replay"},
        divergence_score=0.0,
    )

    ledger = None
    run_folder = await _get_run_folder(uni, session)
    if run_folder is not None:
        try:
            from backend.app.storage.ledger import Ledger

            ledger = Ledger.open(run_folder.parent.parent, uni.big_bang_id)
        except Exception:
            ledger = None

    job_envelope = scheduler.make_envelope(
        job_type="branch_universe",
        run_id=uni.big_bang_id,
        universe_id=universe_id,
        tick=replay_from_tick,
        idempotency_key=f"branch_universe:{uni.big_bang_id}:{universe_id}:t{replay_from_tick}:replay:{uuid.uuid4().hex}",
        payload={
            "parent_universe_id": universe_id,
            "branch_from_tick": replay_from_tick,
            "replay_from_tick": replay_from_tick,
            "delta": delta.model_dump(mode="json"),
            "reason": f"Replay from tick {replay_from_tick}",
            "policy_decision": "approve",
            "inline": True,
        },
    )
    result = await commit_branch(
        session=session,
        parent_universe=uni,
        branch_from_tick=replay_from_tick,
        delta=delta,
        branch_reason=f"Replay from tick {replay_from_tick}",
        policy_result=policy_result,
        ledger=ledger,
        enqueue_first_tick=False,
    )
    await session.commit()
    await scheduler.mark_enqueued(job_envelope)
    await scheduler.mark_started(job_envelope.job_id)
    enqueued = await enqueue_first_child_tick(
        run_id=uni.big_bang_id,
        child_universe_id=result.child_universe_id,
        tick=replay_from_tick + 1,
    )
    await scheduler.mark_succeeded(
        job_envelope.job_id,
        result_summary={
            "source_universe_id": universe_id,
            "child_universe_id": result.child_universe_id,
            "replay_from_tick": replay_from_tick,
            "first_tick_enqueued": enqueued,
        },
    )
    return {
        "job_id": job_envelope.job_id,
        "universe_id": result.child_universe_id,
        "source_universe_id": universe_id,
        "replay_from_tick": replay_from_tick,
        "enqueued": enqueued,
    }


# ---------------------------------------------------------------------------
# POST /api/universes/{universe_id}/step
# ---------------------------------------------------------------------------


@router.post("/{universe_id}/step", status_code=202)
async def step_universe(
    universe_id: str, body: StepRequest, session: DbSession
) -> dict:
    """Enqueue exactly one ``simulate_universe_tick`` job.

    Falls back to running the tick inline via
    :func:`backend.app.simulation.local_runner.run_tick_locally` when the
    Celery broker is unreachable — this keeps the dev workflow working
    without a worker container.
    """
    uni = await _get_universe_or_404(universe_id, session)
    target_tick = body.tick if body.tick is not None else uni.current_tick + 1
    run = await session.get(BigBangRunModel, uni.big_bang_id)
    max_ticks = int(getattr(run, "max_ticks", 0) or 0) if run else 0
    if max_ticks and target_tick > max_ticks:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Run max_ticks reached; no further tick can be queued.",
                "target_tick": target_tick,
                "max_ticks": max_ticks,
            },
        )

    envelope = make_envelope(
        job_type="simulate_universe_tick",
        run_id=uni.big_bang_id,
        universe_id=universe_id,
        tick=target_tick,
        payload={"universe_id": universe_id, "tick": target_tick},
    )
    job_id = envelope.job_id

    enqueued_via_celery = False
    try:
        await enqueue(envelope)
        enqueued_via_celery = True
    except Exception:
        # Broker unreachable — fall through to the local runner.
        enqueued_via_celery = False

    local_summary: dict | None = None
    if not enqueued_via_celery:
        from backend.app.workers import scheduler

        await scheduler.mark_started(job_id)
        try:
            local_summary = await _run_tick_locally_for_step(
                run_id=uni.big_bang_id,
                universe_id=universe_id,
                tick=target_tick,
                session=session,
            )
            await scheduler.mark_succeeded(job_id, result_summary=local_summary)
        except Exception:  # pragma: no cover — best effort
            local_summary = None
            await scheduler.mark_failed(job_id, "local step fallback failed")

    return {
        "job_id": job_id,
        "universe_id": universe_id,
        "tick": target_tick,
        "enqueued_via_celery": enqueued_via_celery,
        "local_summary": local_summary,
    }


@router.get("/{universe_id}/network")
async def get_universe_network(
    universe_id: str,
    session: DbSession,
    layer: str = "exposure",
    tick: int | None = None,
) -> dict:
    """Return a lightweight multiplex network dataset for CLI/API consumers."""
    uni = await _get_universe_or_404(universe_id, session)
    target_tick = uni.current_tick if tick is None else min(max(tick, 0), uni.current_tick)
    latest_ticks = (
        select(
            CohortStateModel.cohort_id.label("cohort_id"),
            func.max(CohortStateModel.tick).label("tick"),
        )
        .where(
            CohortStateModel.universe_id == universe_id,
            CohortStateModel.tick <= target_tick,
            CohortStateModel.is_active == True,  # noqa: E712
        )
        .group_by(CohortStateModel.cohort_id)
        .subquery()
    )
    result = await session.execute(
        select(CohortStateModel, PopulationArchetypeModel)
        .join(
            latest_ticks,
            (CohortStateModel.cohort_id == latest_ticks.c.cohort_id)
            & (CohortStateModel.tick == latest_ticks.c.tick),
        )
        .join(
            PopulationArchetypeModel,
            CohortStateModel.archetype_id == PopulationArchetypeModel.archetype_id,
        )
        .where(
            CohortStateModel.universe_id == universe_id,
            CohortStateModel.is_active == True,  # noqa: E712
        )
    )
    rows = result.all()

    palette = ["#2563eb", "#059669", "#d97706", "#dc2626", "#7c3aed", "#0891b2"]
    archetypes: list[dict] = []
    archetype_seen: dict[str, str] = {}
    nodes: list[dict] = []
    for idx, (cohort, archetype) in enumerate(rows):
        color = archetype_seen.setdefault(
            archetype.archetype_id, palette[len(archetype_seen) % len(palette)]
        )
        if len(archetypes) < len(archetype_seen):
            archetypes.append(
                {
                    "key": archetype.archetype_id,
                    "label": archetype.label,
                    "color": color,
                }
            )
        angle = (idx / max(1, len(rows))) * 6.283185307
        radius = 160 + (idx % 5) * 45
        issue_stance = dict(cohort.issue_stance or {})
        ideology = dict(archetype.ideology_axes or {})
        nodes.append(
            {
                "id": cohort.cohort_id,
                "attrs": {
                    "label": archetype.label,
                    "archetype": archetype.archetype_id,
                    "representedPopulation": cohort.represented_population,
                    "analyticalDepth": float((archetype.behavior_axes or {}).get("analytical_depth", 0.5)),
                    "trust": float(cohort.visible_trust_summary.get("mean", 0.5) if cohort.visible_trust_summary else 0.5),
                    "expressionLevel": cohort.expression_level,
                    "mobilizationCapacity": float(archetype.mobilization_capacity or 0.5),
                    "cohortStance": float(next(iter(issue_stance.values()), 0.0)),
                    "x": radius * math.cos(angle),
                    "y": radius * math.sin(angle),
                    "size": max(5, min(24, cohort.represented_population ** 0.25)),
                    "color": color,
                    "issueStances": issue_stance,
                    "ideology": {
                        "economic": float(ideology.get("economic", 0.0)),
                        "social": float(ideology.get("social", 0.0)),
                        "institutional": float(ideology.get("institutional", 0.0)),
                        "cultural": float(ideology.get("cultural", 0.0)),
                        "international": float(ideology.get("international", 0.0)),
                    },
                    "recentPosts": [],
                },
            }
        )

    layers = ["exposure", "trust", "dependency", "mobilization", "identity"]
    edges: list[dict] = []
    for idx, source in enumerate(nodes):
        for offset, target in enumerate(nodes[idx + 1 : idx + 4], start=1):
            edge_layer = layers[(idx + offset) % len(layers)]
            weight = round(0.25 + ((idx + offset) % 7) / 10, 2)
            edges.append(
                {
                    "id": f"{edge_layer}-{source['id']}-{target['id']}",
                    "source": source["id"],
                    "target": target["id"],
                    "attrs": {
                        "layer": edge_layer,
                        "weight": weight,
                        "size": 0.4 + weight * 1.6,
                        "color": "#64748b88",
                    },
                }
            )

    if not nodes:
        raise HTTPException(status_code=404, detail="No active cohort network is available yet.")

    return {
        "nodes": nodes,
        "edges": edges,
        "archetypes": archetypes,
        "issueAxes": sorted({axis for node in nodes for axis in node["attrs"]["issueStances"].keys()}),
        "activeLayer": layer,
        "tick": target_tick,
    }


async def _run_tick_locally_for_step(
    *,
    run_id: str,
    universe_id: str,
    tick: int,
    session: AsyncSession,
) -> dict:
    """Helper used by ``/step`` when Celery is unavailable.

    Builds the supporting plumbing (routing table, rate limiter, ledger)
    and delegates to :func:`local_runner.run_tick_locally`.
    """
    from backend.app.core.config import settings as _cfg
    from backend.app.core.redis_client import get_redis_client
    from backend.app.providers.rate_limits import ProviderRateLimiter
    from backend.app.providers.routing import RoutingTable
    from backend.app.simulation.local_runner import run_tick_locally
    from backend.app.simulation.tick_runner import TickContext
    from backend.app.storage.ledger import Ledger

    try:
        routing = await RoutingTable.from_db(session)
    except Exception:
        routing = RoutingTable.defaults()

    redis = get_redis_client()
    limiter = ProviderRateLimiter(
        redis,
        provider="openrouter",
        rpm_limit=600,
        tpm_limit=1_000_000,
        max_concurrency=8,
        daily_budget_usd=None,
        jitter=False,
    )

    run_root = _cfg.run_root
    if run_root.name == "runs":
        run_root = run_root.parent
    try:
        ledger = Ledger.open(run_root, run_id)
    except Exception:
        return {"status": "no_ledger"}

    ctx = TickContext(
        run_id=run_id,
        universe_id=universe_id,
        tick=tick,
        attempt_number=1,
    )
    return await run_tick_locally(
        ctx,
        session=session,
        ledger=ledger,
        routing=routing,
        limiter=limiter,
        memory=None,
    )


# ---------------------------------------------------------------------------
# POST /api/universes/{universe_id}/branch-preview
# ---------------------------------------------------------------------------


@router.post("/{universe_id}/branch-preview", response_model=BranchPreviewResponse)
async def branch_preview(
    universe_id: str, body: BranchPreviewRequest, session: DbSession
) -> BranchPreviewResponse:
    """Check whether a branch delta would be approved by branch policy."""
    uni = await _get_universe_or_404(universe_id, session)
    _, policy_result = await _branch_policy_for_universe(
        universe=uni,
        session=session,
        delta_payload=body.delta,
        reason=body.reason,
    )
    return BranchPreviewResponse(
        approved=policy_result.decision != "reject",
        downgraded=policy_result.decision == "downgrade_to_candidate",
        rejection_reason=policy_result.reason if policy_result.decision == "reject" else None,
        policy_checks=[
            {
                "decision": policy_result.decision,
                "reason": policy_result.reason,
                "divergence_score": policy_result.divergence_score,
                "cost_estimate": policy_result.cost_estimate,
            }
        ],
        note="Branch policy evaluated against current multiverse state.",
    )


# ---------------------------------------------------------------------------
# POST /api/universes/{universe_id}/branch
# ---------------------------------------------------------------------------


@router.post("/{universe_id}/branch", status_code=202, response_model=BranchResponse)
async def branch_universe(
    universe_id: str, body: BranchRequest, session: DbSession
) -> BranchResponse:
    """Commit a child universe through the branch engine."""
    uni = await _get_universe_or_404(universe_id, session)
    delta, policy_result = await _branch_policy_for_universe(
        universe=uni,
        session=session,
        delta_payload=body.delta,
        reason=body.reason,
    )
    if policy_result.decision == "reject":
        raise HTTPException(status_code=409, detail=policy_result.reason)

    ledger = None
    run_folder = await _get_run_folder(uni, session)
    if run_folder is not None:
        try:
            from backend.app.storage.ledger import Ledger

            ledger = Ledger.open(run_folder.parent.parent, uni.big_bang_id)
        except Exception:
            ledger = None

    from backend.app.branching.branch_engine import commit_branch, enqueue_first_child_tick
    from backend.app.workers import scheduler

    job_envelope = scheduler.make_envelope(
        job_type="branch_universe",
        run_id=uni.big_bang_id,
        universe_id=universe_id,
        tick=uni.current_tick,
        idempotency_key=f"branch_universe:{uni.big_bang_id}:{universe_id}:t{uni.current_tick}:manual:{uuid.uuid4().hex}",
        payload={
            "parent_universe_id": universe_id,
            "branch_from_tick": uni.current_tick,
            "delta": delta.model_dump(mode="json"),
            "reason": body.reason or "Manual branch request",
            "policy_decision": policy_result.decision,
            "inline": True,
        },
    )

    result = await commit_branch(
        session=session,
        parent_universe=uni,
        branch_from_tick=uni.current_tick,
        delta=delta,
        branch_reason=body.reason or "Manual branch request",
        policy_result=policy_result,
        ledger=ledger,
        enqueue_first_tick=False,
    )
    await session.commit()
    await scheduler.mark_enqueued(job_envelope)
    await scheduler.mark_started(job_envelope.job_id)
    enqueued = False
    if result.status == "active":
        enqueued = await enqueue_first_child_tick(
            run_id=uni.big_bang_id,
            child_universe_id=result.child_universe_id,
            tick=uni.current_tick + 1,
        )
    await scheduler.mark_succeeded(
        job_envelope.job_id,
        result_summary={
            "child_universe_id": result.child_universe_id,
            "parent_universe_id": result.parent_universe_id,
            "branch_from_tick": result.branch_from_tick,
            "status": result.status,
            "first_tick_enqueued": enqueued,
        },
    )

    return BranchResponse(
        candidate_universe_id=result.child_universe_id,
        job_id=job_envelope.job_id,
        note=f"Branch committed with policy decision: {policy_result.decision}; first tick enqueued={enqueued}.",
    )


# ---------------------------------------------------------------------------
# GET /api/universes/{universe_id}/ticks/{tick}
# ---------------------------------------------------------------------------


@router.get("/{universe_id}/ticks/{tick}", response_model=TickArtifactResponse)
async def get_tick_artifact(
    universe_id: str, tick: int, session: DbSession
) -> TickArtifactResponse:
    """Read tick artifacts from the ledger file system."""
    uni = await _get_universe_or_404(universe_id, session)

    run_folder = await _get_run_folder(uni, session)

    parsed_decisions: list[dict] = []
    social_posts: list[dict] = []
    state_after: dict = {}
    god_decision: dict | None = None
    metrics: dict = {}
    prompt_summary: dict = _prompt_summary(
        universe_id=universe_id,
        tick=tick,
        llm_artifacts=[],
        tool_call_count=0,
    )
    tool_calls: list[dict] = []
    emotion_trends: list[dict[str, float]] = [{"tick": float(tick)}]

    if run_folder is not None:
        tick_dir = _resolve_tick_dir(run_folder, uni, tick)

        if tick_dir is None:
            raise HTTPException(
                status_code=404,
                detail=f"Tick {tick} artifacts not found for universe {universe_id!r}.",
            )

        parsed_decisions = _load_parsed_decisions(tick_dir)

        # Social posts (JSONL).
        posts_file = tick_dir / "social_posts" / "posts.jsonl"
        social_posts = _read_jsonl(posts_file)

        # State after (JSON).
        state_file = tick_dir / "universe_state_after.json"
        if not state_file.exists():
            state_file = tick_dir / "state_after.json"
        loaded_state = _read_json(state_file) if state_file.exists() else None
        if isinstance(loaded_state, dict):
            state_after = loaded_state

        # God decision.
        god_file = tick_dir / "god" / "decision.json"
        loaded_god_decision = _read_json(god_file) if god_file.exists() else None
        if isinstance(loaded_god_decision, dict):
            god_decision = loaded_god_decision

        # Metrics.
        metrics_file = tick_dir / "metrics.json"
        loaded_metrics = _read_json(metrics_file) if metrics_file.exists() else None
        if isinstance(loaded_metrics, dict):
            metrics = loaded_metrics

        llm_artifacts = _load_llm_artifacts(tick_dir)
        tool_calls = _load_tool_calls(tick_dir, llm_artifacts)
        prompt_summary = _prompt_summary(
            universe_id=universe_id,
            tick=tick,
            llm_artifacts=llm_artifacts,
            tool_call_count=len(tool_calls),
        )
        emotion_trends = _emotion_trends_from_state(state_after, tick)
    else:
        # No run folder yet (draft run) — return empty artifact.
        if tick > uni.current_tick:
            raise HTTPException(
                status_code=404,
                detail=f"Tick {tick} not yet available for universe {universe_id!r}.",
            )

    return TickArtifactResponse(
        universe_id=universe_id,
        tick=tick,
        parsed_decisions=parsed_decisions,
        social_posts=social_posts,
        state_after=state_after,
        god_decision=god_decision,
        metrics=metrics,
        prompt_summary=prompt_summary,
        tool_calls=tool_calls,
        emotion_trends=emotion_trends,
    )


@router.get("/{universe_id}/ticks/{tick}/trace", response_model=TickTraceResponse)
async def get_tick_trace(
    universe_id: str,
    tick: int,
    session: DbSession,
    include_raw: Annotated[bool, Query()] = False,
) -> TickTraceResponse:
    """Return full per-actor trace data for a universe tick."""
    uni = await _get_universe_or_404(universe_id, session)
    run_folder = await _get_run_folder(uni, session)
    if run_folder is None:
        raise HTTPException(status_code=404, detail="Run ledger is not available.")
    tick_dir = _resolve_tick_dir(run_folder, uni, tick)
    if tick_dir is None:
        raise HTTPException(status_code=404, detail=f"Tick {tick} trace not found.")

    missing: list[str] = []
    state_before = _read_json(tick_dir / "universe_state_before.json") or {}
    state_after = _read_json(tick_dir / "universe_state_after.json") or {}
    if not state_before:
        missing.append("universe_state_before")
    if not state_after:
        missing.append("universe_state_after")
    god_decision = _read_json(tick_dir / "god" / "decision.json")
    llm_artifacts = _load_llm_artifacts(tick_dir)
    parsed_decisions = _load_parsed_decisions(tick_dir)
    parsed_by_call: dict[str, dict] = {}
    for item in parsed_decisions:
        call_id = item.get("raw_call_id")
        if isinstance(call_id, str):
            parsed_by_call[call_id] = item

    actors: list[TickTraceActor] = []
    for artifact in llm_artifacts:
        prompt = artifact.get("prompt") if isinstance(artifact.get("prompt"), dict) else {}
        result = artifact.get("result") if isinstance(artifact.get("result"), dict) else {}
        call_id = str(artifact.get("call_id") or result.get("call_id") or "")
        actor_id = str(prompt.get("actor_id") or "unknown")
        actor_kind = str(prompt.get("actor_kind") or "cohort")
        parsed_json = result.get("parsed_json") if isinstance(result.get("parsed_json"), dict) else None
        parsed_decision = parsed_by_call.get(call_id)
        if parsed_decision and isinstance(parsed_decision.get("decision"), dict):
            parsed_json = parsed_decision["decision"]

        rationale = None
        self_ratings: dict[str, Any] = {}
        if isinstance(parsed_json, dict):
            rationale = parsed_json.get("decision_rationale") or parsed_json.get("rationale")
            if isinstance(parsed_json.get("self_ratings"), dict):
                self_ratings = dict(parsed_json["self_ratings"])

        before_actor = _actor_state_slice(state_before, actor_id, actor_kind)
        after_actor = _actor_state_slice(state_after, actor_id, actor_kind)
        raw_response: Any = result.get("raw_response") if include_raw else None
        actors.append(
            TickTraceActor(
                actor_id=actor_id,
                actor_kind=actor_kind,
                call_id=call_id or None,
                provider=str(result.get("provider") or artifact.get("provider") or "") or None,
                model_used=str(result.get("model_used") or artifact.get("model_used") or "") or None,
                job_type=str(result.get("job_type") or "") or None,
                prompt_packet=prompt if include_raw else {
                    "actor_id": actor_id,
                    "actor_kind": actor_kind,
                    "output_schema_id": prompt.get("output_schema_id"),
                    "metadata": prompt.get("metadata"),
                },
                visible_feed=list(prompt.get("visible_feed") or []),
                visible_events=list(prompt.get("visible_events") or []),
                retrieved_memory=prompt.get("retrieved_memory") if isinstance(prompt.get("retrieved_memory"), dict) else None,
                raw_response=raw_response,
                parsed_json=parsed_json,
                tool_calls=list(result.get("tool_calls") or []),
                rationale=rationale,
                self_ratings=self_ratings,
                state_before=before_actor,
                state_after=after_actor,
                state_delta=_state_delta(before_actor, after_actor),
            )
        )

    if god_decision:
        actors.append(
            TickTraceActor(
                actor_id=f"god:{universe_id}",
                actor_kind="god",
                parsed_json=god_decision if isinstance(god_decision, dict) else None,
                rationale=(god_decision or {}).get("rationale") if isinstance(god_decision, dict) else None,
                state_before=state_before,
                state_after=state_after,
                state_delta={},
            )
        )

    payload = TickTraceResponse(
        universe_id=universe_id,
        tick=tick,
        include_raw=include_raw,
        actors=actors,
        state_before=state_before,
        state_after=state_after,
        god_decision=god_decision if isinstance(god_decision, dict) else None,
        missing_artifacts=missing,
    )
    return TickTraceResponse.model_validate(_redact(payload.model_dump(mode="json")))


async def _force_delta_from_prompt(
    *,
    session: AsyncSession,
    universe: UniverseModel,
    tick: int,
    prompt: str,
    reason: str,
) -> dict[str, Any]:
    """Use the god tier to convert natural language into BranchDelta."""
    from backend.app.core.config import settings as _settings
    from backend.app.core.redis_client import get_redis_client
    from backend.app.providers import call_with_policy, ensure_providers_in_loop
    from backend.app.providers.rate_limits import ProviderRateLimiter
    from backend.app.providers.routing import RoutingTable
    from backend.app.schemas.llm import PromptPacket
    from backend.app.schemas.common import Clock

    await ensure_providers_in_loop(_settings)
    routing = await RoutingTable.from_db(session)
    limiter = ProviderRateLimiter(
        get_redis_client(),
        provider="openrouter",
        rpm_limit=600,
        tpm_limit=1_000_000,
        max_concurrency=8,
        daily_budget_usd=None,
        jitter=False,
    )
    packet = PromptPacket(
        system=(
            "Convert the operator request into one valid WorldFork BranchDelta. "
            "Return JSON only with a single top-level key delta. Valid delta types: "
            "parameter_shift, actor_state_override, hero_decision_override, "
            "counterfactual_event_rewrite. Use new_value for actor_state_override "
            "and new_decision plus tick for hero_decision_override."
        ),
        clock=Clock(
            current_tick=tick,
            tick_duration_minutes=1440,
            elapsed_minutes=tick * 1440,
            previous_tick_minutes=None,
            max_schedule_horizon_ticks=10,
        ),
        actor_id=f"force-deviation:{universe.universe_id}",
        actor_kind="god",
        state={
            "universe_id": universe.universe_id,
            "run_id": universe.big_bang_id,
            "current_tick": universe.current_tick,
            "branch_depth": universe.branch_depth,
            "latest_metrics": dict(universe.latest_metrics or {}),
            "operator_prompt": prompt,
            "operator_reason": reason,
        },
        sot_excerpt={},
        visible_feed=[],
        visible_events=[],
        own_queued_events=[],
        own_recent_actions=[],
        retrieved_memory=None,
        allowed_tools=[],
        output_schema_id="force_deviation_schema",
        temperature=0.2,
        metadata={"tick": tick, "mode": "god_prompt"},
    )
    try:
        result = await call_with_policy(
            job_type="force_deviation",
            prompt=packet,
            routing=routing,
            limiter=limiter,
            ledger=None,
            run_id=universe.big_bang_id,
            universe_id=universe.universe_id,
            tick=tick,
            max_attempts=2,
        )
        parsed = result.parsed_json or {}
        delta = parsed.get("delta") if isinstance(parsed.get("delta"), dict) else parsed
        if isinstance(delta, dict) and delta.get("type"):
            return delta
    except Exception:
        pass
    return {
        "type": "parameter_shift",
        "target": "operator_forced_deviation",
        "delta": {"strength": 1.0},
    }


async def _forced_branch_policy_result(
    *,
    session: AsyncSession,
    universe: UniverseModel,
) -> Any:
    """Approve a forced branch only if hard capacity caps permit it."""
    from backend.app.models.settings import BranchPolicySettingModel
    from backend.app.schemas.branching import BranchPolicyResult

    policy = await session.get(BranchPolicySettingModel, "default")
    max_depth = int(getattr(policy, "max_depth", 8) or 8)
    max_total = int(getattr(policy, "max_total_branches", 500) or 500)
    max_active = int(getattr(policy, "max_active_universes", 50) or 50)

    if universe.branch_depth + 1 > max_depth:
        raise HTTPException(status_code=409, detail=f"Forced branch exceeds max depth {max_depth}.")
    total_branches = int(
        (
            await session.execute(
                select(func.count(UniverseModel.universe_id)).where(
                    UniverseModel.big_bang_id == universe.big_bang_id,
                    UniverseModel.parent_universe_id.is_not(None),
                )
            )
        ).scalar_one()
        or 0
    )
    if total_branches >= max_total:
        raise HTTPException(status_code=409, detail=f"Forced branch exceeds total branch cap {max_total}.")
    active_count = int(
        (
            await session.execute(
                select(func.count(UniverseModel.universe_id)).where(
                    UniverseModel.big_bang_id == universe.big_bang_id,
                    UniverseModel.status == "active",
                )
            )
        ).scalar_one()
        or 0
    )
    if active_count >= max_active:
        raise HTTPException(status_code=409, detail=f"Forced branch exceeds active universe cap {max_active}.")

    return BranchPolicyResult(
        decision="approve",
        reason="Operator-forced deviation bypassed cooldown and divergence checks.",
        cost_estimate={"source": "force_deviation"},
        divergence_score=1.0,
    )


@router.post("/{universe_id}/force-deviation", status_code=202, response_model=ForceDeviationResponse)
async def force_deviation(
    universe_id: str,
    body: ForceDeviationRequest,
    session: DbSession,
) -> ForceDeviationResponse:
    """Branch from a historical tick by operator prompt or structured delta."""
    uni = await _get_universe_or_404(universe_id, session)
    if body.tick > uni.current_tick:
        raise HTTPException(
            status_code=422,
            detail=f"Force-deviation tick {body.tick} is ahead of current_tick {uni.current_tick}.",
        )
    if uni.status == "killed":
        raise HTTPException(status_code=409, detail="Cannot force-deviate from a killed universe.")

    if body.mode == "structured_delta":
        if not body.delta:
            raise HTTPException(status_code=422, detail="structured_delta mode requires delta.")
        delta_payload = body.delta
    else:
        if not body.prompt or not body.prompt.strip():
            raise HTTPException(status_code=422, detail="god_prompt mode requires prompt.")
        delta_payload = await _force_delta_from_prompt(
            session=session,
            universe=uni,
            tick=body.tick,
            prompt=body.prompt,
            reason=body.reason,
        )

    from backend.app.branching.branch_engine import commit_branch, enqueue_first_child_tick
    from backend.app.schemas.branching import BranchDelta
    from backend.app.workers import scheduler

    try:
        delta = TypeAdapter(BranchDelta).validate_python(delta_payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    policy_result = await _forced_branch_policy_result(session=session, universe=uni)
    run_folder = await _get_run_folder(uni, session)
    ledger = None
    if run_folder is not None:
        try:
            from backend.app.storage.ledger import Ledger

            ledger = Ledger.open(run_folder.parent.parent, uni.big_bang_id)
        except Exception:
            ledger = None

    job_envelope = scheduler.make_envelope(
        job_type="force_deviation",
        run_id=uni.big_bang_id,
        universe_id=universe_id,
        tick=body.tick,
        idempotency_key=f"force_deviation:{uni.big_bang_id}:{universe_id}:t{body.tick}:{uuid.uuid4().hex}",
        payload={
            "parent_universe_id": universe_id,
            "branch_from_tick": body.tick,
            "mode": body.mode,
            "prompt": body.prompt,
            "delta": delta.model_dump(mode="json"),
            "reason": body.reason,
            "auto_start": body.auto_start,
        },
    )
    await scheduler.mark_enqueued(job_envelope)
    await scheduler.mark_started(job_envelope.job_id)
    result = await commit_branch(
        session=session,
        parent_universe=uni,
        branch_from_tick=body.tick,
        delta=delta,
        branch_reason=body.reason or f"Operator-forced deviation at tick {body.tick}",
        policy_result=policy_result,
        ledger=ledger,
        enqueue_first_tick=False,
    )
    await session.commit()

    enqueued = False
    if body.auto_start and result.status == "active":
        enqueued = await enqueue_first_child_tick(
            run_id=uni.big_bang_id,
            child_universe_id=result.child_universe_id,
            tick=body.tick + 1,
        )

    audit_path: str | None = None
    audit_payload = {
        "job_id": job_envelope.job_id,
        "parent_universe_id": universe_id,
        "child_universe_id": result.child_universe_id,
        "tick": body.tick,
        "mode": body.mode,
        "prompt": body.prompt,
        "reason": body.reason,
        "generated_delta": delta.model_dump(mode="json"),
        "policy_result": policy_result.model_dump(mode="json"),
        "auto_start": body.auto_start,
        "first_tick_enqueued": enqueued,
    }
    if ledger is not None:
        rel = f"universes/{universe_id}/ticks/tick_{body.tick:03d}/forced_deviations/{job_envelope.job_id}.json"
        try:
            ledger.write_artifact(rel, _redact(audit_payload), immutable=True)
            audit_path = str(ledger.run_folder / rel)
        except Exception:
            audit_path = None

    await scheduler.mark_succeeded(
        job_envelope.job_id,
        result_summary={
            "child_universe_id": result.child_universe_id,
            "parent_universe_id": universe_id,
            "branch_from_tick": body.tick,
            "mode": body.mode,
            "first_tick_enqueued": enqueued,
            "audit_artifact_path": audit_path,
        },
    )
    return ForceDeviationResponse(
        run_id=uni.big_bang_id,
        parent_universe_id=universe_id,
        child_universe_id=result.child_universe_id,
        tick=body.tick,
        mode=body.mode,
        job_id=job_envelope.job_id,
        status=result.status,
        enqueued=enqueued,
        generated_delta=delta.model_dump(mode="json"),
        audit_artifact_path=audit_path,
        note="Forced deviation committed and child universe created.",
    )


# ---------------------------------------------------------------------------
# GET /api/universes/{universe_id}/lineage
# ---------------------------------------------------------------------------


@router.get("/{universe_id}/lineage", response_model=LineageResponse)
async def get_lineage(universe_id: str, session: DbSession) -> LineageResponse:
    uni = await _get_universe_or_404(universe_id, session)
    return LineageResponse(
        lineage_path=list(uni.lineage_path or [universe_id]),
        parent=uni.parent_universe_id,
        depth=uni.branch_depth,
        branch_from_tick=uni.branch_from_tick,
    )


# ---------------------------------------------------------------------------
# GET /api/universes/{universe_id}/descendants
# ---------------------------------------------------------------------------


@router.get("/{universe_id}/descendants", response_model=DescendantsResponse)
async def get_descendants(universe_id: str, session: DbSession) -> DescendantsResponse:
    """Return a nested tree of all descendants."""
    uni = await _get_universe_or_404(universe_id, session)

    # Fetch all universes under the same big_bang_id to build the tree in Python.
    all_unis_result = await session.execute(
        select(UniverseModel).where(UniverseModel.big_bang_id == uni.big_bang_id)
    )
    all_unis = all_unis_result.scalars().all()

    # Build parent → children map.
    children_map: dict[str, list[UniverseModel]] = {}
    for u in all_unis:
        pid = u.parent_universe_id
        children_map.setdefault(pid or "__root__", []).append(u)

    def _build_node(u: UniverseModel) -> DescendantsResponse:
        child_nodes = [
            _build_node(c) for c in children_map.get(u.universe_id, [])
        ]
        return DescendantsResponse(
            universe_id=u.universe_id,
            status=u.status,
            depth=u.branch_depth,
            current_tick=u.current_tick,
            children=child_nodes,
        )

    return _build_node(uni)
