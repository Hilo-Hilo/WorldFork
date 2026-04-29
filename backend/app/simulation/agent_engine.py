from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models
from app.llm.audit import complete_with_audit
from app.llm.prompt_templates import ACTOR_SYSTEM_PROMPT


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
    model = _agent_deliberation_model(db)
    response, call = complete_with_audit(
        db,
        big_bang_id=big_bang.id,
        purpose=f"agent_{actor.actor_type}_{actor.id}_tick_{tick_index}",
        model=model,
        messages=[
            {
                "role": "system",
                "content": ACTOR_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": f"Actor: {actor.name}\nArchetype: {actor.archetype}\nContext: {prompt_context}",
            },
        ],
        metadata={"max_tokens": 700, "temperature": 0.4, "agent_type": actor.actor_type},
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


def _agent_deliberation_model(db: Session) -> str:
    settings = get_settings()
    try:
        row = db.execute(
            text(
                "SELECT preferred_model FROM settings_model_routing "
                "WHERE job_type = :job_type"
            ),
            {"job_type": "agent_deliberation_batch"},
        ).mappings().first()
    except Exception:
        row = None
    if row is not None and row.get("preferred_model"):
        return str(row["preferred_model"])
    return settings.default_model


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
