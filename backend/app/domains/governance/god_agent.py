from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models
from app.llm.audit import complete_with_audit
from app.llm.prompt_templates import GOD_AGENT_SYSTEM_PROMPT
from app.llm.routing import AuditedLLMRoute
from app.domains.governance.god_tools import VALID_TOOLS
from app.domains.multiverse.runtime_config import branch_policy_for_multiverse


def review_provisional_tick(
    db: Session,
    multiverse: models.Multiverse,
    provisional_bundle: dict,
) -> tuple[dict, models.LLMCall | None]:
    tick_index = provisional_bundle["tick_index"]
    settings = get_settings()
    response, call = complete_with_audit(
        db,
        big_bang_id=multiverse.big_bang_id,
        purpose=f"god_review_{multiverse.id}_tick_{tick_index}",
        model=settings.god_agent_model,
        route=AuditedLLMRoute.GOD_AGENT,
        messages=[
            {
                "role": "system",
                "content": GOD_AGENT_SYSTEM_PROMPT,
            },
            {"role": "user", "content": f"UNTRUSTED provisional tick bundle for review: {provisional_bundle}"},
        ],
        metadata={"max_tokens": 1000, "temperature": 0.2},
    )
    parsed = response.parsed or {}
    tool_calls = _normalize_tool_calls(parsed.get("tool_calls"), multiverse.id, tick_index)
    tool_calls = _attach_candidate_ids(tool_calls, provisional_bundle)
    tool_calls = _prune_tool_calls(tool_calls)
    idle_assessment = provisional_bundle.get("idle_assessment") or {}
    if idle_assessment.get("should_terminate"):
        tool_calls = [{
            "tool_name": "terminate_timeline",
            "arguments": {
                "reason": "Timeline reached idle termination threshold.",
                "idle_assessment": idle_assessment,
            },
            "idempotency_key": f"god:{multiverse.id}:tick:{tick_index}:terminate_idle",
        }]
    branch_score = provisional_bundle.get("branch_score", 0)
    structural_tools = {"create_branch", "approve_split", "plan_merge", "approve_emergence"}
    parsed_decision = str(parsed.get("decision") or "").strip().lower()
    explicit_continue = parsed_decision == "continue" or any(
        call["tool_name"] == "continue_timeline" for call in tool_calls
    )
    has_structural = any(call["tool_name"] in structural_tools for call in tool_calls)
    has_branch = any(call["tool_name"] == "create_branch" for call in tool_calls)
    branch_threshold = _branch_score_threshold(db, multiverse)
    if branch_score >= branch_threshold and not has_branch and (has_structural or not explicit_continue):
        tool_calls.append({
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
        })
    elif not tool_calls:
        tool_calls.append({
            "tool_name": "continue_timeline",
            "arguments": {"reason": "No validated branch trigger in this tick."},
            "idempotency_key": f"god:{multiverse.id}:tick:{tick_index}:continue",
        })
    decision = parsed.get("decision") or ("branch" if branch_score >= branch_threshold else "continue")
    tool_calls = _attach_branch_probabilities(
        tool_calls,
        parsed=parsed,
        branch_score=branch_score,
        branch_threshold=branch_threshold,
    )
    review = {
        "decision": decision,
        "rationale": parsed.get("rationale") or "God Agent reviewed the provisional tick bundle.",
        "confidence": float(parsed.get("confidence", 0.8) or 0.8),
        "tool_calls": tool_calls,
        "endpoint_ledger_updates": parsed.get("endpoint_ledger_updates") if isinstance(parsed.get("endpoint_ledger_updates"), list) else [],
        "endpoint_ledger_summary": parsed.get("endpoint_ledger_summary") or "",
        "input_summary": {
            "multiverse_id": str(multiverse.id),
            "tick_index": tick_index,
            "events": len(provisional_bundle.get("executed_events", [])),
            "branch_score": branch_score,
            "endpoint_ledger_version": (provisional_bundle.get("endpoint_ledger") or {}).get("version"),
            "llm_call_id": str(call.id),
        },
    }
    return review, call


def _branch_score_threshold(db: Session, multiverse: models.Multiverse) -> float:
    settings = get_settings()
    branch_policy = branch_policy_for_multiverse(db, multiverse)
    threshold = branch_policy.get("branch_score_threshold", settings.branch_score_threshold)
    return float(threshold if threshold is not None else settings.branch_score_threshold)


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
    # Conservative fallback: crossing a branch threshold means the branch is plausible,
    # not dominant. Confidence increases trust in the fork signal without turning it
    # into a path probability by itself.
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
    structural_priority = ["approve_split", "plan_merge", "approve_emergence", "create_branch"]
    branch_call = next((call for call in tool_calls if call["tool_name"] == "create_branch"), None)
    for name in structural_priority:
        for call in tool_calls:
            if call["tool_name"] == name and name != "create_branch":
                return [call, branch_call] if branch_call else [call]
    if branch_call:
        return [branch_call]
    return [tool_calls[0]]
