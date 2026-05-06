from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models
from app.llm.audit import complete_with_audit
from app.llm.prompt_templates import ACTOR_SYSTEM_PROMPT
from app.llm.routing import AuditedLLMRoute, route_for_actor_type
from app.domains.event.event_engine import build_event_queue_prompt_context


ACTOR_DELIBERATION_JOB_TYPE = "actor_deliberation_call"


class EventValidationError(ValueError):
    def __init__(self, message: str, *, invalid_events: list[dict], attempts: list[dict]) -> None:
        super().__init__(message)
        self.invalid_events = invalid_events
        self.attempts = attempts


def run_agent_decisions(
    db: Session,
    *,
    big_bang: models.BigBang,
    multiverse: models.Multiverse,
    tick_index: int,
    prompt_context: dict,
) -> dict:
    actors = db.scalars(
        select(models.Actor).where(
            models.Actor.big_bang_id == big_bang.id,
            models.Actor.status == "active",
        )
    ).all()
    if not actors:
        return {"actor_outputs": [], "parsed_actions": [], "emotion_self_ratings": []}

    outputs = []
    parsed_actions = []
    emotion_ratings = []
    for actor in actors:
        result = run_actor_decision(
            db,
            big_bang=big_bang,
            multiverse=multiverse,
            actor=actor,
            tick_index=tick_index,
            prompt_context=prompt_context,
        )
        outputs.append(result["actor_output"])
        parsed_actions.extend(result["parsed_actions"])
        emotion_ratings.extend(result["emotion_self_ratings"])
    return {
        "actor_outputs": outputs,
        "parsed_actions": parsed_actions,
        "emotion_self_ratings": emotion_ratings,
    }


def run_actor_decision(
    db: Session,
    *,
    big_bang: models.BigBang,
    multiverse: models.Multiverse,
    actor: models.Actor,
    tick_index: int,
    prompt_context: dict,
    release_db_connection_before_llm: bool = False,
) -> dict:
    big_bang_id = big_bang.id
    actor_id = actor.id
    actor_type = actor.actor_type
    actor_name = actor.name
    actor_archetype = actor.archetype
    multiverse_id = multiverse.id
    route = route_for_actor_type(actor_type)
    model = _actor_fallback_model(actor_type)
    base_prompt_context = dict(prompt_context or {})
    if "event_queue" not in base_prompt_context and hasattr(db, "scalars"):
        base_prompt_context["event_queue"] = build_event_queue_prompt_context(
            db,
            multiverse_id=multiverse.id,
            tick_index=tick_index,
        )
    shared_prompt_context = _shared_actor_prompt_context(base_prompt_context)
    actor_prompt_context = _actor_specific_prompt_context(
        db,
        multiverse=multiverse,
        actor=actor,
        tick_index=tick_index,
        prompt_context=base_prompt_context,
    )
    if release_db_connection_before_llm and isinstance(db, Session):
        db.commit()
    response, call = complete_with_audit(
        db,
        big_bang_id=big_bang_id,
        purpose=f"agent_{actor_type}_{actor_id}_tick_{tick_index}",
        model=model,
        route=route,
        messages=[
            {
                "role": "system",
                "content": ACTOR_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    "Shared tick context for all actor decisions in this tick. "
                    f"It is identical across actors for prompt-cache reuse:\n{_render_actor_shared_context(shared_prompt_context)}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Actor-specific deliberation request:\n"
                    f"Actor: {actor_name}\n"
                    f"Archetype: {_prompt_json(_compact_prompt_value(actor_archetype))}\n"
                    f"Actor context: {_prompt_json(actor_prompt_context)}"
                ),
            },
        ],
        metadata={
            "max_tokens": 700,
            "temperature": 0.4,
            "agent_type": actor_type,
            "actor_type": actor_type,
            "agent_source": str(route),
            "canonical_job_type": ACTOR_DELIBERATION_JOB_TYPE,
            "actor_id": str(actor_id),
            "actor_name": actor_name,
            "multiverse_id": str(multiverse_id),
            "tick_index": tick_index,
            "prompt_cache_strategy": "openrouter_implicit_sticky",
            "prompt_cache_stable_prefix_messages": 2,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
    )
    parsed = response.parsed if isinstance(response.parsed, dict) else {}
    social_actions = _normalize_social_actions(parsed)
    proposed_events = _normalize_event_actions(parsed)
    ratings = _normalize_self_ratings(parsed)
    if not social_actions and not proposed_events and not ratings:
        social_actions = [
            {
                "action_type": "post",
                "body": f"{actor.name} reacts to the current tick.",
                "channel": "oasis",
            }
        ]
        ratings = [{"emotion": "uncertainty", "value": 4}]
    parsed_actions = [{**action, "actor_id": actor_id} for action in social_actions]
    parsed_actions.extend({**{"proposed_event": event}, "actor_id": actor_id} for event in proposed_events)
    emotion_ratings = [{**rating, "actor_id": actor_id} for rating in ratings]
    return {
        "actor_output": {"actor_id": str(actor_id), "llm_call_id": str(call.id), "parsed": parsed},
        "parsed_actions": parsed_actions,
        "emotion_self_ratings": emotion_ratings,
        "llm_call_id": str(call.id),
        "parsed": parsed,
    }


