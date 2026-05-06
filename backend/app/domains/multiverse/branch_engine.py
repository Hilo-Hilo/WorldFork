from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.labels import next_child_label, tick_label
from app.db import models
from app.domains.multiverse.runtime_config import branch_policy_for_multiverse, simulation_config_for_multiverse
from app.domains.tick.tick_bundles import hydrate_tick_bundle, inherited_tick_bundle_ref


def create_branch(
    db: Session,
    *,
    parent: models.Multiverse,
    fork_tick_index: int,
    reason: str,
    idempotency_key: str,
    branch_probability: float | None = None,
    parent_continuation_probability: float | None = None,
    probability_basis: dict[str, Any] | None = None,
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
    if branch_policy.get("branching_enabled") is False:
        raise ValueError("branch budget exceeded: branching disabled")
    max_depth = branch_policy.get("max_branch_depth", 3)
    max_active = branch_policy.get("max_active_multiverses", 12)
    max_per_tick = branch_policy.get("max_branches_per_tick", 2)
    min_runway = max(1, _optional_int(branch_policy.get("min_branch_runway_ticks")) or 1)
    simulation_config = simulation_config_for_multiverse(db, parent)
    max_ticks = _optional_int(simulation_config.get("max_ticks"))
    if max_ticks is not None and max_ticks - fork_tick_index < min_runway:
        raise ValueError(
            "branch budget exceeded: min_branch_runway_ticks "
            f"requires {min_runway} remaining ticks, got {max_ticks - fork_tick_index}"
        )

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
    probability = _resolve_branch_probability(
        branch_probability,
        allow_full_transfer=parent_continuation_probability is not None,
    )
    split = _split_parent_path_probability(
        parent=parent,
        branch_probability=probability,
        parent_continuation_probability=parent_continuation_probability,
    )
    parent.path_probability = split["parent_path_probability_after"]
    child_label = next_child_label(parent.ui_label, child_count)
    child = models.Multiverse(
        big_bang_id=parent.big_bang_id,
        parent_multiverse_id=parent.id,
        fork_tick_index=fork_tick_index,
        ui_label=child_label,
        depth=parent.depth + 1,
        status="active",
        branch_reason=reason,
        branch_probability=split["branch_probability"],
        path_probability=split["child_path_probability"],
        state=_child_state(
            db,
            parent=parent,
            fork_tick_index=fork_tick_index,
            reason=reason,
            source_tick=source_tick,
            probability_split=split,
            probability_basis=probability_basis,
        ),
    )
    db.add(child)
    db.flush()
    db.add(models.MultiverseLineageEdge(
        big_bang_id=parent.big_bang_id,
        parent_multiverse_id=parent.id,
        child_multiverse_id=child.id,
        fork_tick_index=fork_tick_index,
        reason=reason,
        branch_probability=split["branch_probability"],
        parent_path_probability=split["parent_path_probability_before"],
        child_path_probability=split["child_path_probability"],
        probability_basis=probability_basis or {
            "source": "default_branch_probability",
            "reason": "No explicit God-agent branch probability was supplied.",
        },
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
                    provisional_bundle=inherited_tick_bundle_ref(
                        parent=parent,
                        child=child,
                        source_tick=tick,
                        bundle_field="provisional_bundle",
                    ),
                    final_bundle=inherited_tick_bundle_ref(
                        parent=parent,
                        child=child,
                        source_tick=tick,
                        bundle_field="final_bundle",
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
    db: Session,
    *,
    parent: models.Multiverse,
    fork_tick_index: int,
    reason: str,
    source_tick: models.TickSnapshot,
    probability_split: dict[str, float],
    probability_basis: dict[str, Any] | None,
) -> dict:
    final_bundle = hydrate_tick_bundle(db, source_tick, "final_bundle")
    if not _bundle_has_payload(final_bundle):
        final_bundle = hydrate_tick_bundle(db, source_tick, "provisional_bundle")
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
        "branch_premise": _branch_premise(reason=reason, probability_basis=probability_basis),
        "prompt_instruction": (
            "Treat branch_premise as the local timeline premise for this child timeline. "
            "Explore plausible consequences of that alternate path while preserving uncertainty; "
            "do not force a terminal endpoint until path evidence supports it."
        ),
        "branch_probability": probability_split["branch_probability"],
        "path_probability": probability_split["child_path_probability"],
        "parent_path_probability_before": probability_split["parent_path_probability_before"],
        "parent_path_probability_after": probability_split["parent_path_probability_after"],
        "probability_basis": probability_basis or {},
    }
    return state


def _branch_premise(*, reason: str, probability_basis: dict[str, Any] | None) -> str:
    if isinstance(probability_basis, dict):
        for key in ("branch_premise", "premise", "alternate_path"):
            value = probability_basis.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return str(reason or "God Agent branch.").strip()


def _resolve_branch_probability(value: float | None, *, allow_full_transfer: bool = False) -> float:
    maximum = 1.0 if allow_full_transfer else 0.99
    return _clamp_probability(value, default=0.5, minimum=0.01, maximum=maximum)


def _optional_int(value) -> int | None:  # noqa: ANN001
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _split_parent_path_probability(
    *,
    parent: models.Multiverse,
    branch_probability: float,
    parent_continuation_probability: float | None,
) -> dict[str, float]:
    parent_before = _clamp_probability(getattr(parent, "path_probability", None), default=1.0, minimum=0.0, maximum=1.0)
    branch_probability = _resolve_branch_probability(
        branch_probability,
        allow_full_transfer=parent_continuation_probability is not None,
    )
    continuation = (
        _clamp_probability(parent_continuation_probability, default=1.0 - branch_probability, minimum=0.0, maximum=1.0)
        if parent_continuation_probability is not None
        else 1.0 - branch_probability
    )
    total = branch_probability + continuation
    if total <= 0:
        branch_probability = 0.5
        continuation = 0.5
    elif total < 1.0:
        continuation += 1.0 - total
    elif total > 1.0:
        branch_probability /= total
        continuation /= total
    child_path = parent_before * branch_probability
    parent_after = parent_before * continuation
    return {
        "branch_probability": round(branch_probability, 10),
        "parent_continuation_probability": round(continuation, 10),
        "parent_path_probability_before": round(parent_before, 10),
        "parent_path_probability_after": round(parent_after, 10),
        "child_path_probability": round(child_path, 10),
    }


def _clamp_probability(
    value: float | int | str | None,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed != parsed:
        return default
    return max(minimum, min(maximum, parsed))


def _bundle_has_payload(bundle: dict) -> bool:
    return any(key not in {"multiverse_id", "inherited_from"} for key in bundle)


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
    _inherit_latest_endpoint_ledger(db, parent=parent, child=child)


def _inherit_latest_endpoint_ledger(
    db: Session,
    *,
    parent: models.Multiverse,
    child: models.Multiverse,
) -> None:
    parent_ledger = db.scalar(
        select(models.EndpointLedgerVersion)
        .where(
            models.EndpointLedgerVersion.big_bang_id == parent.big_bang_id,
            models.EndpointLedgerVersion.multiverse_id == parent.id,
            models.EndpointLedgerVersion.scope == "multiverse",
        )
        .order_by(
            models.EndpointLedgerVersion.version.desc(),
            models.EndpointLedgerVersion.created_at.desc(),
            models.EndpointLedgerVersion.id.desc(),
        )
        .limit(1)
    )
    if parent_ledger is None:
        return
    existing_child_ledger = db.scalar(
        select(models.EndpointLedgerVersion.id)
        .where(
            models.EndpointLedgerVersion.big_bang_id == parent.big_bang_id,
            models.EndpointLedgerVersion.multiverse_id == child.id,
            models.EndpointLedgerVersion.scope == "multiverse",
        )
        .limit(1)
    )
    if existing_child_ledger is not None:
        return
    branch_context = _ledger_branch_context(child)

    child_ledger = models.EndpointLedgerVersion(
        big_bang_id=parent.big_bang_id,
        multiverse_id=child.id,
        scope="multiverse",
        version=1,
        status=parent_ledger.status,
        source_type="branch_inherited",
        source_tick_snapshot_id=parent_ledger.source_tick_snapshot_id,
        source_report_version_id=parent_ledger.source_report_version_id,
        parent_ledger_version_id=parent_ledger.id,
        created_by="branch_engine",
        summary=f"Inherited endpoint ledger from {parent.ui_label} at branch creation.",
        model=parent_ledger.model,
        llm_call_id=parent_ledger.llm_call_id,
        payload={
            "inherited_from_ledger_version_id": str(parent_ledger.id),
            "inherited_from_multiverse_id": str(parent.id),
            "branch_context": branch_context,
            "source_payload": deepcopy(parent_ledger.payload or {}),
        },
    )
    db.add(child_ledger)
    db.flush()

    parent_entries = db.scalars(
        select(models.EndpointLedgerEntry)
        .where(models.EndpointLedgerEntry.ledger_version_id == parent_ledger.id)
        .order_by(models.EndpointLedgerEntry.endpoint_key)
    ).all()
    for entry in parent_entries:
        entry_meta = deepcopy(entry.meta or {})
        branch_signature = branch_context.get("branch_hypothesis_signature")
        db.add(
            models.EndpointLedgerEntry(
                ledger_version_id=child_ledger.id,
                endpoint_key=entry.endpoint_key,
                label=entry.label,
                description=entry.description,
                status=entry.status,
                probability=entry.probability,
                realization_criteria=deepcopy(entry.realization_criteria or []),
                authority_refs=deepcopy(entry.authority_refs or []),
                evidence_refs=deepcopy(entry.evidence_refs or []),
                negative_evidence_refs=deepcopy(entry.negative_evidence_refs or []),
                blockers=deepcopy(entry.blockers or []),
                status_basis=entry.status_basis,
                contradiction_notes=entry.contradiction_notes,
                rationale=entry.rationale,
                last_observed_tick_index=entry.last_observed_tick_index,
                meta={
                    **entry_meta,
                    "inherited_from_ledger_entry_id": str(entry.id),
                    "inherited_from_ledger_version_id": str(parent_ledger.id),
                    "inherited_from_multiverse_id": str(parent.id),
                    "branch_premise": branch_context.get("branch_premise"),
                    "branch_hypothesis_signature": branch_signature,
                },
            )
        )


def _ledger_branch_context(child: models.Multiverse) -> dict[str, Any]:
    state = child.state if isinstance(child.state, dict) else {}
    branch = state.get("branch") if isinstance(state.get("branch"), dict) else {}
    premise = branch.get("branch_premise") or branch.get("reason") or child.branch_reason
    if not isinstance(premise, str) or not premise.strip():
        return {}
    context = {
        "fork_tick_index": branch.get("fork_tick_index") if branch else child.fork_tick_index,
        "branch_premise": premise.strip(),
        "branch_probability": branch.get("branch_probability") if branch else child.branch_probability,
        "path_probability": branch.get("path_probability") if branch else child.path_probability,
        "probability_basis": deepcopy(branch.get("probability_basis") or {}),
    }
    context["branch_hypothesis_signature"] = _branch_hypothesis_signature(context)
    return {key: value for key, value in context.items() if value not in (None, {}, [])}


def _branch_hypothesis_signature(branch_context: dict[str, Any]) -> str:
    premise = str(branch_context.get("branch_premise") or "").strip().lower()
    basis = branch_context.get("probability_basis") if isinstance(branch_context.get("probability_basis"), dict) else {}
    basis_text = str(basis.get("branch_premise") or basis.get("premise") or basis.get("alternate_path") or "").strip().lower()
    text = " ".join(part for part in (premise, basis_text) if part)
    return " ".join(text.split())[:500] or "branch"


def _inherit_queued_events(
    db: Session,
    *,
    parent: models.Multiverse,
    child: models.Multiverse,
    fork_tick_index: int,
) -> None:
    branch_candidate_id = _branch_candidate_endpoint_id(child)
    events = db.scalars(
        select(models.Event).where(
            models.Event.multiverse_id == parent.id,
            models.Event.status == "queued",
            models.Event.created_tick <= fork_tick_index,
            models.Event.scheduled_tick > fork_tick_index,
        )
    ).all()
    for event in events:
        event_candidate_id = _event_candidate_endpoint_id(event)
        if branch_candidate_id in {"yes", "no"} and event_candidate_id in {"yes", "no"}:
            if event_candidate_id != branch_candidate_id:
                continue
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


def _branch_candidate_endpoint_id(child: models.Multiverse) -> str | None:
    state = child.state if isinstance(child.state, dict) else {}
    branch = state.get("branch") if isinstance(state.get("branch"), dict) else {}
    basis = branch.get("probability_basis") if isinstance(branch.get("probability_basis"), dict) else {}
    for value in (
        basis.get("candidate_endpoint_id"),
        basis.get("candidate_endpoint"),
        branch.get("candidate_endpoint_id"),
    ):
        normalized = str(value or "").strip().lower()
        if normalized in {"yes", "no"}:
            return normalized
    return None


def _event_candidate_endpoint_id(event: models.Event) -> str | None:
    for container in (event.expected_impact, event.actual_impact, event.meta):
        if not isinstance(container, dict):
            continue
        for key in ("candidate_endpoint_id", "endpoint", "outcome", "result"):
            normalized = str(container.get(key) or "").strip().lower()
            if normalized in {"yes", "no"}:
                return normalized
        summary = container.get("summary")
        if isinstance(summary, dict):
            for key in ("candidate_endpoint_id", "endpoint", "outcome", "result"):
                normalized = str(summary.get(key) or "").strip().lower()
                if normalized in {"yes", "no"}:
                    return normalized
    return None


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
