from __future__ import annotations

import json
from typing import Any, Literal, TypedDict

from sqlalchemy.orm import Session
from langgraph.graph import END, StateGraph

from app.core.config import get_settings
from app.db import models
from app.llm.audit import complete_with_audit
from app.llm.prompt_budget import budget_god_provisional_bundle
from app.llm.prompt_templates import GOD_AGENT_SYSTEM_PROMPT
from app.llm.routing import AuditedLLMRoute
from app.domains.governance.god_tools import VALID_TOOLS, execute_tool_call
from app.domains.multiverse.runtime_config import branch_policy_for_multiverse

MAX_GOD_AGENT_ITERATIONS = 3


class GodAgentLoopState(TypedDict, total=False):
    messages: list[dict[str, str]]
    parsed: dict[str, Any]
    final_call: models.LLMCall | None
    iterations: list[dict[str, Any]]
    executed_tool_calls: list[dict[str, Any]]
    executed_tool_keys: list[str]
    current_tool_calls: list[dict[str, Any]]
    iteration: int
    should_continue: bool


def review_provisional_tick(
    db: Session,
    multiverse: models.Multiverse,
    provisional_bundle: dict,
    *,
    tick_snapshot_id=None,
) -> tuple[dict, models.LLMCall | None]:
    tick_index = provisional_bundle["tick_index"]
    settings = get_settings()
    prompt_bundle = budget_god_provisional_bundle(
        provisional_bundle,
        max_chars=settings.prompt_god_bundle_max_chars,
    )
    messages = [
        {"role": "system", "content": GOD_AGENT_SYSTEM_PROMPT},
        {"role": "user", "content": f"UNTRUSTED provisional tick bundle for review: {prompt_bundle}"},
    ]
    initial_state: GodAgentLoopState = {
        "messages": messages,
        "parsed": {},
        "final_call": None,
        "iterations": [],
        "executed_tool_calls": [],
        "executed_tool_keys": [],
        "iteration": 0,
        "should_continue": True,
    }

    def call_god_model(state: GodAgentLoopState) -> GodAgentLoopState:
        iteration = int(state.get("iteration") or 0) + 1
        response, call = complete_with_audit(
            db,
            big_bang_id=multiverse.big_bang_id,
            purpose=f"god_review_{multiverse.id}_tick_{tick_index}_iter_{iteration}",
            model=settings.god_agent_model,
            route=AuditedLLMRoute.GOD_AGENT,
            messages=state["messages"],
            metadata={"max_tokens": 1400, "temperature": 0.2, "god_iteration": iteration},
        )
        parsed = response.parsed or {}
        tool_calls = _prepare_tool_calls(
            db,
            multiverse=multiverse,
            provisional_bundle=provisional_bundle,
            parsed=parsed,
            tick_index=tick_index,
        )
        return {
            **state,
            "parsed": parsed,
            "final_call": call,
            "iteration": iteration,
            "current_tool_calls": tool_calls,
        }

    def execute_and_audit_tools(state: GodAgentLoopState) -> GodAgentLoopState:
        parsed = state.get("parsed") or {}
        tool_calls = state.get("current_tool_calls") or []
        iteration_record = {
            "iteration": state.get("iteration"),
            "llm_call_id": str(state["final_call"].id) if state.get("final_call") else None,
            "decision": parsed.get("decision"),
            "rationale": parsed.get("rationale"),
            "tool_calls": tool_calls,
            "tool_results": [],
        }
        executed_tool_keys = set(state.get("executed_tool_keys") or [])
        executed_tool_calls = list(state.get("executed_tool_calls") or [])
        for tool_call in tool_calls:
            key = tool_call.get("idempotency_key")
            if key in executed_tool_keys:
                continue
            executed_tool_keys.add(key)
            row = execute_tool_call(
                db,
                big_bang_id=multiverse.big_bang_id,
                multiverse=multiverse,
                tick_snapshot_id=tick_snapshot_id,
                god_review_id=None,
                tool_name=tool_call["tool_name"],
                arguments=tool_call.get("arguments", {}),
                idempotency_key=tool_call["idempotency_key"],
            )
            result = {
                "tool_name": row.tool_name,
                "status": row.status,
                "result": row.result,
                "error": row.error,
                "tool_call_id": str(row.id),
                "idempotency_key": row.idempotency_key,
            }
            iteration_record["tool_results"].append(result)
            executed_tool_calls.append(tool_call)
        iterations = [*(state.get("iterations") or []), iteration_record]
        messages = list(state["messages"])
        messages.append(
            {
                "role": "assistant",
                "content": _prompt_json(
                    {
                        "decision": parsed.get("decision"),
                        "rationale": parsed.get("rationale"),
                        "tool_calls": tool_calls,
                    }
                ),
            }
        )
        messages.append(
            {
                "role": "user",
                "content": (
                    "Backend tool audit results for this same tick: "
                    f"{_prompt_json(iteration_record['tool_results'])}\n"
                    "If the tick state is now coherent, return final JSON with no additional "
                    "mutation tools or a continue_timeline tool. If a tool failed or population "
                    "conservation is still incoherent, emit only the repair tool calls needed."
                ),
            }
        )
        has_failed_tool = any(item.get("status") == "failed" for item in iteration_record["tool_results"])
        should_continue = bool(tool_calls) and has_failed_tool and int(state.get("iteration") or 0) < MAX_GOD_AGENT_ITERATIONS
        return {
            **state,
            "messages": messages,
            "iterations": iterations,
            "executed_tool_calls": executed_tool_calls,
            "executed_tool_keys": sorted(executed_tool_keys),
            "should_continue": should_continue,
        }

    def route_after_tools(state: GodAgentLoopState) -> Literal["continue", "finish"]:
        return "continue" if state.get("should_continue") else "finish"

    graph = StateGraph(GodAgentLoopState)
    graph.add_node("call_god_model", call_god_model)
    graph.add_node("execute_and_audit_tools", execute_and_audit_tools)
    graph.set_entry_point("call_god_model")
    graph.add_edge("call_god_model", "execute_and_audit_tools")
    graph.add_conditional_edges(
        "execute_and_audit_tools",
        route_after_tools,
        {"continue": "call_god_model", "finish": END},
    )
    final_state = graph.compile().invoke(initial_state)

    parsed = final_state.get("parsed") or {}
    final_call = final_state.get("final_call")
    iterations = final_state.get("iterations") or []
    executed_tool_calls = final_state.get("executed_tool_calls") or []
    tool_calls = executed_tool_calls or _prepare_tool_calls(
        db,
        multiverse=multiverse,
        provisional_bundle=provisional_bundle,
        parsed=parsed,
        tick_index=tick_index,
    )
    if not tool_calls:
        tool_calls = [
            {
                "tool_name": "continue_timeline",
                "arguments": {"reason": "No validated branch trigger or structural mutation in this tick."},
                "idempotency_key": f"god:{multiverse.id}:tick:{tick_index}:continue",
            }
        ]
        row = execute_tool_call(
            db,
            big_bang_id=multiverse.big_bang_id,
            multiverse=multiverse,
            tick_snapshot_id=tick_snapshot_id,
            god_review_id=None,
            tool_name="continue_timeline",
            arguments=tool_calls[0]["arguments"],
            idempotency_key=tool_calls[0]["idempotency_key"],
        )
        iterations.append(
            {
                "iteration": "fallback_continue",
                "tool_results": [
                    {
                        "tool_name": row.tool_name,
                        "status": row.status,
                        "result": row.result,
                        "error": row.error,
                        "tool_call_id": str(row.id),
                        "idempotency_key": row.idempotency_key,
                    }
                ],
            }
        )

    branch_score = provisional_bundle.get("branch_score", 0)
    review = {
        "decision": parsed.get("decision") or ("branch" if _has_tool(tool_calls, "create_branch") else "continue"),
        "rationale": parsed.get("rationale") or "God Agent reviewed the provisional tick bundle.",
        "confidence": float(parsed.get("confidence", 0.8) or 0.8),
        "tool_calls": tool_calls,
        "rejected_candidates": parsed.get("rejected_candidates") if isinstance(parsed.get("rejected_candidates"), list) else [],
        "watchlist": parsed.get("watchlist") if isinstance(parsed.get("watchlist"), list) else [],
        "god_agent_iterations": iterations,
        "endpoint_ledger_updates": parsed.get("endpoint_ledger_updates") if isinstance(parsed.get("endpoint_ledger_updates"), list) else [],
        "endpoint_ledger_summary": parsed.get("endpoint_ledger_summary") or "",
        "input_summary": {
            "multiverse_id": str(multiverse.id),
            "tick_index": tick_index,
            "events": len(provisional_bundle.get("executed_events", [])),
            "branch_score": branch_score,
            "endpoint_ledger_version": (provisional_bundle.get("endpoint_ledger") or {}).get("version"),
            "llm_call_id": str(final_call.id) if final_call else None,
            "god_agent_iterations": len(iterations),
            "prompt_budget": prompt_bundle.get("prompt_budget", {}),
        },
    }
    return review, final_call