def validate_and_repair_event_actions(
    db: Session,
    *,
    big_bang: models.BigBang,
    multiverse: models.Multiverse,
    tick_index: int,
    prompt_context: dict,
    agent_result: dict,
    max_retries: int = 3,
) -> dict:
    """Validate proposed events before they are committed to the event queue.

    The gate is intentionally deterministic: hard society rules should not be
    negotiated by the same actor that proposed the event. Invalid actors are
    given targeted feedback and rerun up to ``max_retries`` times.
    """

    parsed_actions = list(agent_result.get("parsed_actions") or [])
    actor_outputs = list(agent_result.get("actor_outputs") or [])
    emotion_ratings = list(agent_result.get("emotion_self_ratings") or [])
    validation_attempts: list[dict] = []

    for retry_round in range(0, max_retries + 1):
        invalid = _invalid_event_proposals(
            db,
            parsed_actions,
            big_bang=big_bang,
            prompt_context=prompt_context,
        )
        validation_attempts.append(
            {
                "round": retry_round,
                "invalid_count": len(invalid),
                "invalid_events": invalid,
            }
        )
        if not invalid:
            return {
                **agent_result,
                "parsed_actions": parsed_actions,
                "actor_outputs": actor_outputs,
                "emotion_self_ratings": emotion_ratings,
                "event_validation": {
                    "status": "passed",
                    "max_retries": max_retries,
                    "attempts": validation_attempts,
                },
            }
        if retry_round >= max_retries:
            titles = ", ".join(item.get("title") or "untitled event" for item in invalid[:3])
            raise EventValidationError(
                "God event sanity gate rejected proposed events after "
                f"{max_retries} actor retries: {titles}",
                invalid_events=invalid,
                attempts=validation_attempts,
            )

        _log_rejected_event_attempts(
            db,
            big_bang_id=big_bang.id,
            multiverse_id=multiverse.id,
            tick_index=tick_index,
            invalid=invalid,
            final=False,
        )
        invalid_by_actor: dict[str, list[dict]] = defaultdict(list)
        for item in invalid:
            if item.get("actor_id"):
                invalid_by_actor[str(item["actor_id"])].append(item)
        for actor_id, actor_invalid in invalid_by_actor.items():
            actor = db.get(models.Actor, actor_id)
            if actor is None:
                continue
            actor_outputs = [
                output
                for output in actor_outputs
                if str((output or {}).get("actor_id")) != actor_id
            ]
            emotion_ratings = [
                rating
                for rating in emotion_ratings
                if str((rating or {}).get("actor_id")) != actor_id
            ]
            retry_context = {
                **(prompt_context or {}),
                "event_validation_feedback": {
                    "source": "god_event_sanity_gate",
                    "retry_round": retry_round + 1,
                    "blocked_events": actor_invalid,
                    "instructions": (
                        "Do not repeat blocked proposed events. They violate hardcoded society rules "
                        "or exceed this actor's authority. Replace them with plausible pressure, request, "
                        "communication, organizing, or no-event actions that this actor can actually cause."
                    ),
                },
            }
            retry = run_actor_decision(
                db,
                big_bang=big_bang,
                multiverse=multiverse,
                actor=actor,
                tick_index=tick_index,
                prompt_context=retry_context,
            )
            parsed_actions = [
                action
                for action in parsed_actions
                if str(action.get("actor_id")) != actor_id
            ]
            parsed_actions.extend(retry.get("parsed_actions") or [])
            actor_outputs.append(
                {
                    **(retry.get("actor_output") or {}),
                    "event_validation_retry_round": retry_round + 1,
                }
            )
            emotion_ratings.extend(retry.get("emotion_self_ratings") or [])

    return agent_result


def _shared_actor_prompt_context(prompt_context: dict) -> dict:
    """Keep the cacheable actor prefix shared; actor-only feedback belongs later."""

    if not isinstance(prompt_context, dict):
        return {}
    return {
        key: value
        for key, value in prompt_context.items()
        if key not in {"event_validation_feedback"}
    }


def _actor_specific_prompt_context(
    db: Session,
    *,
    multiverse: models.Multiverse,
    actor: models.Actor,
    tick_index: int,
    prompt_context: dict,
) -> dict:
    actor_context: dict = {}
    if isinstance(prompt_context, dict) and prompt_context.get("event_validation_feedback"):
        actor_context["event_validation_feedback"] = _compact_prompt_value(prompt_context["event_validation_feedback"])
    if not hasattr(db, "scalars"):
        return actor_context
    event_queue = build_event_queue_prompt_context(
        db,
        multiverse_id=multiverse.id,
        tick_index=tick_index,
        actor_id=actor.id,
    )
    actor_context["actor_event_queue"] = {
        "current_tick": event_queue.get("current_tick"),
        "own_queued_events": event_queue.get("own_queued_events") or [],
        "prompt_budget": {
            "kind": "actor_specific_event_queue",
            "source": "global due/past/upcoming events are in the shared tick context",
            "own_queued_events": (event_queue.get("prompt_budget") or {}).get("sections", {}).get("own_queued_events", {}),
        },
    }
    return actor_context


