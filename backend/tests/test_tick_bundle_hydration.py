from __future__ import annotations

import json
import warnings
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api import agent as agent_api
from app.api import multiverses as multiverses_api
from app.api import ticks as ticks_api
from app.db import models
from app.main import app
from app.simulation import report_engine
from app.simulation.branch_engine import create_branch
from app.simulation.tick_bundles import (
    TICK_BUNDLE_REF_KEY,
    TickBundleHydrationError,
    hydrate_tick_bundle,
    hydrate_tick_snapshot_for_read,
    inherited_tick_bundle_ref,
    is_inherited_tick_bundle_ref,
)


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    models.Base.metadata.create_all(engine)
    session = Session(engine)
    try:
        yield session
    finally:
        session.close()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Can't sort tables for DROP", category=SAWarning)
            models.Base.metadata.drop_all(engine)


def test_branch_stores_compact_refs_and_api_reads_hydrated_shape(db: Session):
    _big_bang, root, root_tick = _seed_root_tick(db)

    child = create_branch(
        db,
        parent=root,
        fork_tick_index=0,
        reason="copy-on-write branch",
        idempotency_key="branch:child",
    )
    child_tick = _tick(db, child, 0)

    assert is_inherited_tick_bundle_ref(child_tick.provisional_bundle)
    assert is_inherited_tick_bundle_ref(child_tick.final_bundle)
    assert len(json.dumps(child_tick.final_bundle)) < len(json.dumps(root_tick.final_bundle))

    hydrated = hydrate_tick_snapshot_for_read(db, child_tick)
    assert TICK_BUNDLE_REF_KEY not in hydrated.final_bundle
    assert hydrated.final_bundle["large_evidence"] == root_tick.final_bundle["large_evidence"]
    assert hydrated.final_bundle["multiverse_id"] == str(child.id)
    assert hydrated.final_bundle["inherited_from"] == {
        "source_multiverse_id": str(root.id),
        "source_tick_snapshot_id": str(root_tick.id),
        "source_ui_label": root_tick.ui_label,
    }

    list_payload = multiverses_api.ticks(child.id, db=db)
    tick_payload = ticks_api.get(child_tick.id, db=db)
    details_payload = ticks_api.details(child_tick.id, db=db)
    lineage_payload = multiverses_api.lineage(child.id, db=db)

    assert list_payload[0].final_bundle == hydrated.final_bundle
    assert tick_payload.final_bundle == hydrated.final_bundle
    assert details_payload["final_bundle"] == hydrated.final_bundle
    assert lineage_payload["inherited_ticks"][0].source_tick_snapshot_id == root_tick.id


def test_nested_inherited_ticks_hydrate_through_multiple_lineage_levels(db: Session):
    _big_bang, root, root_tick = _seed_root_tick(db)
    child = create_branch(
        db,
        parent=root,
        fork_tick_index=0,
        reason="first branch",
        idempotency_key="branch:child",
    )
    child_tick = _tick(db, child, 0)

    grandchild = create_branch(
        db,
        parent=child,
        fork_tick_index=0,
        reason="nested branch",
        idempotency_key="branch:grandchild",
    )
    grandchild_tick = _tick(db, grandchild, 0)

    assert grandchild_tick.final_bundle[TICK_BUNDLE_REF_KEY]["source_tick_snapshot_id"] == str(child_tick.id)
    hydrated = hydrate_tick_snapshot_for_read(db, grandchild_tick)

    assert hydrated.final_bundle["large_evidence"] == root_tick.final_bundle["large_evidence"]
    assert hydrated.final_bundle["multiverse_id"] == str(grandchild.id)
    assert hydrated.final_bundle["inherited_from"] == {
        "source_multiverse_id": str(child.id),
        "source_tick_snapshot_id": str(child_tick.id),
        "source_ui_label": child_tick.ui_label,
    }
    assert grandchild.state["last_sociology"]["cohort_state_updates"][0]["cohort_id"] == "cohort-a"


def test_agent_trace_transcript_and_report_content_read_hydrated_inherited_ticks(db: Session):
    _big_bang, root, _root_tick = _seed_root_tick(db)
    child = create_branch(
        db,
        parent=root,
        fork_tick_index=0,
        reason="reader branch",
        idempotency_key="branch:reader",
    )
    child_tick = _tick(db, child, 0)

    trace = agent_api.trace(child.id, db=db, tick=0, verbosity="full")
    transcript = agent_api.cohort_transcript("cohort-a", child.id, db=db, from_tick=0, to_tick=0)
    content = report_engine._build_multiverse_report_content(
        db,
        multiverse=child,
        title="Child report",
        summary=None,
        report_version_number=1,
        latest_tick=child_tick,
    )

    assert trace["data"]["actor_count"] == 2
    assert trace["data"]["tick_snapshot"]["final_bundle"]["branch_score"] == 0.91
    assert transcript["data"][0]["name"] == "Cohort A"
    assert content["outcome_distribution"]["latest_branch_score"] == 0.91
    assert content["sections"][1]["table"][0]["god_decision"] == "continue"
    assert content["sections"][3]["items"][0]["count"] == 1