def _prepare_tool_calls(
    db: Session,
    *,
    multiverse: models.Multiverse,
    provisional_bundle: dict,
    parsed: dict,
    tick_index: int,
) -> list[dict]:
    tool_calls = _normalize_tool_calls(parsed.get("tool_calls"), multiverse.id, tick_index)
    tool_calls = _attach_candidate_ids(tool_calls, provisional_bundle)
    tool_calls = _prune_tool_calls(tool_calls)
    final_tick_context = provisional_bundle.get("final_tick_context") or {}
    is_final_allowed_tick = isinstance(final_tick_context, dict) and bool(
        final_tick_context.get("is_final_allowed_tick")
    )
    branch_runway = _branch_runway_context(
        db,
        multiverse=multiverse,
        provisional_bundle=provisional_bundle,
        tick_index=tick_index,
    )
    forecast_root_seeded = False
    if branch_runway["suppress_branching"]:
        tool_calls = [call for call in tool_calls if call.get("tool_name") != "create_branch"]
    else:
        seeded_branches = _forecast_candidate_branch_tool_calls(
            db,
            multiverse=multiverse,
            provisional_bundle=provisional_bundle,
            tick_index=tick_index,
        )
        if seeded_branches:
            tool_calls = [
                call
                for call in tool_calls
                if call.get("tool_name") not in {"continue_timeline", "create_branch"}
            ]
            tool_calls.extend(seeded_branches)
            tool_calls = _prune_tool_calls(tool_calls)
        elif _forecast_root_already_seeded(db, multiverse):
            tool_calls = [call for call in tool_calls if call.get("tool_name") != "create_branch"]
            forecast_root_seeded = True
    idle_assessment = provisional_bundle.get("idle_assessment") or {}
    if idle_assessment.get("should_terminate"):
        return [
            {
                "tool_name": "terminate_timeline",
                "arguments": {
                    "reason": "Timeline reached idle termination threshold.",
                    "idle_assessment": idle_assessment,
                },
                "idempotency_key": f"god:{multiverse.id}:tick:{tick_index}:terminate_idle",
            }
        ]
    branch_score = provisional_bundle.get("branch_score", 0)
    structural_tools = {
        "create_branch",
        "approve_split",
        "plan_merge",
        "approve_emergence",
        "update_population_archetype_total",
        "update_cohort_state",
        "update_hero_state",
        "apply_population_delta",
        "split_cohort",
        "merge_cohorts",
        "create_cohort",
        "deactivate_cohort",
        "deactivate_hero",
        "kill_hero",
    }
    parsed_decision = str(parsed.get("decision") or "").strip().lower()
    if parsed_decision == "complete_universe" and not tool_calls:
        return [
            {
                "tool_name": "mark_ready_for_report",
                "arguments": {"reason": "God Agent marked the timeline complete."},
                "idempotency_key": f"god:{multiverse.id}:tick:{tick_index}:mark_ready_for_report",
            }
        ]
    explicit_continue = parsed_decision == "continue" or any(
        call["tool_name"] == "continue_timeline" for call in tool_calls
    )
    has_structural = any(call["tool_name"] in structural_tools for call in tool_calls)
    has_branch = any(call["tool_name"] == "create_branch" for call in tool_calls)
    branch_threshold = _branch_score_threshold(db, multiverse)
    if (
        not branch_runway["suppress_branching"]
        and not forecast_root_seeded
        and branch_score >= branch_threshold
        and not has_branch
        and (has_structural or not explicit_continue)
    ):
        tool_calls.append(
            {
                "tool_name": "create_branch",
                "arguments": {
                    "fork_tick_index": tick_index,
                    "reason": "Branch threshold crossed from graph/sociology candidate evidence.",
                    "branch_probability": _heuristic_branch_probability(
                        parsed=parsed,
                        branch_score=branch_score,
                        branch_threshold=branch_threshold,
                    ),
                    "probability_source": "heuristic_branch_score",
                    "probability_basis": (
                        "Auto-created because branch_score crossed threshold and the God output did not include "
                        "an explicit create_branch tool call."
                    ),
                },
                "idempotency_key": f"god:{multiverse.id}:tick:{tick_index}:create_branch:0",
            }
        )
    elif not tool_calls:
        if is_final_allowed_tick:
            reason = (
                "Final allowed tick reached; terminal settlement must use the endpoint ledger "
                "instead of creating a branch."
            )
        elif branch_runway["branching_disabled"]:
            reason = "Branching disabled by branch policy for this condition."
        elif branch_runway["suppress_branching"]:
            reason = (
                "Branch runway too short for a useful child timeline: "
                f"{branch_runway['remaining_ticks']} ticks remain, "
                f"requires {branch_runway['min_branch_runway_ticks']}."
            )
        else:
            reason = "No validated branch trigger in this tick."
        tool_calls.append(
            {
                "tool_name": "continue_timeline",
                "arguments": {"reason": reason},
                "idempotency_key": f"god:{multiverse.id}:tick:{tick_index}:continue",
            }
        )
    return _attach_branch_probabilities(
        tool_calls,
        parsed=parsed,
        branch_score=branch_score,
        branch_threshold=branch_threshold,
    )