def _render_actor_shared_context(prompt_context: dict) -> str:
    if not isinstance(prompt_context, dict):
        return "CLOCK: not provided"
    lines: list[str] = []
    lines.append(f"CLOCK: {_one_line(prompt_context.get('clock') or 'not provided')}")
    forecast_lines = _forecast_context_lines(prompt_context)
    if forecast_lines:
        lines.append("FORECAST:")
        lines.extend(f"- {line}" for line in forecast_lines)
    branch = _branch_context(prompt_context)
    if branch:
        lines.append("BRANCH:")
        lines.append(f"- {_one_line(branch.get('branch_premise') or branch.get('prompt_instruction') or branch)}")
    state_lines = _state_context_lines(prompt_context)
    if state_lines:
        lines.append("STATE:")
        lines.extend(f"- {line}" for line in state_lines)
    event_queue = prompt_context.get("event_queue") if isinstance(prompt_context.get("event_queue"), dict) else {}
    if event_queue:
        _append_event_section(lines, "DUE EVENTS", event_queue.get("due_events"))
        _append_event_section(lines, "UPCOMING", event_queue.get("upcoming_events"), limit=8)
        _append_event_section(lines, "PAST EVENTS", event_queue.get("past_events"), limit=8)
        budget = event_queue.get("prompt_budget") if isinstance(event_queue.get("prompt_budget"), dict) else {}
        omitted = budget.get("omitted_total")
        if omitted:
            lines.append(f"EVENT QUEUE WINDOW: {omitted} older/lower-priority rows omitted; use visible sections only.")
    influences = prompt_context.get("sociology_prompt_influences")
    if isinstance(influences, list) and influences:
        lines.append("SOCIOLOGY:")
        for index, item in enumerate(influences[:6], start=1):
            lines.append(f"{index}. {_one_line(item, limit=260)}")
    policy = prompt_context.get("untrusted_content_policy")
    if policy:
        lines.append(f"POLICY: {_one_line(policy, limit=360)}")
    return "\n".join(lines)


def _forecast_context_lines(prompt_context: dict) -> list[str]:
    lines: list[str] = []
    forecast_clock = prompt_context.get("forecast_clock")
    if isinstance(forecast_clock, dict) and forecast_clock:
        parts = []
        for key in (
            "as_of_date",
            "forecast_deadline_date",
            "current_tick",
            "deadline_tick",
            "deadline_tick_reached",
            "estimated_current_date",
        ):
            if forecast_clock.get(key) is not None:
                parts.append(f"{key}={forecast_clock[key]}")
        if parts:
            lines.append("; ".join(parts))
        if forecast_clock.get("forecast_horizon"):
            lines.append(f"horizon={_one_line(forecast_clock['forecast_horizon'], limit=240)}")
    state = prompt_context.get("current_state") if isinstance(prompt_context.get("current_state"), dict) else {}
    scenario = state.get("scenario_summary") if isinstance(state.get("scenario_summary"), dict) else {}
    if scenario.get("scenario_text_excerpt"):
        lines.append(f"scenario={_one_line(scenario['scenario_text_excerpt'], limit=360)}")
    if scenario.get("simulation_brief"):
        lines.append(f"brief={_one_line(scenario['simulation_brief'], limit=260)}")
    return lines


def _branch_context(prompt_context: dict) -> dict[str, Any]:
    state = prompt_context.get("current_state") if isinstance(prompt_context.get("current_state"), dict) else {}
    branch = state.get("branch_context")
    return branch if isinstance(branch, dict) else {}


def _state_context_lines(prompt_context: dict) -> list[str]:
    state = prompt_context.get("current_state") if isinstance(prompt_context.get("current_state"), dict) else {}
    lines: list[str] = []
    if state.get("last_tick_index") is not None:
        lines.append(f"last_tick={state['last_tick_index']}")
    graph = state.get("graph_summary")
    if isinstance(graph, dict) and graph:
        lines.append(f"graph={_one_line(graph, limit=320)}")
    for section in ("cohorts", "heroes"):
        rows = state.get(section)
        if isinstance(rows, list) and rows:
            labels = [
                str(row.get("name") or row.get("actor_name") or row.get("id") or row)[:80]
                for row in rows[:6]
                if isinstance(row, dict)
            ]
            if labels:
                lines.append(f"{section}={'; '.join(labels)}")
    return lines


def _append_event_section(lines: list[str], title: str, rows: Any, *, limit: int = 12) -> None:
    if not isinstance(rows, list) or not rows:
        return
    lines.append(f"{title}:")
    for index, row in enumerate([item for item in rows if isinstance(item, dict)][:limit], start=1):
        lines.append(f"{index}. {_event_line(row)}")


