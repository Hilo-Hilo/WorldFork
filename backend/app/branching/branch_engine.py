"""Recursive copy-on-write branch engine.

``commit_branch`` is the single entry point used by the tick runner / API
to spawn a new universe.  It:

1. Allocates a new universe_id and lineage_path.
2. Inserts the child :class:`UniverseModel` and :class:`BranchNodeModel`.
3. Updates the parent's ``child_universe_ids``.
4. Copy-on-write seeds the child at ``branch_from_tick`` — only that one
   tick's cohort_state / hero_state / event / social_post rows are
   physically copied.  Earlier ticks remain on the parent and are read via
   the (future) ``v_universe_state`` SQL view.
5. Applies the :class:`BranchDelta` via :func:`apply_delta`.
6. Records the begin_universe artifact in the run ledger.
7. Enqueues the child's first tick (``branch_from_tick + 1``) when the
   policy approves.
8. Invalidates the lineage Redis cache for the run.

Copy-on-write namespacing
-------------------------
The cohort / hero / event tables don't carry ``universe_id`` in their PK,
so we namespace ids on copy: ``original_id#c<suffix>`` where ``suffix`` is
the last 6 chars of the new universe_id.  See ``namespacing.py``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from backend.app.branching.delta import apply_delta
from backend.app.branching.namespacing import (
    is_namespaced,
    namespace_id,
    strip_namespace,
    suffix_for,
)
from backend.app.core.ids import new_id

if TYPE_CHECKING:
    from backend.app.models.universes import UniverseModel
    from backend.app.schemas.branching import BranchDelta, BranchNode, BranchPolicyResult
    from backend.app.storage.ledger import Ledger

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass
class BranchCommitResult:
    """Returned by :func:`commit_branch`.

    Holds the IDs and structural metadata callers need for follow-up work
    (broadcasting, ledger append, schedule the child's first tick, etc.).
    """

    child_universe_id: str
    parent_universe_id: str
    branch_from_tick: int
    branch_delta: Any  # BranchDelta — runtime discriminated union
    branch_node: Any  # BranchNode (Pydantic schema)
    lineage_path: list[str]
    status: str
    delta_apply_summary: dict[str, Any]
    enqueued: bool


# ---------------------------------------------------------------------------
# commit_branch
# ---------------------------------------------------------------------------


async def commit_branch(
    *,
    session: AsyncSession,
    parent_universe: UniverseModel,
    branch_from_tick: int,
    delta: BranchDelta,
    branch_reason: str,
    policy_result: BranchPolicyResult,
    ledger: Ledger | None = None,
    enqueue_first_tick: bool = True,
) -> BranchCommitResult:
    """Atomically branch ``parent_universe`` at ``branch_from_tick``.

    Parameters
    ----------
    session
        An open async SQLAlchemy session.  This function manages its own
        ``begin_nested`` so callers may pass either a transactional or a
        pristine session.
    parent_universe
        The :class:`UniverseModel` row to branch from.  Must be loaded.
    branch_from_tick
        The tick at which the child diverges.  Rows at this tick are
        physically copied; earlier ticks stay on the parent.
    delta
        Discriminated :class:`BranchDelta` describing the counterfactual.
    branch_reason
        Free-form reason recorded on both ``Universe.branch_reason`` and
        ``BranchNode.branch_trigger``.
    policy_result
        Output of ``branch_policy.evaluate_branch_policy``.  Decides
        ``status`` (active / candidate) and whether to enqueue the child.
    ledger
        Optional :class:`Ledger`.  When supplied, ``begin_universe`` is
        called so the run folder structure mirrors the DB.
    """
    from backend.app.models.branches import BranchNodeModel
    from backend.app.models.cohorts import CohortStateModel
    from backend.app.models.events import EventModel
    from backend.app.models.heroes import HeroArchetypeModel, HeroStateModel
    from backend.app.models.posts import SocialPostModel
    from backend.app.models.universes import UniverseModel

    decision = policy_result.decision
    if decision == "reject":
        raise ValueError(
            f"Cannot commit_branch for rejected policy: {policy_result.reason}"
        )

    status = "active" if decision == "approve" else "candidate"

    # --- Allocate IDs and lineage ----------------------------------------
    child_universe_id = new_id("uni")
    parent_lineage = list(parent_universe.lineage_path or [parent_universe.universe_id])
    lineage_path = parent_lineage + [child_universe_id]
    depth = len(lineage_path) - 1
    suffix = suffix_for(child_universe_id)

    delta_payload: dict[str, Any] = delta.model_dump()
    metrics_summary = dict(parent_universe.latest_metrics or {})

    # --- Open a savepoint so the whole branch is atomic -------------------
    async with session.begin_nested():
        # 1. Insert child UniverseModel
        now = datetime.now(UTC)
        child_universe = UniverseModel(
            universe_id=child_universe_id,
            big_bang_id=parent_universe.big_bang_id,
            parent_universe_id=parent_universe.universe_id,
            lineage_path=lineage_path,
            branch_from_tick=branch_from_tick,
            branch_depth=depth,
            status=status,
            branch_reason=branch_reason,
            branch_delta=dict(delta_payload),
            current_tick=branch_from_tick,
            latest_metrics=metrics_summary,
            created_at=now,
        )
        session.add(child_universe)
        await session.flush()

        # 2. Append child id to parent's child_universe_ids (if model carries it)
        # NOTE: production UniverseModel derives child_universe_ids via the
        # ``children`` relationship, but the test shadow keeps a column for
        # convenience.  Be tolerant of either.
        if hasattr(parent_universe, "child_universe_ids"):
            existing = list(parent_universe.child_universe_ids or [])
            if child_universe_id not in existing:
                existing.append(child_universe_id)
                parent_universe.child_universe_ids = existing
                try:
                    flag_modified(parent_universe, "child_universe_ids")
                except Exception:  # pragma: no cover — not all rows are SQLA-managed
                    pass

        # 3. Insert BranchNodeModel
        branch_node_row = BranchNodeModel(
            universe_id=child_universe_id,
            parent_universe_id=parent_universe.universe_id,
            child_universe_ids=[],
            depth=depth,
            branch_tick=branch_from_tick,
            branch_point_id=f"{parent_universe.universe_id}@t{branch_from_tick}",
            branch_trigger=branch_reason,
            branch_delta=dict(delta_payload),
            status=status,
            metrics_summary=metrics_summary,
            cost_estimate=dict(policy_result.cost_estimate or {}),
            descendant_count=0,
            lineage_path=lineage_path,
        )
        session.add(branch_node_row)
        await session.flush()

        # 4. Copy-on-write seed: cohort_states at branch_from_tick ---------
        cohort_stmt = select(CohortStateModel).where(
            CohortStateModel.universe_id == parent_universe.universe_id,
            CohortStateModel.tick == branch_from_tick,
        )
        for parent_cohort in (await session.execute(cohort_stmt)).scalars().all():
            new_cohort_id = namespace_id(parent_cohort.cohort_id, suffix)
            parent_pcid = parent_cohort.parent_cohort_id
            new_parent_pcid: str | None
            if parent_pcid is None:
                new_parent_pcid = None
            elif is_namespaced(parent_pcid):
                # Already namespaced (shouldn't happen for parent rows, but
                # be defensive) — re-namespace under the child suffix.
                new_parent_pcid = namespace_id(strip_namespace(parent_pcid), suffix)
            else:
                new_parent_pcid = namespace_id(parent_pcid, suffix)

            child_row = _clone_cohort_row(
                parent_cohort,
                new_cohort_id=new_cohort_id,
                new_universe_id=child_universe_id,
                new_parent_cohort_id=new_parent_pcid,
                suffix=suffix,
            )
            session.add(child_row)

        await session.flush()

        # 5. Copy-on-write seed: hero_states at branch_from_tick ----------
        hero_stmt = select(HeroStateModel).where(
            HeroStateModel.universe_id == parent_universe.universe_id,
            HeroStateModel.tick == branch_from_tick,
        )
        hero_id_map: dict[str, str] = {}
        for parent_hero in (await session.execute(hero_stmt)).scalars().all():
            new_hero_id = hero_id_map.get(parent_hero.hero_id)
            if new_hero_id is None:
                new_hero_id = namespace_id(parent_hero.hero_id, suffix)
                hero_id_map[parent_hero.hero_id] = new_hero_id

                parent_archetype = await session.get(HeroArchetypeModel, parent_hero.hero_id)
                if parent_archetype is not None:
                    existing_archetype = await session.get(HeroArchetypeModel, new_hero_id)
                    if existing_archetype is None:
                        session.add(
                            _clone_hero_archetype_row(
                                parent_archetype,
                                new_hero_id=new_hero_id,
                                new_big_bang_id=parent_universe.big_bang_id,
                            )
                        )
            child_row = _clone_hero_row(
                parent_hero,
                new_hero_id=new_hero_id,
                new_universe_id=child_universe_id,
            )
            session.add(child_row)
        await session.flush()

        # 6. Copy events --------------------------------------------------
        # Copy events with created_tick <= branch_from_tick OR scheduled in the
        # future.  Past completed events are needed for the historical record;
        # future scheduled events may be rewritten by a counterfactual delta.
        from sqlalchemy import or_

        event_stmt = select(EventModel).where(
            EventModel.universe_id == parent_universe.universe_id,
            or_(
                EventModel.created_tick <= branch_from_tick,
                EventModel.scheduled_tick > branch_from_tick,
            ),
        )
        for parent_event in (await session.execute(event_stmt)).scalars().all():
            new_event_id = namespace_id(parent_event.event_id, suffix)
            new_parent_evt_id = (
                namespace_id(parent_event.parent_event_id, suffix)
                if parent_event.parent_event_id
                else None
            )
            child_row = _clone_event_row(
                parent_event,
                new_event_id=new_event_id,
                new_universe_id=child_universe_id,
                new_parent_event_id=new_parent_evt_id,
            )
            session.add(child_row)
        await session.flush()

        # 7. Copy social posts -------------------------------------------
        post_stmt = select(SocialPostModel).where(
            SocialPostModel.universe_id == parent_universe.universe_id,
            SocialPostModel.tick_created <= branch_from_tick,
        )
        for parent_post in (await session.execute(post_stmt)).scalars().all():
            new_post_id = namespace_id(parent_post.post_id, suffix)
            child_row = _clone_post_row(
                parent_post,
                new_post_id=new_post_id,
                new_universe_id=child_universe_id,
            )
            session.add(child_row)
        await session.flush()

        # 8. Apply the BranchDelta to the seeded child ---------------------
        delta_summary = await apply_delta(
            session=session,
            child_universe_id=child_universe_id,
            branch_from_tick=branch_from_tick,
            delta=delta,
        )
        if delta_summary.get("applied") is False:
            raise ValueError(
                "Branch delta did not apply; refusing to commit no-op branch "
                f"({delta_summary.get('reason') or 'unknown_reason'})"
            )

        # 9. Run-ledger record (best-effort) -------------------------------
        if ledger is not None:
            try:
                ledger.begin_universe(
                    child_universe_id,
                    parent=parent_universe.universe_id,
                    branch_from_tick=branch_from_tick,
                    branch_delta=dict(delta_payload),
                )
            except Exception as exc:  # pragma: no cover — ledger errors logged
                _log.warning(
                    "ledger.begin_universe failed for %s: %s",
                    child_universe_id,
                    exc,
                )

    # --- Post-commit side effects ----------------------------------------
    enqueued = False
    if enqueue_first_tick and status == "active":
        enqueued = await enqueue_first_child_tick(
            run_id=parent_universe.big_bang_id,
            child_universe_id=child_universe_id,
            tick=branch_from_tick + 1,
        )

    await _invalidate_lineage_cache(parent_universe.big_bang_id)

    branch_node_schema: BranchNode = branch_node_row.to_schema()

    return BranchCommitResult(
        child_universe_id=child_universe_id,
        parent_universe_id=parent_universe.universe_id,
        branch_from_tick=branch_from_tick,
        branch_delta=delta,
        branch_node=branch_node_schema,
        lineage_path=lineage_path,
        status=status,
        delta_apply_summary=delta_summary,
        enqueued=enqueued,
    )


# ---------------------------------------------------------------------------
# Row cloning helpers
# ---------------------------------------------------------------------------


_COHORT_COPY_FIELDS = (
    "archetype_id",
    "represented_population",
    "population_share_of_archetype",
    "issue_stance",
    "expression_level",
    "mobilization_mode",
    "speech_mode",
    "emotions",
    "behavior_state",
    "attention",
    "fatigue",
    "grievance",
    "perceived_efficacy",
    "perceived_majority",
    "fear_of_isolation",
    "willingness_to_speak",
    "identity_salience",
    "visible_trust_summary",
    "exposure_summary",
    "dependency_summary",
    "memory_session_id",
    "recent_post_ids",
    "queued_event_ids",
    "previous_action_ids",
    "prompt_temperature",
    "representation_mode",
    "allowed_tools",
    "is_active",
)


def _clone_cohort_row(
    src: Any,
    *,
    new_cohort_id: str,
    new_universe_id: str,
    new_parent_cohort_id: str | None,
    suffix: str,
) -> Any:
    cls = type(src)
    kwargs: dict[str, Any] = {
        "cohort_id": new_cohort_id,
        "tick": src.tick,
        "universe_id": new_universe_id,
        "parent_cohort_id": new_parent_cohort_id,
        # Re-namespace child_cohort_ids so cross-universe refs stay coherent
        "child_cohort_ids": [
            namespace_id(strip_namespace(cid), suffix)
            for cid in (src.child_cohort_ids or [])
        ],
    }
    for f in _COHORT_COPY_FIELDS:
        kwargs[f] = _copy_value(getattr(src, f, None))
    return cls(**kwargs)


_HERO_COPY_FIELDS = (
    "current_emotions",
    "current_issue_stances",
    "attention",
    "fatigue",
    "perceived_pressure",
    "current_strategy",
    "queued_events",
    "recent_posts",
    "memory_session_id",
)


_HERO_ARCHETYPE_COPY_FIELDS = (
    "label",
    "description",
    "role",
    "institution",
    "location_scope",
    "public_reach",
    "institutional_power",
    "financial_power",
    "agenda_control",
    "media_access",
    "ideology_axes",
    "value_priors",
    "trust_priors",
    "behavioral_axes",
    "volatility",
    "ego_sensitivity",
    "strategic_discipline",
    "controversy_tolerance",
    "direct_event_power",
    "scheduling_permissions",
    "allowed_channels",
)


def _clone_hero_archetype_row(
    src: Any,
    *,
    new_hero_id: str,
    new_big_bang_id: str,
) -> Any:
    cls = type(src)
    kwargs: dict[str, Any] = {
        "hero_id": new_hero_id,
        "big_bang_id": new_big_bang_id,
    }
    for f in _HERO_ARCHETYPE_COPY_FIELDS:
        kwargs[f] = _copy_value(getattr(src, f, None))
    return cls(**kwargs)


def _clone_hero_row(
    src: Any,
    *,
    new_hero_id: str,
    new_universe_id: str,
) -> Any:
    cls = type(src)
    kwargs: dict[str, Any] = {
        "hero_id": new_hero_id,
        "tick": src.tick,
        "universe_id": new_universe_id,
    }
    for f in _HERO_COPY_FIELDS:
        kwargs[f] = _copy_value(getattr(src, f, None))
    return cls(**kwargs)


_EVENT_COPY_FIELDS = (
    "created_tick",
    "scheduled_tick",
    "duration_ticks",
    "event_type",
    "title",
    "description",
    "created_by_actor_id",
    "participants",
    "target_audience",
    "visibility",
    "preconditions",
    "expected_effects",
    "actual_effects",
    "risk_level",
    "status",
    "source_llm_call_id",
)


def _clone_event_row(
    src: Any,
    *,
    new_event_id: str,
    new_universe_id: str,
    new_parent_event_id: str | None,
) -> Any:
    cls = type(src)
    kwargs: dict[str, Any] = {
        "event_id": new_event_id,
        "universe_id": new_universe_id,
        "parent_event_id": new_parent_event_id,
    }
    for f in _EVENT_COPY_FIELDS:
        kwargs[f] = _copy_value(getattr(src, f, None))
    return cls(**kwargs)


_POST_COPY_FIELDS = (
    "platform",
    "tick_created",
    "author_actor_id",
    "author_avatar_id",
    "content",
    "stance_signal",
    "emotion_signal",
    "credibility_signal",
    "visibility_scope",
    "reach_score",
    "hot_score",
    "reactions",
    "repost_count",
    "comment_count",
    "upvote_power_total",
    "downvote_power_total",
)


def _clone_post_row(
    src: Any,
    *,
    new_post_id: str,
    new_universe_id: str,
) -> Any:
    cls = type(src)
    kwargs: dict[str, Any] = {
        "post_id": new_post_id,
        "universe_id": new_universe_id,
    }
    for f in _POST_COPY_FIELDS:
        kwargs[f] = _copy_value(getattr(src, f, None))
    return cls(**kwargs)


def _copy_value(v: Any) -> Any:
    """Shallow-copy mutable JSON containers; pass through scalars."""
    if isinstance(v, dict):
        return dict(v)
    if isinstance(v, list):
        return list(v)
    return v


# ---------------------------------------------------------------------------
# Side effects: tick enqueue + cache invalidation
# ---------------------------------------------------------------------------


async def enqueue_first_child_tick(
    *,
    run_id: str,
    child_universe_id: str,
    tick: int,
) -> bool:
    """Enqueue the child's first tick on P0 via the worker scheduler.

    Best-effort: returns True on success, False if the scheduler / Celery
    isn't available (the caller can resume after broker recovery).
    """
    try:
        from backend.app.core.db import SessionLocal
        from backend.app.models.runs import BigBangRunModel
        from backend.app.workers.scheduler import enqueue, make_envelope

        async with SessionLocal() as session:
            run = await session.get(BigBangRunModel, run_id)
            max_ticks = int(getattr(run, "max_ticks", 0) or 0) if run else 0
            if max_ticks and tick > max_ticks:
                _log.info(
                    "Skipping first child tick %s for %s in run %s; max_ticks=%s",
                    tick,
                    child_universe_id,
                    run_id,
                    max_ticks,
                )
                return False

        envelope = make_envelope(
            job_type="simulate_universe_tick",
            run_id=run_id,
            universe_id=child_universe_id,
            tick=tick,
            payload={
                "run_id": run_id,
                "universe_id": child_universe_id,
                "tick": tick,
            },
        )
        await enqueue(envelope)
        return True
    except Exception as exc:  # pragma: no cover — tested via mocks
        _log.warning(
            "Failed to enqueue first child tick %s for %s: %s",
            tick,
            child_universe_id,
            exc,
        )
        return False


async def _invalidate_lineage_cache(big_bang_id: str) -> None:
    """Invalidate the Redis lineage cache key for ``big_bang_id``.

    Best-effort: swallowed on any error so a missing Redis doesn't break the
    DB transaction.
    """
    try:
        from backend.app.branching.lineage import LineageCache
        from backend.app.core.redis_client import get_redis_client

        redis = get_redis_client()
        await LineageCache(redis).invalidate(big_bang_id)
    except Exception as exc:  # pragma: no cover — tested via mocks
        _log.debug(
            "lineage cache invalidation skipped for %s: %s",
            big_bang_id,
            exc,
        )