def test_corrupt_inherited_tick_refs_fail_clearly(db: Session):
    _big_bang, root, root_tick = _seed_root_tick(db)
    child = _add_child_multiverse(db, root, ui_label="M1.a")
    child_tick = models.TickSnapshot(
        big_bang_id=root.big_bang_id,
        multiverse_id=child.id,
        tick_index=0,
        ui_label="M1.a.T0",
        status="final",
        provisional_bundle={},
        final_bundle=inherited_tick_bundle_ref(
            parent=root,
            child=child,
            source_tick=root_tick,
            bundle_field="final_bundle",
        ),
    )
    db.add(child_tick)
    db.flush()

    with pytest.raises(TickBundleHydrationError, match="missing lineage ref"):
        hydrate_tick_bundle(db, child_tick, "final_bundle")

    missing_source_id = uuid4()
    child_tick.final_bundle = {
        TICK_BUNDLE_REF_KEY: {
            "version": 1,
            "bundle_field": "final_bundle",
            "source_tick_snapshot_id": str(missing_source_id),
        },
        "inherited_from": {"source_tick_snapshot_id": str(missing_source_id)},
    }
    db.add(
        models.TickLineageRef(
            child_multiverse_id=child.id,
            source_multiverse_id=root.id,
            source_tick_snapshot_id=missing_source_id,
            inherited_tick_index=0,
            inherited_ui_label=child_tick.ui_label,
        )
    )
    db.flush()

    with pytest.raises(TickBundleHydrationError, match="missing source tick"):
        hydrate_tick_bundle(db, child_tick, "final_bundle")

    cycle_tick = models.TickSnapshot(
        big_bang_id=root.big_bang_id,
        multiverse_id=child.id,
        tick_index=1,
        ui_label="M1.a.T1",
        status="final",
        provisional_bundle={},
        final_bundle={},
    )
    db.add(cycle_tick)
    db.flush()
    cycle_tick.final_bundle = {
        TICK_BUNDLE_REF_KEY: {
            "version": 1,
            "bundle_field": "final_bundle",
            "source_tick_snapshot_id": str(cycle_tick.id),
        },
        "inherited_from": {"source_tick_snapshot_id": str(cycle_tick.id)},
    }
    db.add(
        models.TickLineageRef(
            child_multiverse_id=child.id,
            source_multiverse_id=child.id,
            source_tick_snapshot_id=cycle_tick.id,
            inherited_tick_index=1,
            inherited_ui_label=cycle_tick.ui_label,
        )
    )
    db.flush()

    with pytest.raises(TickBundleHydrationError, match="cycle"):
        hydrate_tick_bundle(db, cycle_tick, "final_bundle")


def test_tick_snapshot_openapi_contract_does_not_expose_internal_ref_marker():
    schema = TestClient(app).get("/openapi.json").json()
    tick_schema = schema["components"]["schemas"]["TickSnapshotOut"]

    assert set(tick_schema["properties"]) == {
        "id",
        "big_bang_id",
        "multiverse_id",
        "tick_index",
        "ui_label",
        "status",
        "provisional_bundle",
        "final_bundle",
        "summary",
        "artifact_id",
        "created_at",
        "updated_at",
    }
    assert TICK_BUNDLE_REF_KEY not in json.dumps(tick_schema)


def _seed_root_tick(db: Session) -> tuple[models.BigBang, models.Multiverse, models.TickSnapshot]:
    big_bang = models.BigBang(
        name="Tick hydration",
        description=None,
        scenario_input={},
        status="active",
        current_config_version=1,
    )
    db.add(big_bang)
    db.flush()
    db.add(
        models.BigBangConfig(
            big_bang_id=big_bang.id,
            version=1,
            simulation_config={"max_ticks": 2},
            model_config={},
            branch_policy={
                "max_branch_depth": 4,
                "max_active_multiverses": 8,
                "max_branches_per_tick": 4,
            },
        )
    )
    root = models.Multiverse(
        big_bang_id=big_bang.id,
        parent_multiverse_id=None,
        fork_tick_index=None,
        ui_label="M1",
        depth=0,
        status="active",
        branch_reason="Root timeline",
        state={},
    )
    db.add(root)
    db.flush()
    final_bundle = {
        "multiverse_id": str(root.id),
        "branch_score": 0.91,
        "god_review": {"decision": "continue"},
        "executed_events": [{"title": "Source event"}],
        "large_evidence": ["branch evidence"] * 200,
        "sociology_result": {
            "cohort_state_updates": [
                {"cohort_id": "cohort-a", "name": "Cohort A", "mood": "watchful"}
            ],
            "hero_state_updates": [{"hero_id": "hero-a", "name": "Hero A", "mood": "focused"}],
            "graph_summary": {"pressure": {"conflict_max": 0.7}},
        },
        "idle_assessment": {"idle_streak": 2},
    }
    tick = models.TickSnapshot(
        big_bang_id=big_bang.id,
        multiverse_id=root.id,
        tick_index=0,
        ui_label="M1.T0",
        status="final",
        provisional_bundle={**final_bundle, "phase": "provisional"},
        final_bundle=final_bundle,
        summary="root summary",
    )
    db.add(tick)
    db.flush()
    return big_bang, root, tick


def _add_child_multiverse(
    db: Session,
    root: models.Multiverse,
    *,
    ui_label: str,
) -> models.Multiverse:
    child = models.Multiverse(
        big_bang_id=root.big_bang_id,
        parent_multiverse_id=root.id,
        fork_tick_index=0,
        ui_label=ui_label,
        depth=root.depth + 1,
        status="active",
        branch_reason="corrupt fixture",
        state={},
    )
    db.add(child)
    db.flush()
    return child


def _tick(db: Session, multiverse: models.Multiverse, tick_index: int) -> models.TickSnapshot:
    return db.scalar(
        select(models.TickSnapshot).where(
            models.TickSnapshot.multiverse_id == multiverse.id,
            models.TickSnapshot.tick_index == tick_index,
        )
    )