def _event_line(row: dict[str, Any]) -> str:
    tick = row.get("scheduled_tick")
    tick_text = f"T{tick}" if tick is not None else "T?"
    title = row.get("title") or "untitled"
    event_type = row.get("event_type") or "event"
    status = row.get("status")
    impact = row.get("actual_impact") or row.get("expected_impact") or {}
    parts = [tick_text, str(event_type), _one_line(title, limit=160)]
    if status:
        parts.append(f"status={status}")
    if impact:
        parts.append(f"impact={_one_line(impact, limit=220)}")
    return " | ".join(parts)


def _one_line(value: Any, *, limit: int = 700) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(_compact_prompt_value(value), sort_keys=True, separators=(",", ":"), default=str)
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[:limit] + "..."


def _prompt_json(value) -> str:  # noqa: ANN001
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _compact_prompt_value(value, *, depth: int = 0, max_items: int = 10, string_limit: int = 700):  # noqa: ANN001
    if depth > 4:
        return _excerpt_text(str(value), 240)
    if isinstance(value, dict):
        compact = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                compact["_truncated_keys"] = len(value) - max_items
                break
            compact[str(key)] = _compact_prompt_value(
                item,
                depth=depth + 1,
                max_items=max_items,
                string_limit=string_limit,
            )
        return compact
    if isinstance(value, list):
        items = [
            _compact_prompt_value(item, depth=depth + 1, max_items=max_items, string_limit=string_limit)
            for item in value[:max_items]
        ]
        if len(value) > max_items:
            items.append({"_truncated_items": len(value) - max_items})
        return items
    if isinstance(value, str):
        return _excerpt_text(value, string_limit)
    return value


def _excerpt_text(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit] + "..."


DIRECT_AUTHORITY_PATTERNS = (
    (
        "legislation_direct_action",
        re.compile(r"\b(pass|enact|repeal|amend|sign|veto)\b.{0,50}\b(law|bill|legislation|statute|ordinance)\b", re.I),
    ),
    (
        "court_direct_action",
        re.compile(r"\b(issue|grant|deny|vacate|enforce)\b.{0,50}\b(order|injunction|ruling|judgment|warrant)\b", re.I),
    ),
    (
        "election_certification_direct_action",
        re.compile(r"\b(certify|decertify|overturn|reject)\b.{0,50}\b(election|results?|ballots?|certification)\b", re.I),
    ),
    (
        "policing_direct_action",
        re.compile(r"\b(deploy|arrest|detain|raid|charge)\b.{0,50}\b(police|guard|troops|prosecutors?|officers?)\b", re.I),
    ),
    (
        "corporate_control_direct_action",
        re.compile(r"\b(fire|appoint|remove|replace|mandate|adopt)\b.{0,50}\b(ceo|executive|board|company policy|pricing|terms)\b", re.I),
    ),
    (
        "regulatory_direct_action",
        re.compile(r"\b(ban|license|fine|regulate|approve|block)\b.{0,50}\b(platform|merger|product|service|company|agency)\b", re.I),
    ),
)

PRESSURE_ACTION_HINTS = re.compile(
    r"\b(ask|urge|petition|protest|request|call for|pressure|boycott|organize|testify|warn|demand)\b",
    re.I,
)

AUTHORITY_ACTOR_TYPES = {
    "institution",
    "regulator",
    "court",
    "government",
    "agency",
    "company",
    "employer",
    "board",
    "official",
}


def _invalid_event_proposals(
    db: Session,
    parsed_actions: list[dict],
    *,
    big_bang: models.BigBang | None = None,
    prompt_context: dict | None = None,
) -> list[dict]:
    invalid: list[dict] = []
    for action in parsed_actions:
        if not isinstance(action, dict):
            continue
        payload = action.get("proposed_event")
        if not isinstance(payload, dict):
            continue
        actor = db.get(models.Actor, action.get("actor_id")) if action.get("actor_id") else None
        violation = _event_policy_violation(actor, payload) or _forecast_terminal_event_violation(
            big_bang,
            prompt_context,
            payload,
        )
        if violation is None:
            continue
        invalid.append(
            {
                "actor_id": str(action.get("actor_id")) if action.get("actor_id") else None,
                "actor_name": actor.name if actor else None,
                "actor_type": actor.actor_type if actor else None,
                "title": payload.get("title"),
                "event_type": payload.get("event_type"),
                "rule_id": violation["rule_id"],
                "reason": violation["reason"],
                "guidance": violation["guidance"],
            }
        )
    return invalid


