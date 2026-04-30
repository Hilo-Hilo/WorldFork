from __future__ import annotations

from copy import deepcopy

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.labels import next_child_label, tick_label
from app.db import models
from app.simulation.runtime_config import branch_policy_for_multiverse


def create_branch(
    db: Session,
    *,
    parent: models.Multiverse,
    fork_tick_index: int,
    reason: str,
    idempotency_key: str,
) -> models.Multiverse:
    if fork_tick_index < 0:
        raise ValueError("fork_tick_index must be non-negative")
    source_tick = db.scalar(
        select(models.TickSnapshot).where(
            models.TickSnapshot.big_bang_id == parent.big_bang_id,
            models.TickSnapshot.multiverse_id == parent.id,
            models.TickSnapshot.tick_index == fork_tick_index,
        )
    )
    if source_tick is None:
        latest_tick_index = db.scalar(
            select(func.max(models.TickSnapshot.tick_index)).where(
                models.TickSnapshot.big_bang_id == parent.big_bang_id,
                models.TickSnapshot.multiverse_id == parent.id,
            )
        )
        if latest_tick_index is not None and fork_tick_index > latest_tick_index:
            raise ValueError("fork_tick_index is in the future")
        raise ValueError("fork_tick_index must reference an existing parent tick")

    existing_tool = db.scalar(select(models.ToolCall).where(models.ToolCall.idempotency_key == idempotency_key))
    if existing_tool and (existing_tool.result or {}).get("child_multiverse_id"):
        return db.get(models.Multiverse, existing_tool.result["child_multiverse_id"])

    branch_policy = branch_policy_for_multiverse(db, parent)
    max_depth = branch_policy.get("max_branch_depth", 3)
    max_active = branch_policy.get("max_active_multiverses", 12)
    max_per_tick = branch_policy.get("max_branches_per_tick", 2)

    active_count = db.scalar(
        select(func.count()).select_from(models.Multiverse).where(
            models.Multiverse.big_bang_id == parent.big_bang_id,
            models.Multiverse.status == "active",
        )
    )
    if active_count >= max_active:
        raise ValueError("branch budget exceeded: max_active_multiverses")
    if parent.depth + 1 > max_depth:
        raise ValueError("branch budget exceeded: max_branch_depth")

    tick_branch_count = db.scalar(
        select(func.count()).select_from(models.MultiverseLineageEdge).where(
            models.MultiverseLineageEdge.parent_multiverse_id == parent.id,
            models.MultiverseLineageEdge.fork_tick_index == fork_tick_index,
        )
    )
    if tick_branch_count >= max_per_tick:
        raise ValueError("branch budget exceeded: max_branches_per_tick")

    child_count = db.scalar(
        select(func.count()).select_from(models.Multiverse).where(models.Multiverse.parent_multiverse_id == parent.id)
    )
    child_label = next_child_label(parent.ui_label, child_count)
    child = models.Multiverse(
        big_bang_id=parent.big_bang_id,
        parent_multiverse_id=parent.id,
        fork_tick_index=fork_tick_index,
        ui_label=child_label,
        depth=parent.depth + 1,
        status="active",
        branch_reason=reason,
        state=_child_state(parent=parent, fork_tick_index=fork_tick_index, reason=reason, source_tick=source_tick),
    )
    db.add(child)
    db.flush()
    db.add(models.MultiverseLineageEdge(
        big_bang_id=parent.big_bang_id,
        parent_multiverse_id=parent.id,
        child_multiverse_id=child.id,
        fork_tick_index=fork_tick_index,
        reason=reason,
    ))

    inherited_ticks = db.scalars(
        select(models.TickSnapshot).where(
            models.TickSnapshot.multiverse_id == parent.id,
            models.TickSnapshot.tick_index <= fork_tick_index,
        ).order_by(models.TickSnapshot.tick_index)
    ).all()
    for tick in inherited_ticks:
        db.add(models.TickLineageRef(
            child_multiverse_id=child.id,
            source_multiverse_id=parent.id,
            source_tick_snapshot_id=tick.id,
            inherited_tick_index=tick.tick_index,
            inherited_ui_label=tick_label(child.ui_label, tick.tick_index),
        ))
        if not db.scalar(
            select(models.TickSnapshot).where(
                models.TickSnapshot.multiverse_id == child.id,
                models.TickSnapshot.tick_index == tick.tick_index,
            )
        ):
            db.add(
                models.TickSnapshot(
                    big_bang_id=parent.big_bang_id,
                    multiverse_id=child.id,
                    tick_index=tick.tick_index,
                    ui_label=tick_label(child.ui_label, tick.tick_index),
                    status=tick.status,
                    provisional_bundle=_inherited_tick_bundle(
                        tick.provisional_bundle,
                        parent=parent,
                        child=child,
                        source_tick=tick,
                    ),
                    final_bundle=_inherited_tick_bundle(
                        tick.final_bundle,
                        parent=parent,
                        child=child,
                        source_tick=tick,
                    ),
                    summary=tick.summary,
                    artifact_id=None,
                    idempotency_key=f"{child.id}:tick:{tick.tick_index}:inherited",
                )
            )
    _inherit_executable_state(db, parent=parent, child=child, fork_tick_index=fork_tick_index)
    db.flush()
    return child


