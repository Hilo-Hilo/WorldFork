"""
Per-tick orchestration of the sociology layer.

Loads the current state slice for one universe at one tick, runs each
transition (attention, expression, belief, identity, mobilization),
persists the post-state as the next tick's row.

The transition order follows the runtime sociology pipeline: attention → belief → expression
→ trust update → mobilization → identity. Splits/merges are NOT performed
here — they're triggered by LLM proposals and committed via
`commit_split` / `commit_merge`.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.cohorts import CohortStateModel
from backend.app.sociology.attention import update_attention
from backend.app.sociology.belief import update_beliefs
from backend.app.sociology.expression import (
    spiral_of_silence_gate,
    update_expression,
)
from backend.app.sociology.identity import update_identity_salience
from backend.app.sociology.parameters import load_sociology_params
from backend.app.sociology.thresholds import (
    mobilization_mode_transition,
    mobilization_score,
    will_mobilize,
)
from backend.app.sociology.trust import TrustGraph
from backend.app.storage.sot_loader import SoTBundle

_log = logging.getLogger(__name__)


async def run_all_transitions(
    *,
    session: AsyncSession,
    universe_id: str,
    current_tick: int,
    sot: SoTBundle,
    ledger: Any | None = None,
) -> dict[str, Any]:
    """Run the full per-tick sociology transition pipeline.

    Reads cohort rows at ``current_tick`` (active only), computes new state,
    inserts new cohort rows at ``current_tick`` reflecting the post-state.
    Returns a summary dict.

    Note: per the deliverable spec, the new state is written AT
    ``current_tick`` (not ``current_tick + 1``). The tick_runner is
    responsible for advancing ticks elsewhere.
    """
    params = load_sociology_params(sot)

    # ------------------------------------------------------------------
    # Load active cohorts for this universe at the prior tick.
    # If the caller has already inserted current_tick rows, prefer those;
    # otherwise read the most recent prior tick.
    # ------------------------------------------------------------------
    q = select(CohortStateModel).where(
        CohortStateModel.universe_id == universe_id,
        CohortStateModel.is_active.is_(True),
        CohortStateModel.tick <= current_tick,
    )
    rows = (await session.execute(q)).scalars().all()

    # Keep only the latest tick row per cohort_id
    latest_by_cohort: dict[str, CohortStateModel] = {}
    for r in rows:
        existing = latest_by_cohort.get(r.cohort_id)
        if existing is None or r.tick > existing.tick:
            latest_by_cohort[r.cohort_id] = r
    cohort_rows = list(latest_by_cohort.values())

    if not cohort_rows:
        return {
            "universe_id": universe_id,
            "tick": current_tick,
            "cohorts_updated": 0,
            "mobilized": 0,
            "silenced": 0,
        }

    cohort_states = [r.to_schema() for r in cohort_rows]
    cohort_ids = [c.cohort_id for c in cohort_states]

    # ------------------------------------------------------------------
    # Build trust graph from existing cohort visible_trust_summary.
    # Build a basic exposure matrix from cohort exposure_summary.
    # ------------------------------------------------------------------
    trust_graph = TrustGraph.from_cohort_states(cohort_states)
    T = trust_graph.to_matrix(cohort_ids)
    # Exposure matrix: derive from exposure_summary if present, else uniform.
    n = len(cohort_ids)
    import numpy as np

    E = np.full((n, n), 1.0 / max(n - 1, 1), dtype=np.float64)
    np.fill_diagonal(E, 0.0)

    # Issue stance axes: union of every cohort's keys.
    axes: set[str] = set()
    for c in cohort_states:
        axes.update(c.issue_stance.keys())
    axes_list = sorted(axes)

    # ------------------------------------------------------------------
    # 1) Attention update — apply with zeros for now (no event signal in
    #    this stripped-down loop; tick_runner injects real saliences).
    # ------------------------------------------------------------------
    new_attentions: dict[str, float] = {}
    for c in cohort_states:
        new_attentions[c.cohort_id] = update_attention(
            cohort=c,
            event_salience=0.0,
            feed_salience=0.0,
            personal_impact=0.0,
            identity_threat=0.0,
            params=params,
        )

    # ------------------------------------------------------------------
    # 2) Belief drift — vectorized per axis.
    # ------------------------------------------------------------------
    new_beliefs = update_beliefs(
        cohorts=cohort_states,
        trust_matrix=T,
        exposure_matrix=E,
        axes=axes_list,
        params=params,
    )

    # ------------------------------------------------------------------
    # 3) Expression update + spiral-of-silence gating.
    # ------------------------------------------------------------------
    new_expr: dict[str, float] = {}
    silenced_ids: set[str] = set()
    for c in cohort_states:
        ex = update_expression(cohort=c, params=params)
        new_expr[c.cohort_id] = ex
        # Spiral-of-silence: apply a stance-neutral measure of minority status.
        minority = 1.0 - sum(c.perceived_majority.values()) / max(
            len(c.perceived_majority), 1
        ) if c.perceived_majority else 0.0
        if spiral_of_silence_gate(
            cohort=c,
            perceived_minority_status=max(0.0, min(1.0, minority)),
            institutional_risk=float(
                c.behavior_state.get("legal_or_status_risk_sensitivity", 0.0)
            ),
            params=params,
        ):
            silenced_ids.add(c.cohort_id)

    # ------------------------------------------------------------------
    # 4) Mobilization scoring + mode transition.
    # ------------------------------------------------------------------
    new_mob_mode: dict[str, str] = {}
    mobilized_ids: set[str] = set()
    for c in cohort_states:
        score = mobilization_score(
            cohort=c,
            trusted_peer_participation=0.0,
            params=params,
        )
        new_mob_mode[c.cohort_id] = mobilization_mode_transition(
            cohort=c,
            score=score,
        )
        if will_mobilize(score, params):
            mobilized_ids.add(c.cohort_id)

    # ------------------------------------------------------------------
    # 5) Identity salience.
    # ------------------------------------------------------------------
    new_identity: dict[str, float] = {}
    for c in cohort_states:
        new_identity[c.cohort_id] = update_identity_salience(
            cohort=c,
            identity_threat_signal=0.0,
            ingroup_event_signal=0.0,
            params=params,
        )

    # ------------------------------------------------------------------
    # 6) Persist updates.
    #    For each affected cohort, either UPDATE the existing current_tick
    #    row or INSERT a new one at current_tick.
    # ------------------------------------------------------------------
    updated = 0
    for c, row in zip(cohort_states, cohort_rows, strict=True):
        target = row
        if row.tick != current_tick:
            # Need a new row at current_tick. Build from prior row.
            new_row = CohortStateModel(
                cohort_id=row.cohort_id,
                tick=current_tick,
                universe_id=row.universe_id,
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
                is_active=True,
            )
            session.add(new_row)
            target = new_row

        # Apply transitions to the (possibly new) row at current_tick
        target.attention = new_attentions[c.cohort_id]
        new_stance = dict(target.issue_stance or {})
        new_stance.update(new_beliefs[c.cohort_id])
        target.issue_stance = new_stance
        target.expression_level = new_expr[c.cohort_id]
        target.mobilization_mode = new_mob_mode[c.cohort_id]
        target.identity_salience = new_identity[c.cohort_id]
        if c.cohort_id in silenced_ids:
            target.speech_mode = "silent"
        updated += 1

    await session.flush()

    summary = {
        "universe_id": universe_id,
        "tick": current_tick,
        "cohorts_updated": updated,
        "mobilized": len(mobilized_ids),
        "silenced": len(silenced_ids),
    }

    if ledger is not None:
        try:
            ledger.write_artifact(  # pragma: no cover - ledger is optional
                f"sociology/{universe_id}/tick-{current_tick}.json",
                summary,
            )
        except Exception:  # pragma: no cover - never let ledger fail the tick
            _log.warning("ledger write failed for sociology summary", exc_info=True)

    return summary


__all__ = ["run_all_transitions"]
