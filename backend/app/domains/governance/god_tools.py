from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import models
from app.domains.multiverse.branch_engine import create_branch

VALID_TOOLS = {
    "continue_timeline",
    "freeze_timeline",
    "terminate_timeline",
    "create_branch",
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
    "approve_split",
    "reject_split",
    "plan_merge",
    "approve_merge_plan",
    "reject_merge_plan",
    "approve_emergence",
    "reject_emergence",
    "register_key_event",
    "request_event_summary_regeneration",
    "mark_ready_for_report",
}


def execute_tool_call(
    db: Session,
    *,
    big_bang_id,
    multiverse: models.Multiverse,
    tick_snapshot_id,
    god_review_id,
    tool_name: str,
    arguments: dict,
    idempotency_key: str,
) -> models.ToolCall:
    if tool_name not in VALID_TOOLS:
        raise ValueError(f"unknown God Agent tool: {tool_name}")
    if big_bang_id != multiverse.big_bang_id:
        raise ValueError("tool big_bang_id does not match multiverse")
    existing = db.scalar(select(models.ToolCall).where(models.ToolCall.idempotency_key == idempotency_key))
    if existing:
        if god_review_id is not None and existing.god_review_id is None:
            existing.god_review_id = god_review_id
            db.flush()
        return existing

    tool_call = models.ToolCall(
        big_bang_id=big_bang_id,
        multiverse_id=multiverse.id,
        tick_snapshot_id=tick_snapshot_id,
        god_review_id=god_review_id,
        tool_name=tool_name,
        arguments=arguments,
        status="running",
        idempotency_key=idempotency_key,
    )
    try:
        with db.begin_nested():
            db.add(tool_call)
            db.flush()
    except IntegrityError:
        existing = db.scalar(select(models.ToolCall).where(models.ToolCall.idempotency_key == idempotency_key))
        if existing:
            return existing
        raise

    try:
        with db.begin_nested():
            result = _execute(
                db,
                multiverse=multiverse,
                tool_name=tool_name,
                arguments=arguments,
                idempotency_key=idempotency_key,
            )
        tool_call.status = "succeeded"
        tool_call.result = result
    except Exception as exc:
        tool_call.status = "failed"
        tool_call.error = str(exc)
        tool_call.result = {}
    db.flush()
    return tool_call


