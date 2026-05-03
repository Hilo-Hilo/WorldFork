from __future__ import annotations

import json
import re
from collections import defaultdict

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
) -> dict:
    route = route_for_actor_type(actor.actor_type)
    model = _actor_fallback_model(actor.actor_type)
    actor_prompt_context = _with_actor_event_queue(
        db,
        multiverse=multiverse,
        actor=actor,
        tick_index=tick_index,
        prompt_context=prompt_context,
    )
    response, call = complete_with_audit(
        db,
        big_bang_id=big_bang.id,
        purpose=f"agent_{actor.actor_type}_{actor.id}_tick_{tick_index}",
        model=model,
        route=route,
        messages=[
            {
                "role": "system",
                "content": ACTOR_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": f"Actor: {actor.name}\nArchetype: {actor.archetype}\nContext: {actor_prompt_context}",
            },
        ],
        metadata={
            "max_tokens": 700,
            "temperature": 0.4,
            "agent_type": actor.actor_type,
            "actor_type": actor.actor_type,
            "agent_source": str(route),
            "canonical_job_type": ACTOR_DELIBERATION_JOB_TYPE,
            "actor_id": str(actor.id),
            "actor_name": actor.name,
            "multiverse_id": str(multiverse.id),
            "tick_index": tick_index,
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
    parsed_actions = [{**action, "actor_id": actor.id} for action in social_actions]
    parsed_actions.extend({**{"proposed_event": event}, "actor_id": actor.id} for event in proposed_events)
    emotion_ratings = [{**rating, "actor_id": actor.id} for rating in ratings]
    return {
        "actor_output": {"actor_id": str(actor.id), "llm_call_id": str(call.id), "parsed": parsed},
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
        invalid = _invalid_event_proposals(db, parsed_actions)
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


def _with_actor_event_queue(
    db: Session,
    *,
    multiverse: models.Multiverse,
    actor: models.Actor,
    tick_index: int,
    prompt_context: dict,
) -> dict:
    actor_context = dict(prompt_context or {})
    if not hasattr(db, "scalars"):
        return actor_context
    actor_context["event_queue"] = build_event_queue_prompt_context(
        db,
        multiverse_id=multiverse.id,
        tick_index=tick_index,
        actor_id=actor.id,
    )
    return actor_context


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


def _invalid_event_proposals(db: Session, parsed_actions: list[dict]) -> list[dict]:
    invalid: list[dict] = []
    for action in parsed_actions:
        if not isinstance(action, dict):
            continue
        payload = action.get("proposed_event")
        if not isinstance(payload, dict):
            continue
        actor = db.get(models.Actor, action.get("actor_id")) if action.get("actor_id") else None
        violation = _event_policy_violation(actor, payload)
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
        body = action.get("body") or action.get("content") or f"{action_type} action"
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


def queue_agent_events(db: Session, *, big_bang_id, multiverse_id, tick_index: int, parsed_actions: list[dict]) -> list[dict]:
    queued = []
    for action in parsed_actions:
        if not isinstance(action, dict):
            continue
        event_payload = action.get("proposed_event")
        if not isinstance(event_payload, dict):
            continue
        title = event_payload.get("title") or "Agent proposed event"
        scheduled_tick = _parse_scheduled_tick(event_payload.get("scheduled_tick"), tick_index + 1)
        event = models.Event(
            big_bang_id=big_bang_id,
            multiverse_id=multiverse_id,
            creator_actor_id=action.get("actor_id"),
            event_type=event_payload.get("event_type", "announcement"),
            created_tick=tick_index,
            scheduled_tick=scheduled_tick,
            status="queued",
            title=title,
            description=event_payload.get("description"),
            expected_impact=event_payload.get("expected_impact", {}),
            meta={"source": "agent_proposal"},
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