def _event_policy_violation(actor: models.Actor | None, payload: dict) -> dict | None:
    text = _event_policy_text(payload)
    if not text:
        return None
    for rule_id, pattern in DIRECT_AUTHORITY_PATTERNS:
        if not pattern.search(text):
            continue
        if _actor_has_direct_authority(actor, rule_id):
            return None
        if _is_indirect_pressure_event(payload, text):
            return None
        return {
            "rule_id": rule_id,
            "reason": "Proposed event directly changes institutional state outside the actor's encoded authority.",
            "guidance": (
                "Use an indirect pressure/request/organizing event, or have an actor with encoded authority "
                "make the direct decision."
            ),
        }
    return None


FORECAST_TERMINAL_EVENT_TYPES = {
    "announcement",
    "award_announcement",
    "certification",
    "commercial_milestone",
    "court_order",
    "decision",
    "launch",
    "policy_decision",
    "release",
    "result",
    "ruling",
}

FORECAST_TERMINAL_HINTS = re.compile(
    r"\b("
    r"official announcement|announces?|announcement|certif(?:y|ies|ication)|decision announced|"
    r"final result|forecast (?:question|endpoint)|resolves? (?:the )?forecast|winner|laureate|"
    r"released?|launch(?:ed)?|available|target range|court orders?|fine(?:d)?|merger closes?"
    r")\b",
    re.I,
)

FORECAST_ANNOUNCEMENT_OUTCOME_HINTS = re.compile(
    r"\b("
    r"final result|forecast (?:question|endpoint|result)|resolves? (?:the )?forecast|"
    r"winner|wins?|won|laureate|named|selected|award goes to|best picture|"
    r"certif(?:y|ies|ication)|decision announced|target range|court orders?|"
    r"fine(?:d)?|merger closes?"
    r")\b",
    re.I,
)

FORECAST_PROCESS_ANNOUNCEMENT_HINTS = re.compile(
    r"\b(process|protocol|integrity|voting|pre-ceremony|statement|safeguards?|procedures?|tabulation)\b",
    re.I,
)

FORECAST_DECISIVE_ANNOUNCEMENT_HINTS = re.compile(
    r"\b(?:announces?|declares?|names?|confirms?|selects?|awards?|reveals?)\b.{0,100}"
    r"\b(?:winner|laureate|best picture|forecast result)\b",
    re.I | re.S,
)

CONDITIONAL_BINARY_PLACEHOLDER = re.compile(
    r"\bif\b.{0,160}\byes\b.{0,160}\b(?:otherwise|else|if not|no)\b",
    re.I | re.S,
)


def _forecast_terminal_event_violation(
    big_bang: models.BigBang | None,
    prompt_context: dict | None,
    payload: dict,
) -> dict | None:
    if not _deadline_aware_binary_forecast(big_bang):
        return None
    text = _event_policy_text(payload)
    if not _looks_like_forecast_terminal_event(payload, text):
        return None
    deadline_tick = _forecast_deadline_tick(big_bang, prompt_context)
    scheduled_tick = _parse_scheduled_tick(payload.get("scheduled_tick"), -1)
    if deadline_tick is not None and scheduled_tick >= 0 and scheduled_tick < deadline_tick and "forecast" not in text.lower():
        return None
    candidate_marker = _candidate_endpoint_marker(payload)
    branch_marker = _branch_candidate_endpoint_marker(prompt_context)
    if candidate_marker in {"yes", "no"}:
        if branch_marker in {"yes", "no"} and candidate_marker != branch_marker:
            return {
                "rule_id": "forecast_terminal_event_branch_candidate_conflict",
                "reason": (
                    f"Terminal event resolves candidate {candidate_marker}, but this branch premise expected "
                    f"{branch_marker}."
                ),
                "guidance": (
                    f"This branch is committed to candidate_endpoint_id={branch_marker}. Terminal settlement "
                    "events must either match that candidate or be non-terminal process/commentary events."
                ),
            }
        return None
    return {
        "rule_id": "forecast_terminal_event_missing_candidate_endpoint",
        "reason": (
            "Deadline-aware forecast terminal events must commit to an explicit yes/no candidate endpoint. "
            "Conditional placeholders leave the endpoint ledger unable to settle."
        ),
        "guidance": (
            'Set expected_impact.candidate_endpoint_id to "yes" or "no" and state the simulated authority '
            "outcome directly. Do not write placeholders such as 'if Candidate A is named, yes; otherwise no'."
        ),
    }


def _deadline_aware_binary_forecast(big_bang: models.BigBang | None) -> bool:
    scenario = big_bang.scenario_input if big_bang is not None and isinstance(big_bang.scenario_input, dict) else {}
    metadata = scenario.get("forecast_metadata") if isinstance(scenario.get("forecast_metadata"), dict) else {}
    if metadata.get("tick_horizon_policy") not in {None, "deadline_aware"}:
        return False
    candidates = scenario.get("candidate_endpoints") if isinstance(scenario.get("candidate_endpoints"), list) else []
    ids = {str(item.get("id") or item.get("endpoint_key") or "").strip().lower() for item in candidates if isinstance(item, dict)}
    return {"yes", "no"}.issubset(ids)