def _execute(
    db: Session,
    *,
    multiverse: models.Multiverse,
    tool_name: str,
    arguments: dict,
    idempotency_key: str,
) -> dict:
    if tool_name == "continue_timeline":
        return {"status": "continued", "reason": arguments.get("reason")}
    if tool_name == "freeze_timeline":
        multiverse.status = "frozen"
        return {"status": "frozen"}
    if tool_name == "terminate_timeline":
        multiverse.status = "terminated"
        return {"status": "terminated"}
    if tool_name == "mark_ready_for_report":
        multiverse.report_status = "ready"
        return {"status": "ready_for_report"}
    if tool_name == "update_population_archetype_total":
        return _update_population_archetype_total(db, multiverse=multiverse, arguments=arguments)
    if tool_name == "update_cohort_state":
        return _update_actor_state(
            db,
            model=models.CohortState,
            multiverse=multiverse,
            arguments=arguments,
            state_type="cohort",
        )
    if tool_name == "update_hero_state":
        return _update_actor_state(
            db,
            model=models.HeroState,
            multiverse=multiverse,
            arguments=arguments,
            state_type="hero",
        )
    if tool_name == "apply_population_delta":
        return _apply_population_delta(db, multiverse=multiverse, arguments=arguments)
    if tool_name == "create_cohort":
        return _create_cohort(db, multiverse=multiverse, arguments=arguments)
    if tool_name == "split_cohort":
        return _split_cohort(db, multiverse=multiverse, arguments=arguments)
    if tool_name == "merge_cohorts":
        return _merge_cohorts(db, multiverse=multiverse, arguments=arguments)
    if tool_name == "deactivate_cohort":
        return _deactivate_actor_state(
            db,
            model=models.CohortState,
            multiverse=multiverse,
            arguments=arguments,
            state_type="cohort",
            actor_status="inactive",
        )
    if tool_name in {"deactivate_hero", "kill_hero"}:
        return _deactivate_actor_state(
            db,
            model=models.HeroState,
            multiverse=multiverse,
            arguments=arguments,
            state_type="hero",
            actor_status="killed" if tool_name == "kill_hero" else "inactive",
        )
    if tool_name == "create_branch":
        child = create_branch(
            db,
            parent=multiverse,
            fork_tick_index=int(arguments["fork_tick_index"]),
            reason=arguments.get("reason", "God Agent branch."),
            idempotency_key=idempotency_key,
            branch_probability=arguments.get("branch_probability"),
            parent_continuation_probability=arguments.get("parent_continuation_probability"),
            probability_basis={
                "source": arguments.get("probability_source") or "god_agent",
                "basis": arguments.get("probability_basis") or arguments.get("reason"),
                "branch_probability": arguments.get("branch_probability"),
                "parent_continuation_probability": arguments.get("parent_continuation_probability"),
            },
        )
        return {
            "status": "created",
            "child_multiverse_id": str(child.id),
            "child_label": child.ui_label,
            "branch_probability": child.branch_probability,
            "child_path_probability": child.path_probability,
        }
    if tool_name == "register_key_event":
        event = db.get(models.Event, arguments.get("event_id")) if arguments.get("event_id") else None
        if event:
            _require_scope(event, multiverse=multiverse, resource_name="event")
            event.meta = {**(event.meta or {}), "key_event": True, "key_event_reason": arguments.get("reason")}
        return {"status": "registered", "event_id": arguments.get("event_id")}
    if tool_name == "request_event_summary_regeneration":
        return {"status": "queued", "event_id": arguments.get("event_id")}
    if tool_name in {"approve_split", "reject_split"}:
        candidate_id = arguments.get("candidate_id")
        if not candidate_id:
            raise ValueError("split candidate_id is required")
        candidate = db.get(models.CohortSplitCandidate, candidate_id)
        if not candidate:
            raise ValueError("split candidate not found")
        _require_scope(candidate, multiverse=multiverse, resource_name="split candidate")
        candidate.status = "approved" if tool_name == "approve_split" else "rejected"
        if tool_name == "approve_split":
            split = models.CohortSplit(
                big_bang_id=multiverse.big_bang_id,
                multiverse_id=multiverse.id,
                tick_index=candidate.tick_index,
                status="persisted",
                payload={**candidate.payload, "approved_by": "god_agent"},
            )
            db.add(split)
            db.flush()
            return {"status": "approved", "split_id": str(split.id)}
        return {"status": "rejected", "candidate_id": str(candidate.id)}
    if tool_name == "plan_merge":
        candidate_id = arguments.get("candidate_id")
        if not candidate_id:
            raise ValueError("merge candidate_id is required")
        candidate = db.get(models.CohortMergeCandidate, candidate_id)
        if not candidate:
            raise ValueError("merge candidate not found")
        _require_scope(candidate, multiverse=multiverse, resource_name="merge candidate")
        plan = models.CohortMergePlan(
            big_bang_id=multiverse.big_bang_id,
            multiverse_id=multiverse.id,
            tick_index=candidate.tick_index,
            status="planned",
            payload={"candidate_id": str(candidate.id), "plan": arguments.get("plan", candidate.payload)},
        )
        db.add(plan)
        db.flush()
        return {"status": "planned", "merge_plan_id": str(plan.id)}
    if tool_name in {"approve_merge_plan", "reject_merge_plan"}:
        merge_plan_id = arguments.get("merge_plan_id")
        if not merge_plan_id:
            raise ValueError("merge_plan_id is required")
        plan = db.get(models.CohortMergePlan, merge_plan_id)
        if not plan:
            raise ValueError("merge plan not found")
        _require_scope(plan, multiverse=multiverse, resource_name="merge plan")
        plan.status = "approved" if tool_name == "approve_merge_plan" else "rejected"
        if tool_name == "approve_merge_plan":
            merge = models.CohortMerge(
                big_bang_id=multiverse.big_bang_id,
                multiverse_id=multiverse.id,
                tick_index=plan.tick_index,
                status="persisted",
                payload=plan.payload,
            )
            db.add(merge)
            db.flush()
            return {"status": "approved", "merge_id": str(merge.id)}
        return {"status": "rejected", "merge_plan_id": str(plan.id)}
    if tool_name in {"approve_emergence", "reject_emergence"}:
        candidate_id = arguments.get("candidate_id")
        if not candidate_id:
            raise ValueError("emergence candidate_id is required")
        candidate = db.get(models.CohortEmergenceCandidate, candidate_id)
        if not candidate:
            raise ValueError("emergence candidate not found")
        _require_scope(candidate, multiverse=multiverse, resource_name="emergence candidate")
        candidate.status = "approved" if tool_name == "approve_emergence" else "rejected"
        if tool_name == "approve_emergence":
            emergence = models.CohortEmergence(
                big_bang_id=multiverse.big_bang_id,
                multiverse_id=multiverse.id,
                tick_index=candidate.tick_index,
                status="persisted",
                payload=candidate.payload,
            )
            db.add(emergence)
            db.flush()
            return {"status": "approved", "emergence_id": str(emergence.id)}
        return {"status": "rejected", "candidate_id": str(candidate.id)}
    return {"status": "recorded", "tool_name": tool_name, "arguments": arguments}


