"""Apply a :class:`BranchDelta` to a freshly-seeded child universe.

A ``BranchDelta`` is the *counterfactual mutation* applied to the parent's
state-snapshot at ``branch_from_tick`` to produce the child universe.  This
module owns the mutation strategies for each delta variant (branch-delta model):

* ``counterfactual_event_rewrite`` — rewrite the description / status of an
  event that was already copied into the child by the branch engine.
* ``parameter_shift`` — store a per-universe parameter override on
  ``Universe.branch_delta`` so the simulation engine can read it at runtime.
* ``actor_state_override`` — mutate a single field on the cohort_state or
  hero_state row at ``branch_from_tick`` in the child universe.
* ``hero_decision_override`` — stash a recorded hero decision on
  ``Universe.branch_delta.hero_decision_overrides`` so the next tick's
  prompt builder skips the LLM call for that hero.

The branch engine calls :func:`apply_delta` *after* it has copied the
parent's seed-tick state into the child universe (copy-on-write seed).
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from backend.app.branching.namespacing import namespace_id, suffix_for
from backend.app.schemas.branching import (
    ActorStateOverrideDelta,
    CounterfactualEventRewriteDelta,
    HeroDecisionOverrideDelta,
    ParameterShiftDelta,
)

_log = logging.getLogger(__name__)

_PROTECTED_ACTOR_OVERRIDE_FIELDS = {
    "actor_id",
    "archetype_id",
    "cohort_id",
    "hero_id",
    "population_share_of_archetype",
    "represented_population",
    "tick",
    "universe_id",
}


# ---------------------------------------------------------------------------
# Public dispatcher
# ---------------------------------------------------------------------------


async def apply_delta(
    *,
    session: AsyncSession,
    child_universe_id: str,
    branch_from_tick: int,
    delta: Any,
) -> dict[str, Any]:
    """Mutate the child's seed-tick state to reflect the counterfactual.

    Returns a side-effect summary dict for ledger persistence.

    The caller is responsible for opening / committing the surrounding
    transaction; this function only flushes pending mutations.
    """
    if isinstance(delta, CounterfactualEventRewriteDelta):
        return await _apply_counterfactual_event_rewrite(
            session=session,
            child_universe_id=child_universe_id,
            branch_from_tick=branch_from_tick,
            delta=delta,
        )
    if isinstance(delta, ParameterShiftDelta):
        return await _apply_parameter_shift(
            session=session,
            child_universe_id=child_universe_id,
            delta=delta,
        )
    if isinstance(delta, ActorStateOverrideDelta):
        return await _apply_actor_state_override(
            session=session,
            child_universe_id=child_universe_id,
            branch_from_tick=branch_from_tick,
            delta=delta,
        )
    if isinstance(delta, HeroDecisionOverrideDelta):
        return await _apply_hero_decision_override(
            session=session,
            child_universe_id=child_universe_id,
            delta=delta,
        )
    raise TypeError(f"Unknown BranchDelta variant: {type(delta).__name__}")


# ---------------------------------------------------------------------------
# counterfactual_event_rewrite
# ---------------------------------------------------------------------------


async def _apply_counterfactual_event_rewrite(
    *,
    session: AsyncSession,
    child_universe_id: str,
    branch_from_tick: int,
    delta: CounterfactualEventRewriteDelta,
) -> dict[str, Any]:
    """Find the event with ``target_event_id`` in the child universe.

    The branch engine has already namespaced the event_id when copying.
    We accept either the bare original id (``target_event_id``) or the
    namespaced id (``target_event_id#cXXXXXX``) for ergonomic callers.
    """
    from backend.app.models.events import EventModel

    suffix = suffix_for(child_universe_id)
    namespaced = namespace_id(delta.target_event_id, suffix)

    stmt = select(EventModel).where(
        EventModel.universe_id == child_universe_id,
        EventModel.event_id.in_([delta.target_event_id, namespaced]),
    )
    res = await session.execute(stmt)
    row = res.scalars().first()

    if row is None:
        _log.warning(
            "counterfactual_event_rewrite: target event %s not found in child %s",
            delta.target_event_id,
            child_universe_id,
        )
        return {
            "delta_type": "counterfactual_event_rewrite",
            "applied": False,
            "reason": "target_event_not_found",
            "target_event_id": delta.target_event_id,
        }

    original_status = row.status
    row.description = delta.child_version
    row.actual_effects = None
    if original_status == "completed":
        row.status = "scheduled"

    await session.flush()
    return {
        "delta_type": "counterfactual_event_rewrite",
        "applied": True,
        "event_id": row.event_id,
        "previous_description": delta.parent_version,
        "new_description": delta.child_version,
        "previous_status": original_status,
        "new_status": row.status,
    }


# ---------------------------------------------------------------------------
# parameter_shift
# ---------------------------------------------------------------------------


async def _apply_parameter_shift(
    *,
    session: AsyncSession,
    child_universe_id: str,
    delta: ParameterShiftDelta,
) -> dict[str, Any]:
    """Persist the parameter shift on ``Universe.branch_delta.parameter_overrides``.

    Supports targets of the form ``news_channel.<key>.<dim>`` and
    ``sociology.<param>``; unknown targets are still recorded and the engine
    is expected to ignore unsupported values at runtime.
    """
    from backend.app.models.universes import UniverseModel

    universe = await session.get(UniverseModel, child_universe_id)
    if universe is None:
        raise ValueError(
            f"parameter_shift: child universe {child_universe_id} not found"
        )

    bd: dict[str, Any] = dict(universe.branch_delta or {})
    overrides: dict[str, Any] = dict(bd.get("parameter_overrides") or {})
    overrides[delta.target] = dict(delta.delta)
    bd["parameter_overrides"] = overrides

    universe.branch_delta = bd
    flag_modified(universe, "branch_delta")
    await session.flush()
    return {
        "delta_type": "parameter_shift",
        "applied": True,
        "target": delta.target,
        "delta": dict(delta.delta),
    }


# ---------------------------------------------------------------------------
# actor_state_override
# ---------------------------------------------------------------------------


async def _apply_actor_state_override(
    *,
    session: AsyncSession,
    child_universe_id: str,
    branch_from_tick: int,
    delta: ActorStateOverrideDelta,
) -> dict[str, Any]:
    """Find the actor's row at ``branch_from_tick`` in the child and mutate ``field``.

    Tries cohort_states first, then hero_states.  If ``new_value`` is a dict
    and the existing field is a dict, the values are merged (top-level
    update); otherwise the field is replaced wholesale.
    """
    from backend.app.models.cohorts import CohortStateModel
    from backend.app.models.heroes import HeroStateModel

    root_field = delta.field.split(".", 1)[0]
    if root_field in _PROTECTED_ACTOR_OVERRIDE_FIELDS:
        return {
            "delta_type": "actor_state_override",
            "applied": False,
            "reason": "conservation_critical_field",
            "actor_id": delta.actor_id,
            "field": delta.field,
        }

    suffix = suffix_for(child_universe_id)
    namespaced = namespace_id(delta.actor_id, suffix)

    # Cohort first
    cstmt = select(CohortStateModel).where(
        CohortStateModel.universe_id == child_universe_id,
        CohortStateModel.tick == branch_from_tick,
        CohortStateModel.cohort_id.in_([delta.actor_id, namespaced]),
    )
    cres = await session.execute(cstmt)
    cohort = cres.scalars().first()

    target_row: Any | None = cohort
    target_kind = "cohort_state" if cohort else None

    if target_row is None:
        hstmt = select(HeroStateModel).where(
            HeroStateModel.universe_id == child_universe_id,
            HeroStateModel.tick == branch_from_tick,
            HeroStateModel.hero_id.in_([delta.actor_id, namespaced]),
        )
        hres = await session.execute(hstmt)
        target_row = hres.scalars().first()
        target_kind = "hero_state" if target_row else None

    if target_row is None:
        return {
            "delta_type": "actor_state_override",
            "applied": False,
            "reason": "actor_state_not_found",
            "actor_id": delta.actor_id,
            "tick": branch_from_tick,
        }

    if not hasattr(target_row, delta.field):
        return {
            "delta_type": "actor_state_override",
            "applied": False,
            "reason": "field_not_on_row",
            "actor_id": delta.actor_id,
            "field": delta.field,
        }

    previous = getattr(target_row, delta.field)

    if isinstance(delta.new_value, dict) and isinstance(previous, dict):
        merged = dict(previous)
        merged.update(delta.new_value)
        setattr(target_row, delta.field, merged)
        flag_modified(target_row, delta.field)
    else:
        setattr(target_row, delta.field, delta.new_value)
        if isinstance(delta.new_value, dict):
            flag_modified(target_row, delta.field)

    await session.flush()
    return {
        "delta_type": "actor_state_override",
        "applied": True,
        "actor_id": delta.actor_id,
        "actor_kind": target_kind,
        "field": delta.field,
        "previous_value": previous if not isinstance(previous, dict) else dict(previous),
        "new_value": (
            dict(delta.new_value) if isinstance(delta.new_value, dict) else delta.new_value
        ),
    }


# ---------------------------------------------------------------------------
# hero_decision_override
# ---------------------------------------------------------------------------


async def _apply_hero_decision_override(
    *,
    session: AsyncSession,
    child_universe_id: str,
    delta: HeroDecisionOverrideDelta,
) -> dict[str, Any]:
    """Stash the override on ``Universe.branch_delta.hero_decision_overrides``.

    The tick runner reads this map and short-circuits the LLM call for any
    matching ``(hero_id, tick)`` pair, applying ``new_decision`` directly.
    """
    from backend.app.models.universes import UniverseModel

    universe = await session.get(UniverseModel, child_universe_id)
    if universe is None:
        raise ValueError(
            f"hero_decision_override: child universe {child_universe_id} not found"
        )

    bd: dict[str, Any] = dict(universe.branch_delta or {})
    overrides: dict[str, Any] = dict(bd.get("hero_decision_overrides") or {})
    suffix = suffix_for(child_universe_id)
    # Try both the original and the namespaced hero_id so the tick runner can
    # look up either.  The simulation should resolve to whichever exists.
    key_orig = f"{delta.hero_id}@t{delta.tick}"
    key_ns = f"{namespace_id(delta.hero_id, suffix)}@t{delta.tick}"
    overrides[key_orig] = dict(delta.new_decision)
    overrides[key_ns] = dict(delta.new_decision)
    bd["hero_decision_overrides"] = overrides

    universe.branch_delta = bd
    flag_modified(universe, "branch_delta")
    await session.flush()
    return {
        "delta_type": "hero_decision_override",
        "applied": True,
        "hero_id": delta.hero_id,
        "tick": delta.tick,
    }
