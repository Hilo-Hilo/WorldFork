"""
Cohort split / merge — PRD §12.7, §12.8.

ENFORCES POPULATION CONSERVATION at the DB level. Any commit_split or
commit_merge that would change the active total population for an archetype
in a universe MUST be rejected at validation time, and re-checked by
:func:`audit_population_conservation` afterwards.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.ids import new_id
from backend.app.models.cohorts import CohortStateModel
from backend.app.schemas.actors import CohortState, PopulationArchetype
from backend.app.schemas.sociology import (
    MergeProposal,
    SociologyParams,
    SplitProposal,
)

if TYPE_CHECKING:
    pass

_log = logging.getLogger(__name__)


def _row_to_minimal_state(row: Any) -> CohortState:
    """Build a CohortState from a CohortStateModel-like row, falling back to
    a minimal projection so the validator can run.

    Used by ``commit_split`` so we don't require ``row.to_schema()`` to exist
    (the SQLite shadow row in tests doesn't define it).
    """
    if hasattr(row, "to_schema"):
        return row.to_schema()  # type: ignore[no-any-return]
    return CohortState(
        cohort_id=row.cohort_id,
        universe_id=row.universe_id,
        tick=row.tick,
        archetype_id=row.archetype_id,
        parent_cohort_id=row.parent_cohort_id,
        child_cohort_ids=list(row.child_cohort_ids or []),
        represented_population=row.represented_population,
        population_share_of_archetype=row.population_share_of_archetype,
        issue_stance=dict(row.issue_stance or {}),
        expression_level=row.expression_level,
        mobilization_mode=row.mobilization_mode,
        speech_mode=row.speech_mode,
        emotions=dict(row.emotions or {}),
        behavior_state=dict(row.behavior_state or {}),
        attention=row.attention,
        fatigue=row.fatigue,
        grievance=row.grievance,
        perceived_efficacy=row.perceived_efficacy,
        perceived_majority=dict(row.perceived_majority or {}),
        fear_of_isolation=row.fear_of_isolation,
        willingness_to_speak=row.willingness_to_speak,
        identity_salience=row.identity_salience,
        visible_trust_summary=dict(row.visible_trust_summary or {}),
        exposure_summary=dict(row.exposure_summary or {}),
        dependency_summary=dict(row.dependency_summary or {}),
        memory_session_id=row.memory_session_id,
        recent_post_ids=list(row.recent_post_ids or []),
        queued_event_ids=list(row.queued_event_ids or []),
        previous_action_ids=list(row.previous_action_ids or []),
        prompt_temperature=row.prompt_temperature,
        representation_mode=row.representation_mode,
        allowed_tools=list(row.allowed_tools or []),
        is_active=row.is_active,
    )


# ---------------------------------------------------------------------------
# SPLIT
# ---------------------------------------------------------------------------


def evaluate_split_validity(
    *,
    parent: CohortState,
    proposal: SplitProposal,
    archetype: PopulationArchetype,
    params: SociologyParams,
) -> tuple[bool, str | None]:
    """Pure validator. Returns (ok, error_message). PRD §12.7."""
    children = proposal.children
    if len(children) < 2:
        return False, "split must have at least 2 children"
    if len(children) > archetype.max_child_cohorts:
        return (
            False,
            f"split has {len(children)} children but archetype limits "
            f"to {archetype.max_child_cohorts}",
        )

    # HARD population conservation
    total = sum(c.represented_population for c in children)
    if total != parent.represented_population:
        return (
            False,
            f"sum(children.represented_population)={total} != "
            f"parent.represented_population={parent.represented_population}",
        )

    min_pop = max(
        params.split_merge.min_split_population,
        archetype.min_split_population,
    )
    min_share = max(params.split_merge.min_split_share, archetype.min_split_share)
    parent_pop = max(parent.represented_population, 1)

    for i, child in enumerate(children):
        if child.represented_population < min_pop:
            return (
                False,
                f"child[{i}].represented_population={child.represented_population} "
                f"< min_split_population={min_pop}",
            )
        share = child.represented_population / parent_pop
        if share < min_share:
            return (
                False,
                f"child[{i}].share={share:.4f} < min_split_share={min_share}",
            )
        if child.archetype_id != archetype.archetype_id:
            return (
                False,
                f"child[{i}].archetype_id={child.archetype_id!r} "
                f"!= archetype.archetype_id={archetype.archetype_id!r}",
            )

    if proposal.split_distance < params.split_merge.split_distance_threshold:
        return (
            False,
            f"split_distance={proposal.split_distance} "
            f"< threshold={params.split_merge.split_distance_threshold}",
        )

    return True, None


async def commit_split(
    session: AsyncSession,
    *,
    parent: CohortStateModel,
    proposal: SplitProposal,
    current_tick: int,
    archetype: PopulationArchetype,
    params: SociologyParams,
) -> list[CohortStateModel]:
    """Commit a validated split inside a transaction.

    Marks parent inactive, inserts N child cohort rows. All within
    `async with session.begin():` so a violation rolls everything back.
    """
    parent_schema = _row_to_minimal_state(parent)
    ok, err = evaluate_split_validity(
        parent=parent_schema,
        proposal=proposal,
        archetype=archetype,
        params=params,
    )
    if not ok:
        raise ValueError(f"invalid split: {err}")

    children: list[CohortStateModel] = []
    parent_pop = max(parent.represented_population, 1)

    async with session.begin_nested() if session.in_transaction() else session.begin():
        # Mark parent inactive
        parent.is_active = False
        parent.child_cohort_ids = list(parent.child_cohort_ids or [])

        for spec in proposal.children:
            child_id = new_id("coh")
            share = spec.represented_population / parent_pop

            # Seed merged emotions: parent + spec.seed_emotions (spec wins)
            seeded_emotions = dict(parent.emotions or {})
            for k, v in (spec.seed_emotions or {}).items():
                seeded_emotions[k] = float(v)

            # Seed issue_stance: parent + spec.issue_stance (spec wins)
            seeded_stance = dict(parent.issue_stance or {})
            for k, v in (spec.issue_stance or {}).items():
                seeded_stance[k] = float(v)

            child = CohortStateModel(
                cohort_id=child_id,
                tick=current_tick,
                universe_id=parent.universe_id,
                archetype_id=parent.archetype_id,
                parent_cohort_id=parent.cohort_id,
                child_cohort_ids=[],
                represented_population=spec.represented_population,
                population_share_of_archetype=parent.population_share_of_archetype
                * share,
                issue_stance=seeded_stance,
                expression_level=spec.expression_level,
                mobilization_mode=spec.mobilization_mode,
                speech_mode=spec.speech_mode,
                emotions=seeded_emotions,
                behavior_state=dict(parent.behavior_state or {}),
                attention=parent.attention,
                fatigue=parent.fatigue,
                grievance=parent.grievance,
                perceived_efficacy=parent.perceived_efficacy,
                perceived_majority=dict(parent.perceived_majority or {}),
                fear_of_isolation=parent.fear_of_isolation,
                willingness_to_speak=parent.willingness_to_speak,
                identity_salience=parent.identity_salience,
                visible_trust_summary=dict(parent.visible_trust_summary or {}),
                exposure_summary=dict(parent.exposure_summary or {}),
                dependency_summary=dict(parent.dependency_summary or {}),
                memory_session_id=None,
                recent_post_ids=[],
                queued_event_ids=[],
                previous_action_ids=[],
                prompt_temperature=parent.prompt_temperature,
                representation_mode=parent.representation_mode,
                allowed_tools=list(parent.allowed_tools or []),
                is_active=True,
            )
            session.add(child)
            children.append(child)
            parent.child_cohort_ids = parent.child_cohort_ids + [child_id]

        await session.flush()

    _log.info(
        "split committed: parent=%s -> children=%s (universe=%s, tick=%d)",
        parent.cohort_id,
        [c.cohort_id for c in children],
        parent.universe_id,
        current_tick,
    )
    return children


# ---------------------------------------------------------------------------
# MERGE
# ---------------------------------------------------------------------------


def _stance_distance(a: dict[str, float], b: dict[str, float]) -> float:
    """Euclidean distance over the union of axis keys."""
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    s = 0.0
    for k in keys:
        d = float(a.get(k, 0.0)) - float(b.get(k, 0.0))
        s += d * d
    return float(s**0.5)


def evaluate_merge_validity(
    *,
    cohorts: list[CohortState],
    proposal: MergeProposal,
    params: SociologyParams,
    low_divergence_window_ok: bool = True,
) -> tuple[bool, str | None]:
    """Pure validator. Returns (ok, error_message). PRD §12.8."""
    if len(cohorts) < 2:
        return False, "merge requires at least 2 cohorts"
    archetype_ids = {c.archetype_id for c in cohorts}
    if len(archetype_ids) > 1:
        return (
            False,
            f"merge cohorts have differing archetypes: {sorted(archetype_ids)}",
        )
    if proposal.archetype_id not in archetype_ids:
        return (
            False,
            f"proposal.archetype_id={proposal.archetype_id} not in cohorts",
        )

    threshold_dist = 1.0 - params.split_merge.merge_similarity_threshold
    expr_threshold = max(0.05, threshold_dist)

    # Pairwise stance + expression similarity
    for i in range(len(cohorts)):
        for j in range(i + 1, len(cohorts)):
            d = _stance_distance(cohorts[i].issue_stance, cohorts[j].issue_stance)
            if d > threshold_dist:
                return (
                    False,
                    f"cohorts[{i}] and cohorts[{j}] stance distance "
                    f"{d:.4f} > {threshold_dist:.4f}",
                )
            if (
                abs(cohorts[i].expression_level - cohorts[j].expression_level)
                > expr_threshold
            ):
                return (
                    False,
                    f"cohorts[{i}] and cohorts[{j}] expression diverge",
                )

    if not low_divergence_window_ok:
        return (
            False,
            f"merge requires low divergence for "
            f"{params.split_merge.merge_low_divergence_ticks} ticks",
        )

    return True, None


async def commit_merge(
    session: AsyncSession,
    *,
    cohorts: list[CohortStateModel],
    proposal: MergeProposal,
    current_tick: int,
    archetype: PopulationArchetype,
) -> CohortStateModel:
    """Commit a merge inside a transaction. Caller is responsible for
    pre-validation via :func:`evaluate_merge_validity`."""
    if not cohorts:
        raise ValueError("commit_merge requires at least one cohort")
    universe_id = cohorts[0].universe_id

    total_pop = sum(c.represented_population for c in cohorts)
    if total_pop <= 0:
        raise ValueError("merged population would be zero")

    weights = [c.represented_population / total_pop for c in cohorts]

    def wavg_dict(field: str) -> dict[str, float]:
        keys: set[str] = set()
        for c in cohorts:
            keys |= set(getattr(c, field) or {})
        out: dict[str, float] = {}
        for k in keys:
            out[k] = sum(
                w * float((getattr(c, field) or {}).get(k, 0.0))
                for c, w in zip(cohorts, weights, strict=True)
            )
        return out

    def wavg_scalar(field: str) -> float:
        return sum(
            w * float(getattr(c, field))
            for c, w in zip(cohorts, weights, strict=True)
        )

    merged_id = new_id("coh")

    async with session.begin_nested() if session.in_transaction() else session.begin():
        for c in cohorts:
            c.is_active = False

        merged = CohortStateModel(
            cohort_id=merged_id,
            tick=current_tick,
            universe_id=universe_id,
            archetype_id=proposal.archetype_id,
            parent_cohort_id=None,
            child_cohort_ids=[],
            represented_population=total_pop,
            population_share_of_archetype=min(
                1.0, sum(c.population_share_of_archetype for c in cohorts)
            ),
            issue_stance=wavg_dict("issue_stance"),
            expression_level=max(0.0, min(1.0, wavg_scalar("expression_level"))),
            mobilization_mode=cohorts[0].mobilization_mode,
            speech_mode=cohorts[0].speech_mode,
            emotions=wavg_dict("emotions"),
            behavior_state=wavg_dict("behavior_state"),
            attention=max(0.0, min(1.0, wavg_scalar("attention"))),
            fatigue=max(0.0, min(1.0, wavg_scalar("fatigue"))),
            grievance=wavg_scalar("grievance"),
            perceived_efficacy=wavg_scalar("perceived_efficacy"),
            perceived_majority=wavg_dict("perceived_majority"),
            fear_of_isolation=wavg_scalar("fear_of_isolation"),
            willingness_to_speak=wavg_scalar("willingness_to_speak"),
            identity_salience=wavg_scalar("identity_salience"),
            visible_trust_summary=dict(cohorts[0].visible_trust_summary or {}),
            exposure_summary=dict(cohorts[0].exposure_summary or {}),
            dependency_summary=dict(cohorts[0].dependency_summary or {}),
            memory_session_id=None,
            recent_post_ids=[],
            queued_event_ids=[],
            previous_action_ids=[],
            prompt_temperature=wavg_scalar("prompt_temperature"),
            representation_mode=cohorts[0].representation_mode,
            allowed_tools=list(cohorts[0].allowed_tools or []),
            is_active=True,
        )
        session.add(merged)
        await session.flush()

    _log.info(
        "merge committed: cohorts=%s -> %s (universe=%s, tick=%d, pop=%d)",
        [c.cohort_id for c in cohorts],
        merged_id,
        universe_id,
        current_tick,
        total_pop,
    )
    return merged


# ---------------------------------------------------------------------------
# AUDITOR
# ---------------------------------------------------------------------------


async def audit_population_conservation(
    session: AsyncSession,
    universe_id: str,
    current_tick: int,
) -> list[str]:
    """For each archetype touched in this universe, check that the
    sum of active cohort populations at `current_tick` equals the
    archetype's `population_total`. Returns a list of error strings;
    empty list means OK. Logs CRITICAL on any mismatch.
    """
    from backend.app.models.cohorts import PopulationArchetypeModel

    errors: list[str] = []

    cohort_q = select(CohortStateModel).where(
        CohortStateModel.universe_id == universe_id,
        CohortStateModel.tick == current_tick,
        CohortStateModel.is_active.is_(True),
    )
    cohorts = (await session.execute(cohort_q)).scalars().all()

    by_archetype: dict[str, int] = {}
    for c in cohorts:
        by_archetype[c.archetype_id] = (
            by_archetype.get(c.archetype_id, 0) + c.represented_population
        )

    if not by_archetype:
        return errors

    arch_q = select(PopulationArchetypeModel).where(
        PopulationArchetypeModel.archetype_id.in_(list(by_archetype.keys()))
    )
    archetypes = (await session.execute(arch_q)).scalars().all()

    for arch in archetypes:
        observed = by_archetype.get(arch.archetype_id, 0)
        if observed != arch.population_total:
            msg = (
                f"population conservation FAIL: universe={universe_id} "
                f"tick={current_tick} archetype={arch.archetype_id} "
                f"observed_active_pop={observed} != "
                f"archetype.population_total={arch.population_total}"
            )
            _log.critical(msg)
            errors.append(msg)

    return errors


__all__ = [
    "evaluate_split_validity",
    "commit_split",
    "evaluate_merge_validity",
    "commit_merge",
    "audit_population_conservation",
]