def _require_scope(resource, *, multiverse: models.Multiverse, resource_name: str) -> None:
    if resource.big_bang_id != multiverse.big_bang_id or resource.multiverse_id != multiverse.id:
        raise ValueError(f"{resource_name} does not belong to current big_bang and multiverse")


def _update_population_archetype_total(
    db: Session,
    *,
    multiverse: models.Multiverse,
    arguments: dict,
) -> dict:
    archetype_id = arguments.get("archetype_id")
    name = arguments.get("name")
    total = _nonnegative_int(arguments.get("population_total"), field="population_total")
    if not archetype_id and not name:
        raise ValueError("archetype_id or name is required")
    archetype = None
    rows = db.scalars(
        select(models.PopulationArchetype).where(
            models.PopulationArchetype.big_bang_id == multiverse.big_bang_id
        )
    ).all()
    for row in rows:
        definition = row.definition or {}
        if str(row.id) == str(archetype_id) or definition.get("archetype_id") == archetype_id or row.name == name:
            archetype = row
            break
    if archetype is None:
        raise ValueError("population archetype not found")
    previous = archetype.definition or {}
    archetype.definition = {
        **previous,
        "population_total": total,
        "population_total_update_reason": arguments.get("reason"),
    }
    db.flush()
    return {
        "status": "updated",
        "population_archetype_id": str(archetype.id),
        "name": archetype.name,
        "population_total": total,
    }


def _update_actor_state(
    db: Session,
    *,
    model,
    multiverse: models.Multiverse,
    arguments: dict,
    state_type: str,
) -> dict:
    actor = _actor_from_args(db, multiverse=multiverse, arguments=arguments)
    row = _latest_state_row(db, model, multiverse=multiverse, actor_id=actor.id)
    if row is None:
        raise ValueError(f"{state_type} state not found")
    state_delta = arguments.get("state_delta") or arguments.get("state") or {}
    if not isinstance(state_delta, dict):
        raise ValueError("state_delta must be an object")
    tick_index = _tick_index_from_args(arguments, fallback=int(row.tick_index or 0))
    next_state = {
        **(row.state or {}),
        **state_delta,
        "last_god_update_reason": arguments.get("reason"),
        "last_god_update_tick": tick_index,
    }
    _validate_population_fields(next_state)
    db.add(
        model(
            big_bang_id=multiverse.big_bang_id,
            multiverse_id=multiverse.id,
            actor_id=actor.id,
            tick_index=tick_index,
            state=next_state,
            queued_event_ids=list(row.queued_event_ids or []),
        )
    )
    db.flush()
    return {"status": "updated", "actor_id": str(actor.id), "state_type": state_type, "tick_index": tick_index}