def _child_state(
    *,
    parent: models.Multiverse,
    fork_tick_index: int,
    reason: str,
    source_tick: models.TickSnapshot,
) -> dict:
    final_bundle = deepcopy(source_tick.final_bundle or source_tick.provisional_bundle or {})
    sociology_result = final_bundle.get("sociology_result") or {}
    idle_assessment = final_bundle.get("idle_assessment") or {}
    state = {
        "last_tick_index": fork_tick_index,
        "last_executed_events": deepcopy(final_bundle.get("executed_events") or []),
        "last_sociology": sociology_result,
        "graph_summary": deepcopy(sociology_result.get("graph_summary") or {}),
        "cohort_current_states": deepcopy(sociology_result.get("cohort_state_updates") or []),
        "hero_current_states": deepcopy(sociology_result.get("hero_state_updates") or []),
        "idle_assessment": idle_assessment,
        "idle_streak": int(idle_assessment.get("idle_streak") or 0),
    }
    state["branch"] = {
        "parent_multiverse_id": str(parent.id),
        "fork_tick_index": fork_tick_index,
        "reason": reason,
    }
    return state


def _inherited_tick_bundle(
    bundle: dict | None,
    *,
    parent: models.Multiverse,
    child: models.Multiverse,
    source_tick: models.TickSnapshot,
) -> dict:
    inherited = deepcopy(bundle or {})
    inherited["multiverse_id"] = str(child.id)
    inherited["inherited_from"] = {
        "source_multiverse_id": str(parent.id),
        "source_tick_snapshot_id": str(source_tick.id),
        "source_ui_label": source_tick.ui_label,
    }
    return inherited


def _inherit_executable_state(
    db: Session,
    *,
    parent: models.Multiverse,
    child: models.Multiverse,
    fork_tick_index: int,
) -> None:
    _inherit_queued_events(db, parent=parent, child=child, fork_tick_index=fork_tick_index)
    _inherit_latest_actor_state_rows(
        db, models.CohortState, parent=parent, child=child, fork_tick_index=fork_tick_index
    )
    _inherit_latest_actor_state_rows(
        db, models.HeroState, parent=parent, child=child, fork_tick_index=fork_tick_index
    )
    _inherit_latest_graph_edges(db, parent=parent, child=child, fork_tick_index=fork_tick_index)
    _inherit_prompt_influences(db, parent=parent, child=child, fork_tick_index=fork_tick_index)


