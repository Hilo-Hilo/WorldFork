"""Run-level results synthesis for completed WorldFork simulations."""
from __future__ import annotations

import json
import asyncio
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.clock import now_utc
from backend.app.schemas.common import Clock
from backend.app.schemas.llm import PromptPacket
from backend.app.storage.ledger import Ledger


def _metric_number(metrics: dict[str, Any], keys: tuple[str, ...], default: float = 0.0) -> float:
    for key in keys:
        value = metrics.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return default


def _bucket(value: float, *, low: float, high: float, labels: tuple[str, str, str]) -> str:
    if value < low:
        return labels[0]
    if value > high:
        return labels[2]
    return labels[1]


async def _collect_deterministic_payload(session: AsyncSession, run_id: str) -> dict[str, Any]:
    from backend.app.models.events import EventModel
    from backend.app.models.jobs import JobModel
    from backend.app.models.llm_calls import LLMCallModel
    from backend.app.models.posts import SocialPostModel
    from backend.app.models.runs import BigBangRunModel
    from backend.app.models.universes import UniverseModel

    run = await session.get(BigBangRunModel, run_id)
    if run is None:
        raise ValueError(f"run {run_id!r} not found")

    universes = list(
        (
            await session.execute(
                select(UniverseModel)
                .where(UniverseModel.big_bang_id == run_id)
                .order_by(UniverseModel.branch_depth, UniverseModel.created_at)
            )
        )
        .scalars()
        .all()
    )

    event_rows = list(
        (
            await session.execute(
                select(EventModel)
                .join(UniverseModel, EventModel.universe_id == UniverseModel.universe_id)
                .where(UniverseModel.big_bang_id == run_id)
                .order_by(EventModel.scheduled_tick, EventModel.created_tick)
            )
        )
        .scalars()
        .all()
    )
    post_count = int(
        (
            await session.execute(
                select(func.count(SocialPostModel.post_id))
                .join(UniverseModel, SocialPostModel.universe_id == UniverseModel.universe_id)
                .where(UniverseModel.big_bang_id == run_id)
            )
        ).scalar_one()
        or 0
    )
    llm_totals = (
        await session.execute(
            select(
                func.count(LLMCallModel.call_id),
                func.coalesce(func.sum(LLMCallModel.total_tokens), 0),
                func.coalesce(func.sum(LLMCallModel.cost_usd), 0.0),
            ).where(LLMCallModel.run_id == run_id)
        )
    ).one()
    failed_jobs = int(
        (
            await session.execute(
                select(func.count(JobModel.job_id)).where(
                    JobModel.run_id == run_id,
                    JobModel.status == "failed",
                )
            )
        ).scalar_one()
        or 0
    )

    mobilization_values: list[float] = []
    polarization_values: list[float] = []
    trust_values: list[float] = []
    universe_outcomes: list[dict[str, Any]] = []
    for uni in universes:
        metrics = dict(uni.latest_metrics or {})
        mobilization = _metric_number(metrics, ("mobilization_risk", "mobilization", "mobilizationRisk"))
        polarization = _metric_number(metrics, ("polarization", "issue_polarization"))
        trust = _metric_number(metrics, ("trust", "trust_index", "institutional_trust"), 0.5)
        mobilization_values.append(mobilization)
        polarization_values.append(polarization)
        trust_values.append(trust)
        universe_outcomes.append(
            {
                "universe_id": uni.universe_id,
                "parent_universe_id": uni.parent_universe_id,
                "status": uni.status,
                "current_tick": uni.current_tick,
                "branch_depth": uni.branch_depth,
                "branch_from_tick": uni.branch_from_tick,
                "branch_reason": uni.branch_reason,
                "branch_delta": dict(uni.branch_delta or {}),
                "metrics": metrics,
                "outcome_label": _bucket(
                    mobilization,
                    low=0.2,
                    high=0.65,
                    labels=("low mobilization", "contested", "high mobilization"),
                ),
            }
        )

    avg_mobilization = sum(mobilization_values) / max(1, len(mobilization_values))
    avg_polarization = sum(polarization_values) / max(1, len(polarization_values))
    avg_trust = sum(trust_values) / max(1, len(trust_values))
    max_risk = max((float(ev.risk_level or 0.0) for ev in event_rows), default=0.0)

    branch_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for outcome in universe_outcomes:
        delta_value = outcome.get("branch_delta")
        delta = delta_value if isinstance(delta_value, dict) else {}
        key = str(delta.get("type") or "root")
        branch_groups[key].append(outcome)

    branch_clusters = [
        {
            "cluster": key,
            "universe_count": len(items),
            "statuses": dict(Counter(str(item.get("status")) for item in items)),
            "universe_ids": [str(item["universe_id"]) for item in items],
        }
        for key, items in sorted(branch_groups.items())
    ]
    timeline_highlights = [
        {
            "tick": int(ev.scheduled_tick),
            "title": ev.title,
            "description": ev.description,
            "event_type": ev.event_type,
            "status": ev.status,
            "risk_level": float(ev.risk_level or 0.0),
            "universe_id": ev.universe_id,
        }
        for ev in event_rows[:50]
    ]

    classifications = {
        "conflict_trajectory": _bucket(
            max(avg_mobilization, avg_polarization),
            low=0.25,
            high=0.65,
            labels=("de-escalated", "contested", "escalated"),
        ),
        "institutional_legitimacy": _bucket(
            avg_trust,
            low=0.35,
            high=0.65,
            labels=("eroded", "mixed", "durable"),
        ),
        "collective_action": _bucket(
            avg_mobilization,
            low=0.2,
            high=0.6,
            labels=("dormant", "localized", "mass mobilized"),
        ),
        "harm_outcome": _bucket(
            max_risk,
            low=0.25,
            high=0.7,
            labels=("low harm", "material harm risk", "severe harm risk"),
        ),
        "dominant_driver": "events" if len(event_rows) >= post_count else "public discourse",
        "scenario_specific_labels": [
            label
            for label in ("labor", "flood-risk", "public-trust", "misinformation", "mutual-aid")
            if label.replace("-", " ") in run.scenario_text.lower()
        ],
    }

    metrics = {
        "universe_count": len(universes),
        "branch_count": max(0, len(universes) - 1),
        "event_count": len(event_rows),
        "post_count": post_count,
        "llm_call_count": int(llm_totals[0] or 0),
        "llm_total_tokens": int(llm_totals[1] or 0),
        "llm_cost_usd": float(llm_totals[2] or 0.0),
        "failed_job_count": failed_jobs,
        "avg_mobilization": avg_mobilization,
        "avg_polarization": avg_polarization,
        "avg_trust": avg_trust,
    }

    return {
        "run_id": run_id,
        "display_name": run.display_name,
        "scenario_text": run.scenario_text,
        "summary": (
            f"{run.display_name} completed with {len(universes)} universes, "
            f"{len(event_rows)} events, and {post_count} social posts."
        ),
        "classifications": classifications,
        "branch_clusters": branch_clusters,
        "universe_outcomes": universe_outcomes,
        "timeline_highlights": timeline_highlights,
        "metrics": metrics,
    }