def _apply_population_delta(db: Session, *, multiverse: models.Multiverse, arguments: dict) -> dict:
    actor = _actor_from_args(db, multiverse=multiverse, arguments=arguments)
    row = _latest_state_row(db, models.CohortState, multiverse=multiverse, actor_id=actor.id)
    if row is None:
        raise ValueError("cohort state not found")
    state = dict(row.state or {})
    previous = _nonnegative_int(state.get("represented_population"), field="represented_population")
    if "population_total" in arguments:
        updated = _nonnegative_int(arguments.get("population_total"), field="population_total")
    else:
        updated = max(0, previous + int(arguments.get("delta", 0) or 0))
    tick_index = _tick_index_from_args(arguments, fallback=int(row.tick_index or 0))
    next_state = {
        **state,
        "represented_population": updated,
        "population_delta": updated - previous,
        "population_delta_reason": arguments.get("reason"),
        "last_god_update_tick": tick_index,
    }
    db.add(
        models.CohortState(
            big_bang_id=multiverse.big_bang_id,
            multiverse_id=multiverse.id,
            actor_id=actor.id,
            tick_index=tick_index,
            state=next_state,
            queued_event_ids=list(row.queued_event_ids or []),
        )
    )
    db.flush()
    return {
        "status": "updated",
        "actor_id": str(actor.id),
        "previous_population": previous,
        "represented_population": updated,
        "delta": updated - previous,
    }


def _create_cohort(db: Session, *, multiverse: models.Multiverse, arguments: dict) -> dict:
    name = str(arguments.get("name") or "").strip()
    if not name:
        raise ValueError("name is required")
    state = arguments.get("state") or arguments.get("initial_state") or {}
    if not isinstance(state, dict):
        raise ValueError("state must be an object")
    population = _nonnegative_int(
        state.get("represented_population", arguments.get("represented_population")),
        field="represented_population",
    )
    state = {
        **state,
        "represented_population": population,
        "parent_cohort_id": arguments.get("parent_cohort_id") or state.get("parent_cohort_id"),
        "split_or_creation_reason": arguments.get("reason"),
    }
    _validate_population_fields(state)
    actor = models.Actor(
        big_bang_id=multiverse.big_bang_id,
        actor_type="cohort",
        name=name,
        description=arguments.get("description") or arguments.get("reason"),
        archetype={"source": "god_agent_tool", **(arguments.get("archetype") or {})},
        created_tick_index=_tick_index_from_args(arguments, fallback=0),
        status="active",
    )
    db.add(actor)
    db.flush()
    db.add(
        models.CohortState(
            big_bang_id=multiverse.big_bang_id,
            multiverse_id=multiverse.id,
            actor_id=actor.id,
            tick_index=_tick_index_from_args(arguments, fallback=0),
            state=state,
            queued_event_ids=[],
        )
    )
    db.flush()
    return {"status": "created", "actor_id": str(actor.id), "name": actor.name, "represented_population": population}