def _forecast_deadline_tick(big_bang: models.BigBang | None, prompt_context: dict | None) -> int | None:
    clock = prompt_context.get("forecast_clock") if isinstance(prompt_context, dict) else {}
    if isinstance(clock, dict) and clock.get("deadline_tick") is not None:
        return _parse_scheduled_tick(clock.get("deadline_tick"), -1)
    scenario = big_bang.scenario_input if big_bang is not None and isinstance(big_bang.scenario_input, dict) else {}
    metadata = scenario.get("forecast_metadata") if isinstance(scenario.get("forecast_metadata"), dict) else {}
    if metadata.get("deadline_tick") is not None:
        return _parse_scheduled_tick(metadata.get("deadline_tick"), -1)
    return None


def _looks_like_forecast_terminal_event(payload: dict, text: str) -> bool:
    event_type = str(payload.get("event_type") or "").strip().lower()
    if CONDITIONAL_BINARY_PLACEHOLDER.search(text) is not None or re.search(
        r"\b(?:forecast (?:question|endpoint)|resolves? (?:the )?forecast)\b",
        text,
        re.I,
    ) is not None:
        return True
    if event_type not in FORECAST_TERMINAL_EVENT_TYPES:
        return False
    if _candidate_endpoint_marker(payload) in {"yes", "no"}:
        return True
    if event_type == "announcement":
        if FORECAST_PROCESS_ANNOUNCEMENT_HINTS.search(text) and not FORECAST_DECISIVE_ANNOUNCEMENT_HINTS.search(text):
            return False
        return FORECAST_ANNOUNCEMENT_OUTCOME_HINTS.search(text) is not None
    return FORECAST_TERMINAL_HINTS.search(text) is not None


def _candidate_endpoint_marker(payload: dict) -> str | None:
    markers = set()
    for container in _candidate_marker_containers(payload):
        for key in ("candidate_endpoint_id", "endpoint", "outcome", "result"):
            marker = _normalize_candidate_marker(container.get(key))
            if marker:
                markers.add(marker)
    text = _event_policy_text(payload)
    if not CONDITIONAL_BINARY_PLACEHOLDER.search(text):
        for marker in ("yes", "no"):
            if re.search(rf"\bforecast(?: question| endpoint)?\b.{{0,120}}\b(?:as|to|=)\s*{marker}\b", text, re.I):
                markers.add(marker)
            if re.search(rf"\bcandidate_endpoint_id\b.{{0,40}}\b{marker}\b", text, re.I):
                markers.add(marker)
    return markers.pop() if len(markers) == 1 else None


def _candidate_marker_containers(payload: dict) -> list[dict]:
    containers = [payload]
    expected = payload.get("expected_impact")
    if isinstance(expected, dict):
        containers.append(expected)
    meta = payload.get("meta")
    if isinstance(meta, dict):
        containers.append(meta)
    return containers


