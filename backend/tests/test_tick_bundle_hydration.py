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
from app.domains.multiverse import routes as multiverses_api
from app.domains.tick import routes as ticks_api
from app.db import models
from app.main import app
from app.domains.report import engine as report_engine
from app.domains.tick import tick_runner
from app.domains.multiverse.branch_engine import create_branch
from app.domains.endpoint_ledger.service import endpoint_ledger_entries, latest_endpoint_ledger
from app.api.schemas import SimulateTickRequest
from app.db.session import get_db
from app.domains.tick.tick_bundles import (
    TICK_BUNDLE_REF_KEY,
    TICK_BUNDLE_REF_KIND,
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


def test_branch_creation_assigns_path_probability(db: Session):
    _big_bang, root, _root_tick = _seed_root_tick(db)

    child = create_branch(
        db,
        parent=root,
        fork_tick_index=0,
        reason="probabilistic branch",
        idempotency_key="branch:probability",
        branch_probability=0.25,
        parent_continuation_probability=0.75,
        probability_basis={"source": "test", "basis": "explicit conditional probability"},
    )
    edge = db.scalar(
        select(models.MultiverseLineageEdge).where(models.MultiverseLineageEdge.child_multiverse_id == child.id)
    )

    assert float(root.path_probability) == 0.75
    assert float(child.branch_probability) == 0.25
    assert float(child.path_probability) == 0.25
    assert edge is not None
    assert float(edge.branch_probability) == 0.25
    assert float(edge.parent_path_probability) == 1.0
    assert float(edge.child_path_probability) == 0.25
    assert edge.probability_basis["source"] == "test"
    assert child.state["branch"]["path_probability"] == 0.25


def test_branch_creation_can_consume_remaining_parent_path_probability(db: Session):
    _big_bang, root, _root_tick = _seed_root_tick(db)

    first = create_branch(
        db,
        parent=root,
        fork_tick_index=0,
        reason="seed yes path",
        idempotency_key="branch:seed:yes",
        branch_probability=0.5,
        parent_continuation_probability=0.5,
        probability_basis={"source": "forecast_branch_hypothesis", "candidate_endpoint_id": "yes"},
    )
    second = create_branch(
        db,
        parent=root,
        fork_tick_index=0,
        reason="seed no path",
        idempotency_key="branch:seed:no",
        branch_probability=1.0,
        parent_continuation_probability=0.0,
        probability_basis={"source": "forecast_branch_hypothesis", "candidate_endpoint_id": "no"},
    )
    second_edge = db.scalar(
        select(models.MultiverseLineageEdge).where(models.MultiverseLineageEdge.child_multiverse_id == second.id)
    )

    assert float(first.path_probability) == 0.5
    assert float(second.branch_probability) == 1.0
    assert float(second.path_probability) == 0.5
    assert float(root.path_probability) == 0.0
    assert second.state["branch"]["branch_probability"] == 1.0
    assert second.state["branch"]["parent_path_probability_after"] == 0.0
    assert second_edge is not None
    assert float(second_edge.branch_probability) == 1.0
    assert float(second_edge.child_path_probability) == 0.5


def test_branch_creation_inherits_latest_endpoint_ledger(db: Session):
    big_bang, root, _root_tick = _seed_root_tick(db)
    parent_ledger = models.EndpointLedgerVersion(
        big_bang_id=big_bang.id,
        multiverse_id=root.id,
        scope="multiverse",
        version=1,
        status="completed",
        source_type="god_tick_review",
        created_by="god_agent",
        summary="Root settled yes.",
        payload={"source": "test"},
    )
    db.add(parent_ledger)
    db.flush()
    db.add_all(
        [
            models.EndpointLedgerEntry(
                ledger_version_id=parent_ledger.id,
                endpoint_key="yes",
                label="The event occurs by the deadline",
                description=None,
                status="realized",
                probability=None,
                realization_criteria=["Official evidence confirms yes."],
                authority_refs=["official_release"],
                evidence_refs=[{"source": "tick", "tick_index": 0}],
                negative_evidence_refs=[],
                blockers=[],
                status_basis="god_tick_review",
                contradiction_notes=None,
                rationale="Parent timeline settled yes.",
                last_observed_tick_index=0,
                meta={"endpoint_role": "primary_candidate", "candidate_endpoint_id": "yes"},
            ),
            models.EndpointLedgerEntry(
                ledger_version_id=parent_ledger.id,
                endpoint_key="no",
                label="The event does not occur by the deadline",
                description=None,
                status="eliminated",
                probability=None,
                realization_criteria=["Official evidence confirms yes."],
                authority_refs=["official_release"],
                evidence_refs=[{"source": "tick", "tick_index": 0}],
                negative_evidence_refs=[],
                blockers=[],
                status_basis="god_tick_review",
                contradiction_notes=None,
                rationale="No is eliminated because yes settled.",
                last_observed_tick_index=0,
                meta={"endpoint_role": "primary_candidate", "candidate_endpoint_id": "no"},
            ),
        ]
    )
    db.flush()

    child = create_branch(
        db,
        parent=root,
        fork_tick_index=0,
        reason="branch after endpoint settlement",
        idempotency_key="branch:inherits-ledger",
    )

    child_ledger = latest_endpoint_ledger(
        db,
        big_bang_id=big_bang.id,
        multiverse_id=child.id,
        scope="multiverse",
    )
    assert child_ledger is not None
    assert child_ledger.parent_ledger_version_id == parent_ledger.id
    assert child_ledger.source_type == "branch_inherited"
    child_entries = {entry.endpoint_key: entry for entry in endpoint_ledger_entries(db, child_ledger.id)}
    assert child_entries["yes"].status == "realized"
    assert child_entries["no"].status == "eliminated"
    assert child_entries["yes"].meta["inherited_from_multiverse_id"] == str(root.id)


def test_http_tick_readers_return_hydrated_shape(db: Session):
    big_bang, root, _root_tick = _seed_root_tick(db)
    child = create_branch(
        db,
        parent=root,
        fork_tick_index=0,
        reason="http reader branch",
        idempotency_key="branch:http-reader",
    )
    child_tick = _tick(db, child, 0)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    try:
        tick_response = client.get(f"/api/ticks/{child_tick.id}")
        list_response = client.get(f"/api/multiverses/{child.id}/ticks")
        workspace_response = client.get(f"/api/workspace/{big_bang.id}/state")
        trace_response = client.get(f"/api/agent/universes/{child.id}/trace?tick=0&verbosity=full")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert tick_response.status_code == 200, tick_response.text
    assert list_response.status_code == 200, list_response.text
    assert workspace_response.status_code == 200, workspace_response.text
    assert trace_response.status_code == 200, trace_response.text

    tick_payload = tick_response.json()
    assert TICK_BUNDLE_REF_KEY not in json.dumps(tick_payload)
    assert tick_payload["final_bundle"]["branch_score"] == 0.91
    assert list_response.json()[0]["final_bundle"]["multiverse_id"] == str(child.id)
    assert workspace_response.json()["latest_ticks"][0]["final_bundle"]["branch_score"] == 0.91
    trace_payload = trace_response.json()["data"]
    assert trace_payload["tick_snapshot"]["final_bundle"]["branch_score"] == 0.91


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


def test_nested_provisional_branch_falls_back_and_final_sync_recurses(db: Session):
    _big_bang, root, root_tick = _seed_root_tick(db)
    root_tick.status = "provisional"
    root_tick.final_bundle = {}
    db.flush()

    child = create_branch(
        db,
        parent=root,
        fork_tick_index=0,
        reason="branch while parent provisional",
        idempotency_key="branch:provisional-child",
    )
    grandchild = create_branch(
        db,
        parent=child,
        fork_tick_index=0,
        reason="nested provisional branch",
        idempotency_key="branch:provisional-grandchild",
    )

    assert grandchild.state["last_sociology"]["cohort_state_updates"][0]["cohort_id"] == "cohort-a"

    root.state = {
        **(root.state or {}),
        "last_tick_index": 0,
        "graph_summary": {"pressure": {"conflict_max": 0.9}},
    }
    root_tick.status = "final"
    root_tick.final_bundle = {
        **root_tick.provisional_bundle,
        "branch_score": 0.99,
        "god_review": {"decision": "continue"},
    }
    tick_runner._sync_forked_children_after_tick(db, parent=root, tick=root_tick)
    db.flush()

    child_tick = _tick(db, child, 0)
    grandchild_tick = _tick(db, grandchild, 0)
    assert child_tick.status == "final"
    assert grandchild_tick.status == "final"
    assert hydrate_tick_snapshot_for_read(db, grandchild_tick).final_bundle["branch_score"] == 0.99


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


def test_agent_trace_missing_tick_preserves_empty_trace_response(db: Session):
    _big_bang, root, _root_tick = _seed_root_tick(db)

    trace = agent_api.trace(root.id, db=db, tick=999, verbosity="full")

    assert trace["data"]["tick"] == 999
    assert trace["data"]["actor_count"] == 0
    assert trace["data"]["state"] == {}
    assert trace["data"]["tick_snapshot"] is None


def test_simulate_next_returns_hydrated_existing_inherited_tick_at_max_ticks(db: Session):
    big_bang, root, _root_tick = _seed_root_tick(db)
    child = create_branch(
        db,
        parent=root,
        fork_tick_index=0,
        reason="already complete child",
        idempotency_key="branch:max",
    )
    db.add(
        models.BigBangConfig(
            big_bang_id=big_bang.id,
            version=2,
            simulation_config={"max_ticks": 0},
            model_config={},
            branch_policy={},
        )
    )
    child.state = {**(child.state or {}), "runtime_config_version": 2}
    db.flush()

    payload = multiverses_api.simulate(
        child.id,
        SimulateTickRequest(idempotency_key="return-existing"),
        db=db,
    )

    assert payload.tick_index == 0
    assert TICK_BUNDLE_REF_KEY not in payload.final_bundle
    assert payload.final_bundle["multiverse_id"] == str(child.id)
    assert payload.final_bundle["branch_score"] == 0.91


def test_public_hydration_errors_return_actionable_json(db: Session):
    _big_bang, root, root_tick = _seed_root_tick(db)
    child = _add_child_multiverse(db, root, ui_label="M1.error")
    child_tick = models.TickSnapshot(
        big_bang_id=root.big_bang_id,
        multiverse_id=child.id,
        tick_index=0,
        ui_label="M1.error.T0",
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

    app.dependency_overrides[get_db] = lambda: db
    try:
        response = TestClient(app).get(f"/api/ticks/{child_tick.id}")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 500
    assert response.json()["detail"].startswith("tick bundle hydration failed: missing lineage ref")


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
            "kind": TICK_BUNDLE_REF_KIND,
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

    stale_metadata_tick = models.TickSnapshot(
        big_bang_id=root.big_bang_id,
        multiverse_id=child.id,
        tick_index=2,
        ui_label="M1.a.T2",
        status="final",
        provisional_bundle={},
        final_bundle=inherited_tick_bundle_ref(
            parent=root,
            child=child,
            source_tick=root_tick,
            bundle_field="final_bundle",
        ),
    )
    stale_metadata_tick.final_bundle["inherited_from"]["source_tick_snapshot_id"] = str(uuid4())
    db.add(stale_metadata_tick)
    db.add(
        models.TickLineageRef(
            child_multiverse_id=child.id,
            source_multiverse_id=root.id,
            source_tick_snapshot_id=root_tick.id,
            inherited_tick_index=2,
            inherited_ui_label=stale_metadata_tick.ui_label,
        )
    )
    db.flush()

    with pytest.raises(TickBundleHydrationError, match="inherited_from source_tick_snapshot_id mismatch"):
        hydrate_tick_bundle(db, stale_metadata_tick, "final_bundle")

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
            "kind": TICK_BUNDLE_REF_KIND,
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
        "cost_summary",
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