def _split_cohort(db: Session, *, multiverse: models.Multiverse, arguments: dict) -> dict:
    parent = _actor_from_args(db, multiverse=multiverse, arguments=arguments)
    parent_row = _latest_state_row(db, models.CohortState, multiverse=multiverse, actor_id=parent.id)
    if parent_row is None:
        raise ValueError("parent cohort state not found")
    parent_state = dict(parent_row.state or {})
    parent_population = _nonnegative_int(parent_state.get("represented_population"), field="represented_population")
    children = arguments.get("children")
    if not isinstance(children, list) or len(children) < 2:
        raise ValueError("split_cohort requires at least two children")
    child_specs = []
    child_total = 0
    for index, child in enumerate(children):
        if not isinstance(child, dict):
            raise ValueError("each child must be an object")
        child_state = child.get("state") or child.get("initial_state") or {}
        if not isinstance(child_state, dict):
            raise ValueError("child state must be an object")
        population = child.get("represented_population", child_state.get("represented_population"))
        if population is None and child.get("population_share") is not None:
            population = round(parent_population * float(child["population_share"]))
        population = _nonnegative_int(population, field=f"children[{index}].represented_population")
        child_total += population
        child_specs.append((child, child_state, population))
    if child_total != parent_population:
        raise ValueError(
            f"split child populations must conserve parent population: children={child_total}, parent={parent_population}"
        )
    tick_index = _tick_index_from_args(arguments, fallback=int(parent_row.tick_index or 0))
    created = []
    for child, child_state, population in child_specs:
        child_actor = models.Actor(
            big_bang_id=multiverse.big_bang_id,
            actor_type="cohort",
            name=str(child.get("name") or child.get("label") or "Split cohort"),
            description=child.get("description") or child.get("rationale") or arguments.get("reason"),
            archetype={"source": "split_cohort", "parent_actor_id": str(parent.id), **(child.get("archetype") or {})},
            created_tick_index=tick_index,
            status="active",
        )
        db.add(child_actor)
        db.flush()
        next_state = {
            **parent_state,
            **child_state,
            "represented_population": population,
            "population_share_of_parent": round(population / parent_population, 6) if parent_population else 0.0,
            "parent_cohort_id": str(parent.id),
            "split_reason": arguments.get("reason"),
            "split_axis": arguments.get("split_axis"),
            "last_god_update_tick": tick_index,
        }
        _validate_population_fields(next_state)
        db.add(
            models.CohortState(
                big_bang_id=multiverse.big_bang_id,
                multiverse_id=multiverse.id,
                actor_id=child_actor.id,
                tick_index=tick_index,
                state=next_state,
                queued_event_ids=list(parent_row.queued_event_ids or []),
            )
        )
        created.append({"actor_id": str(child_actor.id), "name": child_actor.name, "represented_population": population})
    parent.status = arguments.get("parent_status") or "split"
    db.add(
        models.CohortState(
            big_bang_id=multiverse.big_bang_id,
            multiverse_id=multiverse.id,
            actor_id=parent.id,
            tick_index=tick_index,
            state={
                **parent_state,
                "is_active": False,
                "status": "split",
                "child_cohort_ids": [item["actor_id"] for item in created],
                "split_reason": arguments.get("reason"),
                "last_god_update_tick": tick_index,
            },
            queued_event_ids=list(parent_row.queued_event_ids or []),
        )
    )
    db.flush()
    return {
        "status": "split",
        "parent_actor_id": str(parent.id),
        "parent_population": parent_population,
        "children": created,
    }


def _merge_cohorts(db: Session, *, multiverse: models.Multiverse, arguments: dict) -> dict:
    cohort_ids = arguments.get("cohort_ids")
    if not isinstance(cohort_ids, list) or len(cohort_ids) < 2:
        raise ValueError("merge_cohorts requires at least two cohort_ids")
    rows = []
    total = 0
    for cohort_id in cohort_ids:
        actor = db.get(models.Actor, cohort_id)
        if actor is None or actor.big_bang_id != multiverse.big_bang_id:
            raise ValueError(f"cohort not found or out of scope: {cohort_id}")
        row = _latest_state_row(db, models.CohortState, multiverse=multiverse, actor_id=actor.id)
        if row is None:
            raise ValueError(f"cohort state not found: {cohort_id}")
        rows.append((actor, row))
        total += _nonnegative_int((row.state or {}).get("represented_population"), field="represented_population")
    state = arguments.get("state") or arguments.get("initial_state") or {}
    if not isinstance(state, dict):
        raise ValueError("state must be an object")
    state = {**state, "represented_population": total, "merged_from_cohort_ids": [str(a.id) for a, _ in rows], "merge_reason": arguments.get("reason")}
    _validate_population_fields(state)
    result = _create_cohort(
        db,
        multiverse=multiverse,
        arguments={
            "name": arguments.get("name") or "Merged cohort",
            "description": arguments.get("reason"),
            "state": state,
            "tick_index": arguments.get("tick_index"),
            "reason": arguments.get("reason"),
        },
    )
    for actor, row in rows:
        actor.status = "merged"
        db.add(
            models.CohortState(
                big_bang_id=multiverse.big_bang_id,
                multiverse_id=multiverse.id,
                actor_id=actor.id,
                tick_index=_tick_index_from_args(arguments, fallback=int(row.tick_index or 0)),
                state={**(row.state or {}), "is_active": False, "status": "merged", "merged_into_cohort_id": result["actor_id"]},
                queued_event_ids=list(row.queued_event_ids or []),
            )
        )
    db.flush()
    return {"status": "merged", "merged_actor_id": result["actor_id"], "represented_population": total}