def _forecast_candidate_branch_tool_calls(
    db: Session,
    *,
    multiverse: models.Multiverse,
    provisional_bundle: dict,
    tick_index: int,
) -> list[dict]:
    if multiverse.parent_multiverse_id is not None or tick_index > 1:
        return []
    if _existing_child_branch_count(db, multiverse) > 0:
        return []
    branch_policy = branch_policy_for_multiverse(db, multiverse)
    max_per_tick = max(1, _coerce_int(branch_policy.get("max_branches_per_tick")) or 1)
    hypotheses = _forecast_branch_hypotheses_for_review(db, multiverse, provisional_bundle)
    selected = _select_binary_candidate_hypotheses(hypotheses)[:max_per_tick]
    if len(selected) < 2:
        return []
    calls: list[dict] = []
    for index, item in enumerate(selected):
        remaining = len(selected) - index
        branch_probability = round(1.0 / remaining, 4) if remaining > 1 else 1.0
        parent_continuation = round(max(0.0, 1.0 - branch_probability), 4)
        candidate_id = str(item.get("candidate_endpoint_id") or "").strip().lower()
        premise = _branch_hypothesis_premise(item)
        label = str(item.get("label") or f"{candidate_id.upper()} candidate endpoint path").strip()
        calls.append(
            {
                "tool_name": "create_branch",
                "arguments": {
                    "fork_tick_index": tick_index,
                    "reason": label,
                    "branch_probability": branch_probability,
                    "parent_continuation_probability": parent_continuation,
                    "probability_source": "forecast_branch_hypothesis",
                    "probability_basis": "Seeded from complementary yes/no forecast-card branch hypotheses.",
                    "branch_premise": premise,
                    "alternate_path": premise,
                    "candidate_endpoint_id": candidate_id,
                },
                "idempotency_key": f"god:{multiverse.id}:tick:{tick_index}:forecast_branch:{candidate_id}",
            }
        )
    return calls