def _merge_agent_payload(base: dict[str, Any], parsed: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(parsed, dict):
        return base
    merged = dict(base)
    for key in ("summary", "classifications", "branch_clusters", "universe_outcomes", "timeline_highlights", "metrics"):
        value = parsed.get(key)
        if value is None:
            continue
        if key in {"classifications", "metrics"} and isinstance(value, dict):
            merged[key] = value
        elif key == "summary" and isinstance(value, str):
            merged[key] = value
        elif key not in {"classifications", "metrics", "summary"} and isinstance(value, list):
            merged[key] = value
    return merged


async def aggregate_run_results(
    *,
    session: AsyncSession,
    run_id: str,
    routing: Any,
    limiter: Any,
    ledger: Ledger | None = None,
) -> dict[str, Any]:
    """Aggregate terminal run data, optionally refine it with the god tier."""
    from backend.app.models.results import RunResultModel
    from backend.app.providers import call_with_policy

    base = await _collect_deterministic_payload(session, run_id)
    result_row = await session.get(RunResultModel, run_id)
    if result_row is None:
        result_row = RunResultModel(
            run_id=run_id,
            status="running",
            classifications={},
            branch_clusters=[],
            universe_outcomes=[],
            timeline_highlights=[],
            metrics={},
        )
        session.add(result_row)
    result_row.status = "running"
    result_row.error = None
    await session.flush()

    provider: str | None = None
    model_used: str | None = None
    agent_error: str | None = None
    merged = base
    try:
        prompt = PromptPacket(
            system=(
                "You are the WorldFork run-results aggregation agent. "
                "Return JSON only with keys summary, classifications, "
                "branch_clusters, universe_outcomes, timeline_highlights, and metrics. "
                "Classify outcomes along conflict_trajectory, institutional_legitimacy, "
                "collective_action, harm_outcome, dominant_driver, and scenario_specific_labels. "
                "Do not include chain-of-thought."
            ),
            clock=Clock(
                current_tick=0,
                tick_duration_minutes=1440,
                elapsed_minutes=0,
                previous_tick_minutes=None,
                max_schedule_horizon_ticks=1,
            ),
            actor_id=f"results:{run_id}",
            actor_kind="god",
            state=base,
            sot_excerpt={},
            visible_feed=[],
            visible_events=base.get("timeline_highlights", []),
            own_queued_events=[],
            own_recent_actions=[],
            retrieved_memory=None,
            allowed_tools=[],
            output_schema_id="run_results_schema",
            temperature=0.2,
            metadata={"run_id": run_id, "prompt_template": "run_results"},
        )
        llm_result = await asyncio.wait_for(
            call_with_policy(
                job_type="aggregate_run_results",
                prompt=prompt,
                routing=routing,
                limiter=limiter,
                ledger=None,
                run_id=run_id,
                universe_id=None,
                tick=None,
                max_attempts=1,
            ),
            timeout=35,
        )
        provider = llm_result.provider
        model_used = llm_result.model_used
        merged = _merge_agent_payload(base, llm_result.parsed_json)
    except Exception as exc:  # noqa: BLE001
        agent_error = str(exc) or type(exc).__name__
        merged = base

    generated_at = now_utc()
    artifact_path: str | None = None
    artifact_payload = {
        "status": "succeeded",
        "generated_at": generated_at.isoformat(),
        "provider": provider,
        "model_used": model_used,
        "agent_error": agent_error,
        **merged,
    }
    if ledger is not None:
        rel = f"results/run_results_{generated_at.strftime('%Y%m%dT%H%M%SZ')}.json"
        ledger.write_artifact(rel, artifact_payload, immutable=True)
        artifact_path = str(ledger.run_folder / rel)
    else:
        artifact_path = None

    result_row.status = "succeeded"
    result_row.generated_at = generated_at
    result_row.provider = provider
    result_row.model_used = model_used
    result_row.summary = str(merged.get("summary") or "")
    result_row.classifications = dict(merged.get("classifications") or {})
    result_row.branch_clusters = list(merged.get("branch_clusters") or [])
    result_row.universe_outcomes = list(merged.get("universe_outcomes") or [])
    result_row.timeline_highlights = list(merged.get("timeline_highlights") or [])
    result_row.metrics = dict(merged.get("metrics") or {})
    result_row.artifact_path = artifact_path
    result_row.error = agent_error
    await session.commit()

    return {
        "status": "succeeded",
        "run_id": run_id,
        "artifact_path": artifact_path,
        "model_used": model_used,
        "agent_error": agent_error,
        "result": json.loads(json.dumps(artifact_payload, default=str)),
    }


def ledger_for_run_folder(run_folder_path: str | None, run_id: str) -> Ledger | None:
    if not run_folder_path:
        return None
    path = Path(run_folder_path)
    if not path.exists():
        return None
    try:
        return Ledger.open(path.parent.parent, run_id)
    except Exception:
        return None