def _deactivate_actor_state(
    db: Session,
    *,
    model,
    multiverse: models.Multiverse,
    arguments: dict,
    state_type: str,
    actor_status: str,
) -> dict:
    actor = _actor_from_args(db, multiverse=multiverse, arguments=arguments)
    row = _latest_state_row(db, model, multiverse=multiverse, actor_id=actor.id)
    if row is None:
        raise ValueError(f"{state_type} state not found")
    actor.status = actor_status
    tick_index = _tick_index_from_args(arguments, fallback=int(row.tick_index or 0))
    db.add(
        model(
            big_bang_id=multiverse.big_bang_id,
            multiverse_id=multiverse.id,
            actor_id=actor.id,
            tick_index=tick_index,
            state={
                **(row.state or {}),
                "is_active": False,
                "status": actor_status,
                "deactivation_reason": arguments.get("reason"),
                "last_god_update_tick": tick_index,
            },
            queued_event_ids=list(row.queued_event_ids or []),
        )
    )
    db.flush()
    return {"status": actor_status, "actor_id": str(actor.id), "state_type": state_type}


def _actor_from_args(db: Session, *, multiverse: models.Multiverse, arguments: dict) -> models.Actor:
    actor_id = arguments.get("actor_id") or arguments.get("cohort_id") or arguments.get("hero_id") or arguments.get("parent_cohort_id")
    actor_name = arguments.get("actor_name") or arguments.get("cohort_name") or arguments.get("hero_name")
    actor = db.get(models.Actor, actor_id) if actor_id else None
    if actor is None and actor_name:
        actor = db.scalar(
            select(models.Actor).where(
                models.Actor.big_bang_id == multiverse.big_bang_id,
                models.Actor.name == actor_name,
            )
        )
    if actor is None:
        raise ValueError("actor_id/cohort_id/hero_id or actor_name/cohort_name/hero_name is required")
    if actor.big_bang_id != multiverse.big_bang_id:
        raise ValueError("actor does not belong to current big_bang")
    return actor


def _latest_state_row(db: Session, model, *, multiverse: models.Multiverse, actor_id):
    return db.scalar(
        select(model)
        .where(model.multiverse_id == multiverse.id, model.actor_id == actor_id)
        .order_by(model.tick_index.desc(), model.created_at.desc())
        .limit(1)
    )


def _tick_index_from_args(arguments: dict, *, fallback: int) -> int:
    value = arguments.get("tick_index")
    if value is None:
        return fallback
    return int(value)


def _nonnegative_int(value, *, field: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an integer") from None
    if number < 0:
        raise ValueError(f"{field} must be nonnegative")
    return number


def _validate_population_fields(state: dict) -> None:
    if "represented_population" in state:
        _nonnegative_int(state.get("represented_population"), field="represented_population")
    share = state.get("population_share_of_archetype")
    if share is not None:
        try:
            value = float(share)
        except (TypeError, ValueError):
            raise ValueError("population_share_of_archetype must be numeric") from None
        if value < 0.0 or value > 1.0:
            raise ValueError("population_share_of_archetype must be between 0 and 1")