def _normalize_candidate_marker(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return text if text in {"yes", "no"} else None


def _branch_candidate_endpoint_marker(prompt_context: dict | None) -> str | None:
    if not isinstance(prompt_context, dict):
        return None
    current_state = prompt_context.get("current_state")
    if not isinstance(current_state, dict):
        return None
    branch_context = current_state.get("branch_context")
    if not isinstance(branch_context, dict):
        return None
    containers = [branch_context]
    probability_basis = branch_context.get("probability_basis")
    if isinstance(probability_basis, dict):
        containers.append(probability_basis)
    for container in containers:
        marker = _normalize_candidate_marker(container.get("candidate_endpoint_id") or container.get("candidate_id"))
        if marker:
            return marker
    premise = str(branch_context.get("branch_premise") or branch_context.get("reason") or "").strip().lower()
    if premise.startswith("yes path"):
        return "yes"
    if premise.startswith("no path"):
        return "no"
    return None


RULE_AUTHORITY_HINTS = {
    "legislation_direct_action": {
        "actor_types": {"government", "legislature", "legislator", "official"},
        "tokens": {
            "legislative_authority",
            "can_enact_law",
            "legislature",
            "legislator",
            "lawmaker",
            "parliament",
            "congress",
            "city_council",
            "mayor",
            "governor",
        },
    },
    "court_direct_action": {
        "actor_types": {"court", "judge", "judiciary"},
        "tokens": {"judicial_authority", "can_issue_orders", "court", "judge", "judicial", "tribunal"},
    },
    "election_certification_direct_action": {
        "actor_types": {"election_official", "official"},
        "tokens": {"election_authority", "can_certify_elections", "certification", "canvassing", "secretary of state"},
    },
    "policing_direct_action": {
        "actor_types": {"police", "law_enforcement", "government", "official"},
        "tokens": {"policing_authority", "law_enforcement", "police", "prosecutor", "guard", "troops"},
    },
    "corporate_control_direct_action": {
        "actor_types": {"company", "employer", "board", "executive"},
        "tokens": {"corporate_authority", "employment_authority", "board", "executive", "employer", "management"},
    },
    "regulatory_direct_action": {
        "actor_types": {"regulator", "agency", "government", "official"},
        "tokens": {"regulatory_authority", "can_regulate", "regulator", "agency", "commission", "ministry", "department"},
    },
}

INDIRECT_EVENT_TYPES = {
    "advocacy",
    "announcement",
    "boycott",
    "communication",
    "organizing",
    "petition",
    "pressure",
    "protest",
    "request",
    "testimony",
    "warning",
}

AUTHORITY_TARGET_HINTS = re.compile(
    r"\b(lawmakers?|legislators?|officials?|court|courts|judge|judges|agency|agencies|"
    r"regulators?|board|company|employer|government|council|parliament)\b",
    re.I,
)


def _actor_has_direct_authority(actor: models.Actor | None, rule_id: str) -> bool:
    if actor is None:
        return False
    actor_type = str(actor.actor_type or "").lower()
    hints = RULE_AUTHORITY_HINTS.get(rule_id, {})
    actor_types = hints.get("actor_types") or set()
    archetype = actor.archetype if isinstance(actor.archetype, dict) else {}
    authority_text = json.dumps(archetype, sort_keys=True, default=str).lower()
    domain_authority = any(str(token).lower() in authority_text for token in hints.get("tokens") or set())
    if actor_type in actor_types and (actor_type != "official" or domain_authority):
        return True
    try:
        direct_event_power = float(archetype.get("direct_event_power") or 0.0)
    except (TypeError, ValueError):
        direct_event_power = 0.0
    if direct_event_power >= 0.8 and domain_authority:
        return True
    return domain_authority


def _is_indirect_pressure_event(payload: dict, text: str) -> bool:
    event_type = str(payload.get("event_type") or "").strip().lower()
    title = str(payload.get("title") or "")
    if event_type in {
        "legislation",
        "court_order",
        "ruling",
        "certification",
        "regulation",
        "enforcement",
        "corporate_decision",
    }:
        return False
    has_pressure = PRESSURE_ACTION_HINTS.search(text) is not None
    has_authority_target = AUTHORITY_TARGET_HINTS.search(text) is not None
    if event_type in INDIRECT_EVENT_TYPES:
        return has_pressure and has_authority_target
    return PRESSURE_ACTION_HINTS.search(title) is not None and has_authority_target


def _event_policy_text(payload: dict) -> str:
    parts = [
        payload.get("title"),
        payload.get("event_type"),
        payload.get("description"),
        payload.get("expected_impact"),
        payload.get("why"),
        payload.get("rationale"),
    ]
    return " ".join(json.dumps(item, default=str) if isinstance(item, dict) else str(item or "") for item in parts)


def _log_rejected_event_attempts(
    db: Session,
    *,
    big_bang_id,
    multiverse_id,
    tick_index: int,
    invalid: list[dict],
    final: bool,
) -> None:
    for item in invalid:
        db.add(
            models.OperationLog(
                big_bang_id=big_bang_id,
                multiverse_id=multiverse_id,
                event_type="god_event_sanity_rejected",
                level="error" if final else "warning",
                body={"tick_index": tick_index, "final": final, **item},
            )
        )
    db.flush()


def _actor_fallback_model(actor_type: str | None) -> str:
    settings = get_settings()
    route = route_for_actor_type(actor_type)
    if route == AuditedLLMRoute.HERO_AGENT:
        return settings.hero_agent_model
    return settings.cohort_agent_model


def apply_social_actions(db: Session, *, big_bang_id, multiverse_id, tick_index: int, parsed_actions: list[dict]) -> list[dict]:
    observations = []
    for action in parsed_actions:
        if not isinstance(action, dict):
            continue
        actor_id = action.get("actor_id")
        if "proposed_event" in action:
            continue
        action_type = action.get("action_type", "post")
        body = _social_post_body(action.get("body") or action.get("content") or f"{action_type} action")
        post = models.SocialPost(
            big_bang_id=big_bang_id,
            multiverse_id=multiverse_id,
            tick_index=tick_index,
            actor_id=actor_id,
            channel=action.get("channel", "oasis"),
            body=body,
            meta={"source": "agent_decision", "action": _jsonable(action)},
        )
        oasis = models.OASISAction(
            big_bang_id=big_bang_id,
            multiverse_id=multiverse_id,
            tick_index=tick_index,
            actor_id=actor_id,
            action_type=action_type,
            payload=_jsonable(action),
        )
        db.add_all([post, oasis])
        observations.append({"post": body, "action_type": action_type, "actor_id": str(actor_id) if actor_id else None})
    db.flush()
    return observations


def _social_post_body(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(_jsonable(value), ensure_ascii=False, sort_keys=True)
    return str(value)


def queue_agent_events(
    db: Session,
    *,
    big_bang_id,
    multiverse_id,
    tick_index: int,
    parsed_actions: list[dict],
    max_scheduled_tick: int | None = None,
) -> list[dict]:
    queued = []
    for action in parsed_actions:
        if not isinstance(action, dict):
            continue
        event_payload = action.get("proposed_event")
        if not isinstance(event_payload, dict):
            continue
        title = event_payload.get("title") or "Agent proposed event"
        scheduled_tick = _parse_scheduled_tick(event_payload.get("scheduled_tick"), tick_index + 1)
        if max_scheduled_tick is not None and scheduled_tick > max_scheduled_tick:
            continue
        expected_impact = event_payload.get("expected_impact", {})
        expected_impact = expected_impact if isinstance(expected_impact, dict) else {"summary": expected_impact}
        candidate_endpoint_id = _candidate_endpoint_marker(event_payload)
        if candidate_endpoint_id:
            expected_impact = {**expected_impact, "candidate_endpoint_id": candidate_endpoint_id}
        meta = {"source": "agent_proposal"}
        if candidate_endpoint_id:
            meta["candidate_endpoint_id"] = candidate_endpoint_id
        event = models.Event(
            big_bang_id=big_bang_id,
            multiverse_id=multiverse_id,
            creator_actor_id=action.get("actor_id"),
            event_type=_event_type_or_default(event_payload.get("event_type")),
            created_tick=tick_index,
            scheduled_tick=scheduled_tick,
            status="queued",
            title=title,
            description=event_payload.get("description"),
            expected_impact=expected_impact,
            meta=meta,
        )
        db.add(event)
        db.flush()
        revision = models.EventRevision(
            event_id=event.id,
            revision_number=1,
            edited_by_actor_id=action.get("actor_id"),
            edited_by_agent_type="actor",
            edit_reason="initial_agent_proposal",
            title=event.title,
            description=event.description,
            scheduled_tick=event.scheduled_tick,
            preconditions=event_payload.get("preconditions", {}),
            expected_impact=event.expected_impact,
        )
        db.add(revision)
        db.flush()
        event.current_revision_id = revision.id
        queued.append({"event_id": str(event.id), "title": title, "scheduled_tick": scheduled_tick})
    db.flush()
    return queued


def _event_type_or_default(value: Any) -> str:
    text = str(value or "").strip()
    return text or "announcement"


def _ensure_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _dict_items(value) -> list[dict]:
    return [item for item in _ensure_list(value) if isinstance(item, dict)]


def _normalize_social_actions(parsed: dict) -> list[dict]:
    actions: list[dict] = []
    for item in _dict_items(parsed.get("social_actions")):
        tool_id = item.get("tool_id")
        args = item.get("args")
        if isinstance(tool_id, str) and isinstance(args, dict):
            actions.append(_tool_call_to_social_action(tool_id, args))
        else:
            actions.append(item)
    return actions


def _normalize_event_actions(parsed: dict) -> list[dict]:
    events = _dict_items(parsed.get("proposed_events"))
    for item in _dict_items(parsed.get("event_actions")):
        tool_id = item.get("tool_id")
        args = item.get("args")
        if tool_id == "queue_event" and isinstance(args, dict):
            events.append(args)
    return events


def _normalize_self_ratings(parsed: dict) -> list[dict]:
    ratings = _rating_items(parsed.get("emotion_self_ratings"))
    self_ratings = parsed.get("self_ratings")
    if isinstance(self_ratings, dict):
        emotions = self_ratings.get("emotions")
        if isinstance(emotions, dict):
            ratings.extend({"emotion": key, "value": value} for key, value in emotions.items())
    return _rating_items(ratings)


def _tool_call_to_social_action(tool_id: str, args: dict) -> dict:
    if tool_id == "create_social_post":
        return {
            "action_type": "post",
            "body": args.get("content") or args.get("body") or "",
            "content": args.get("content"),
            "channel": args.get("platform") or args.get("channel") or "oasis",
            "tool_id": tool_id,
            "args": dict(args),
        }
    return {
        "action_type": tool_id,
        "body": args.get("content") or args.get("body") or f"{tool_id} action",
        "channel": args.get("platform") or args.get("channel") or "oasis",
        "tool_id": tool_id,
        "args": dict(args),
    }


def _rating_items(value) -> list[dict]:
    ratings = []
    for item in _ensure_list(value):
        if not isinstance(item, dict):
            continue
        ratings.append(
            {
                **item,
                "emotion": item.get("emotion") or item.get("emotion_key") or "uncertainty",
                "value": _parse_float(item.get("value"), 0.0, low=0.0, high=10.0),
            }
        )
    return ratings


def _jsonable(value):
    if isinstance(value, dict):
        return {key: _jsonable(val) for key, val in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    return str(value) if hasattr(value, "hex") else value


def _parse_scheduled_tick(value, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    match = re.search(r"\d+", str(value))
    if match:
        return max(0, int(match.group(0)))
    return default


def _parse_float(value, default: float, *, low: float | None = None, high: float | None = None) -> float:
    if value is None:
        parsed = default
    else:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            match = re.search(r"-?\d+(?:\.\d+)?", str(value))
            parsed = float(match.group(0)) if match else default
    if low is not None:
        parsed = max(low, parsed)
    if high is not None:
        parsed = min(high, parsed)
    return parsed