def _existing_child_branch_count(db: Session, multiverse: models.Multiverse) -> int:
    from sqlalchemy import func, select

    return int(
        db.scalar(
            select(func.count()).select_from(models.MultiverseLineageEdge).where(
                models.MultiverseLineageEdge.parent_multiverse_id == multiverse.id
            )
        )
        or 0
    )


def _forecast_root_already_seeded(db: Session, multiverse: models.Multiverse) -> bool:
    if multiverse.parent_multiverse_id is not None:
        return False
    if _existing_child_branch_count(db, multiverse) <= 0:
        return False
    big_bang = db.get(models.BigBang, multiverse.big_bang_id)
    scenario = big_bang.scenario_input if big_bang is not None and isinstance(big_bang.scenario_input, dict) else {}
    hypotheses = scenario.get("branch_hypotheses") if isinstance(scenario.get("branch_hypotheses"), list) else []
    candidate_ids = {
        str(item.get("candidate_endpoint_id") or item.get("candidate_id") or "").strip().lower()
        for item in hypotheses
        if isinstance(item, dict)
    }
    return {"yes", "no"}.issubset(candidate_ids)


def _forecast_branch_hypotheses_for_review(
    db: Session,
    multiverse: models.Multiverse,
    provisional_bundle: dict,
) -> list[dict]:
    raw = provisional_bundle.get("forecast_branch_hypotheses") if isinstance(provisional_bundle, dict) else None
    if not raw:
        big_bang = db.get(models.BigBang, multiverse.big_bang_id)
        scenario = big_bang.scenario_input if big_bang is not None and isinstance(big_bang.scenario_input, dict) else {}
        raw = scenario.get("branch_hypotheses")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _select_binary_candidate_hypotheses(items: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    for item in items:
        candidate_id = str(item.get("candidate_endpoint_id") or item.get("candidate_id") or "").strip().lower()
        if candidate_id in {"yes", "no"} and candidate_id not in by_id:
            by_id[candidate_id] = item
    return [by_id[key] for key in ("yes", "no") if key in by_id]


def _branch_hypothesis_premise(item: dict) -> str:
    for key in ("branch_premise", "alternate_path", "plausible_alternate_path", "expected_divergence", "trigger", "label"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    candidate_id = str(item.get("candidate_endpoint_id") or "candidate").strip().upper()
    return f"{candidate_id} forecast candidate endpoint path."


def _branch_score_threshold(db: Session, multiverse: models.Multiverse) -> float:
    settings = get_settings()
    branch_policy = branch_policy_for_multiverse(db, multiverse)
    threshold = branch_policy.get("branch_score_threshold", settings.branch_score_threshold)
    return float(threshold if threshold is not None else settings.branch_score_threshold)


def _branch_runway_context(
    db: Session,
    *,
    multiverse: models.Multiverse,
    provisional_bundle: dict,
    tick_index: int,
) -> dict:
    final_tick_context = provisional_bundle.get("final_tick_context") if isinstance(provisional_bundle, dict) else {}
    if not isinstance(final_tick_context, dict):
        final_tick_context = {}
    max_ticks = _coerce_int(final_tick_context.get("max_ticks"))
    current_tick = _coerce_int(final_tick_context.get("current_tick_index")) or int(tick_index)
    branch_policy = branch_policy_for_multiverse(db, multiverse)
    min_runway = max(1, _coerce_int(branch_policy.get("min_branch_runway_ticks")) or 1)
    remaining_ticks = max_ticks - current_tick if max_ticks is not None else None
    is_final = bool(final_tick_context.get("is_final_allowed_tick"))
    branching_disabled = branch_policy.get("branching_enabled") is False
    suppress = branching_disabled or is_final or (remaining_ticks is not None and remaining_ticks < min_runway)
    return {
        "suppress_branching": suppress,
        "branching_disabled": branching_disabled,
        "max_ticks": max_ticks,
        "current_tick_index": current_tick,
        "remaining_ticks": remaining_ticks,
        "min_branch_runway_ticks": min_runway,
    }


def _coerce_int(value) -> int | None:  # noqa: ANN001
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _normalize_tool_calls(raw_tool_calls, multiverse_id, tick_index: int) -> list[dict]:
    if not isinstance(raw_tool_calls, list):
        return []
    normalized = []
    for index, raw in enumerate(raw_tool_calls):
        if not isinstance(raw, dict):
            continue
        tool_name = raw.get("tool_name") or raw.get("name") or raw.get("function")
        if isinstance(tool_name, dict):
            tool_name = tool_name.get("name")
        arguments = raw.get("arguments") or raw.get("args") or raw.get("parameters") or {}
        if isinstance(arguments, str):
            arguments = {"value": arguments}
        if not tool_name:
            continue
        tool_name = _canonical_tool_name(str(tool_name))
        if tool_name not in VALID_TOOLS:
            continue
        if tool_name == "create_branch":
            arguments.setdefault("fork_tick_index", tick_index)
            arguments.setdefault("reason", "God Agent requested a branch from the current tick.")
        normalized.append(
            {
                "tool_name": tool_name,
                "arguments": arguments if isinstance(arguments, dict) else {},
                "idempotency_key": raw.get("idempotency_key")
                or f"god:{multiverse_id}:tick:{tick_index}:{tool_name}:{index}",
            }
        )
    return normalized


def _attach_branch_probabilities(
    tool_calls: list[dict],
    *,
    parsed: dict,
    branch_score: float,
    branch_threshold: float,
) -> list[dict]:
    for call in tool_calls:
        if call.get("tool_name") != "create_branch":
            continue
        arguments = call.setdefault("arguments", {})
        explicit = _coerce_probability(
            _first_present(
                arguments.get("branch_probability"),
                arguments.get("probability"),
                arguments.get("conditional_probability"),
                parsed.get("branch_probability"),
            )
        )
        if explicit is None:
            explicit = _heuristic_branch_probability(
                parsed=parsed,
                branch_score=branch_score,
                branch_threshold=branch_threshold,
            )
            arguments.setdefault("probability_source", "heuristic_branch_score")
            arguments.setdefault(
                "probability_basis",
                "Estimated from branch_score and God-agent confidence because no explicit branch_probability was supplied.",
            )
        else:
            arguments.setdefault("probability_source", "god_agent")
            arguments.setdefault(
                "probability_basis",
                "God-agent supplied conditional branch probability for this fork.",
            )
        arguments["branch_probability"] = explicit
        parent_continuation = _coerce_probability(
            _first_present(
                arguments.get("parent_continuation_probability"),
                parsed.get("parent_continuation_probability"),
            )
        )
        if parent_continuation is not None:
            arguments["parent_continuation_probability"] = parent_continuation
    return tool_calls


def _heuristic_branch_probability(*, parsed: dict, branch_score: float, branch_threshold: float) -> float:
    confidence = _coerce_probability(parsed.get("confidence")) or 0.8
    if branch_threshold >= 1.0:
        pressure = 0.0
    else:
        pressure = max(0.0, min(1.0, (float(branch_score or 0.0) - branch_threshold) / (1.0 - branch_threshold)))
    return round(max(0.05, min(0.85, 0.2 + 0.35 * pressure + 0.2 * confidence)), 4)


def _coerce_probability(value) -> float | None:  # noqa: ANN001
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return round(max(0.0, min(1.0, parsed)), 4)


def _first_present(*values):  # noqa: ANN001
    for value in values:
        if value is not None and value != "":
            return value
    return None


def _canonical_tool_name(tool_name: str) -> str:
    return tool_name.strip()


def _attach_candidate_ids(tool_calls: list[dict], provisional_bundle: dict) -> list[dict]:
    candidate_sources = {
        "approve_split": "split_candidates",
        "reject_split": "split_candidates",
        "plan_merge": "merge_candidates",
        "approve_emergence": "emergence_candidates",
        "reject_emergence": "emergence_candidates",
    }
    for call in tool_calls:
        source_key = candidate_sources.get(call["tool_name"])
        if not source_key:
            continue
        arguments = call.setdefault("arguments", {})
        if arguments.get("candidate_id"):
            continue
        candidates = provisional_bundle.get(source_key) or []
        if candidates:
            arguments["candidate_id"] = candidates[0].get("id")
            arguments["candidate_id_repaired_from_bundle"] = True
    return tool_calls


def _prune_tool_calls(tool_calls: list[dict]) -> list[dict]:
    if not tool_calls:
        return tool_calls
    terminal = [
        call
        for call in tool_calls
        if call["tool_name"] in {"freeze_timeline", "terminate_timeline", "mark_ready_for_report"}
    ]
    if terminal:
        return [terminal[0]]
    seen = set()
    pruned = []
    for call in tool_calls:
        key = (call.get("tool_name"), call.get("idempotency_key"))
        if key in seen:
            continue
        seen.add(key)
        pruned.append(call)
    return pruned[:6]


def _has_tool(tool_calls: list[dict], tool_name: str) -> bool:
    return any(call.get("tool_name") == tool_name for call in tool_calls)


def _prompt_json(value) -> str:
    return json.dumps(value, ensure_ascii=True, default=str)