def _inherit_queued_events(
    db: Session,
    *,
    parent: models.Multiverse,
    child: models.Multiverse,
    fork_tick_index: int,
) -> None:
    events = db.scalars(
        select(models.Event).where(
            models.Event.multiverse_id == parent.id,
            models.Event.status == "queued",
            models.Event.created_tick <= fork_tick_index,
            models.Event.scheduled_tick > fork_tick_index,
        )
    ).all()
    for event in events:
        inherited_event = models.Event(
            big_bang_id=event.big_bang_id,
            multiverse_id=child.id,
            creator_actor_id=event.creator_actor_id,
            event_type=event.event_type,
            created_tick=event.created_tick,
            scheduled_tick=event.scheduled_tick,
            status=event.status,
            title=event.title,
            description=event.description,
            expected_impact=deepcopy(event.expected_impact or {}),
            actual_impact=deepcopy(event.actual_impact or {}),
            meta={
                **deepcopy(event.meta or {}),
                "inherited_from_event_id": str(event.id),
                "source_multiverse_id": str(parent.id),
            },
        )
        db.add(inherited_event)
        db.flush()
        current_revision_id = None
        revisions = db.scalars(
            select(models.EventRevision)
            .where(models.EventRevision.event_id == event.id)
            .order_by(models.EventRevision.revision_number)
        ).all()
        latest_revision_id = None
        for revision in revisions:
            inherited_revision = models.EventRevision(
                event_id=inherited_event.id,
                revision_number=revision.revision_number,
                edited_by_actor_id=revision.edited_by_actor_id,
                edited_by_agent_type=revision.edited_by_agent_type,
                edit_reason=revision.edit_reason,
                title=revision.title,
                description=revision.description,
                scheduled_tick=revision.scheduled_tick,
                preconditions=deepcopy(revision.preconditions or {}),
                expected_impact=deepcopy(revision.expected_impact or {}),
            )
            db.add(inherited_revision)
            db.flush()
            latest_revision_id = inherited_revision.id
            if revision.id == event.current_revision_id:
                current_revision_id = inherited_revision.id
        inherited_event.current_revision_id = current_revision_id or latest_revision_id


def _inherit_latest_actor_state_rows(
    db: Session,
    model,
    *,
    parent: models.Multiverse,
    child: models.Multiverse,
    fork_tick_index: int,
) -> None:
    latest = {}
    rows = db.scalars(
        select(model)
        .where(model.multiverse_id == parent.id, model.tick_index <= fork_tick_index)
        .order_by(model.tick_index.desc(), model.created_at.desc())
    ).all()
    for row in rows:
        key = row.actor_id or row.id
        if key in latest:
            continue
        latest[key] = row
        db.add(
            model(
                big_bang_id=row.big_bang_id,
                multiverse_id=child.id,
                actor_id=row.actor_id,
                tick_index=row.tick_index,
                state=deepcopy(row.state or {}),
                queued_event_ids=deepcopy(row.queued_event_ids or []),
            )
        )


def _inherit_latest_graph_edges(
    db: Session,
    *,
    parent: models.Multiverse,
    child: models.Multiverse,
    fork_tick_index: int,
) -> None:
    latest = {}
    rows = db.scalars(
        select(models.GraphEdge)
        .where(models.GraphEdge.multiverse_id == parent.id, models.GraphEdge.tick_index <= fork_tick_index)
        .order_by(models.GraphEdge.tick_index.desc(), models.GraphEdge.created_at.desc())
    ).all()
    for edge in rows:
        key = (edge.source_actor_id, edge.target_actor_id, edge.layer)
        if key in latest:
            continue
        latest[key] = edge
        db.add(
            models.GraphEdge(
                big_bang_id=edge.big_bang_id,
                multiverse_id=child.id,
                tick_index=edge.tick_index,
                source_actor_id=edge.source_actor_id,
                target_actor_id=edge.target_actor_id,
                layer=edge.layer,
                weight=edge.weight,
                payload={
                    **deepcopy(edge.payload or {}),
                    "inherited_from_graph_edge_id": str(edge.id),
                    "source_multiverse_id": str(parent.id),
                },
            )
        )


def _inherit_prompt_influences(
    db: Session,
    *,
    parent: models.Multiverse,
    child: models.Multiverse,
    fork_tick_index: int,
) -> None:
    influences = db.scalars(
        select(models.SociologyPromptInfluence).where(
            models.SociologyPromptInfluence.multiverse_id == parent.id,
            models.SociologyPromptInfluence.tick_index <= fork_tick_index,
            models.SociologyPromptInfluence.applies_to_tick_index > fork_tick_index,
        )
    ).all()
    for influence in influences:
        db.add(
            models.SociologyPromptInfluence(
                big_bang_id=influence.big_bang_id,
                multiverse_id=child.id,
                actor_id=influence.actor_id,
                tick_index=influence.tick_index,
                applies_to_tick_index=influence.applies_to_tick_index,
                influence=deepcopy(influence.influence or {}),
            )
        )
