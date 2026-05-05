from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.domains.multiverse import routes as multiverses_api
from app.api.schemas import MultiverseContinueRequest
from app.db import models
from app.domains.tick.tick_runner import run_next_tick


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
        engine.dispose()


def test_continue_multiverse_versions_runtime_override_without_global_config_switch(db: Session):
    big_bang, multiverse = _seed_completed_multiverse(db)
    report = models.Report(
        big_bang_id=big_bang.id,
        multiverse_id=multiverse.id,
        report_type="multiverse",
        status="completed",
        current_version=1,
    )
    db.add(report)
    db.flush()
    report_version = models.ReportVersion(
        report_id=report.id,
        version=1,
        title="M1 report",
        summary=None,
        source_multiverse_version=1,
        content={"source": {"multiverse_version": 1}},
    )
    db.add(report_version)
    db.commit()

    continued = multiverses_api.continue_multiverse(
        multiverse.id,
        MultiverseContinueRequest(max_ticks=4, reason="extend one timeline"),
        db=db,
    )

    assert continued.version == 2
    assert continued.status == "active"
    assert continued.ended_at is None
    assert continued.continued_from_report_version_id == report_version.id
    assert continued.state["runtime_overrides"]["max_ticks"] == 4
    assert continued.state["runtime_config_version"] == 2
    assert db.get(models.BigBang, big_bang.id).current_config_version == 1


def test_continue_multiverse_rejects_archived_big_bang(db: Session):
    big_bang, multiverse = _seed_completed_multiverse(db)
    big_bang.status = "archived"
    db.commit()

    with pytest.raises(HTTPException) as exc:
        multiverses_api.continue_multiverse(
            multiverse.id,
            MultiverseContinueRequest(max_ticks=4, reason="archived runs stay immutable"),
            db=db,
        )

    assert exc.value.status_code == 409
    assert "archived" in exc.value.detail
    assert db.get(models.Multiverse, multiverse.id).status == "completed"


def test_continue_active_multiverse_at_tick_horizon_extends_without_report(db: Session):
    big_bang, multiverse = _seed_completed_multiverse(db)
    big_bang.status = "draft"
    multiverse.status = "active"
    multiverse.report_status = "not_ready"
    multiverse.ended_at = None
    db.commit()

    continued = multiverses_api.continue_multiverse(
        multiverse.id,
        MultiverseContinueRequest(max_ticks=4, reason="extend active capped timeline"),
        db=db,
    )

    assert continued.version == 2
    assert continued.status == "active"
    assert continued.report_status == "not_ready"
    assert continued.ended_at is None
    assert continued.continued_from_report_version_id is None
    assert continued.state["runtime_overrides"]["max_ticks"] == 4
    assert continued.state["continued_from"]["latest_tick_index"] == 1
    assert db.get(models.BigBang, big_bang.id).current_config_version == 1


def test_continue_active_multiverse_before_tick_horizon_is_rejected(db: Session):
    big_bang, multiverse = _seed_active_multiverse_before_horizon(db)

    with pytest.raises(HTTPException) as exc:
        multiverses_api.continue_multiverse(
            multiverse.id,
            MultiverseContinueRequest(max_ticks=4, reason="too early"),
            db=db,
        )

    assert exc.value.status_code == 409
    assert "only terminal multiverses or active multiverses at max_ticks can be continued" in exc.value.detail
    assert db.get(models.BigBang, big_bang.id).current_config_version == 1


def test_continue_rejects_report_version_from_another_multiverse(db: Session):
    big_bang, multiverse = _seed_completed_multiverse(db)
    other = models.Multiverse(
        big_bang_id=big_bang.id,
        parent_multiverse_id=None,
        fork_tick_index=None,
        ui_label="M2",
        depth=0,
        status="completed",
        branch_reason=None,
        state={},
        report_status="completed",
        ended_at=datetime.now(timezone.utc),
    )
    db.add(other)
    db.flush()
    report = models.Report(
        big_bang_id=big_bang.id,
        multiverse_id=other.id,
        report_type="multiverse",
        status="completed",
        current_version=1,
    )
    db.add(report)
    db.flush()
    report_version = models.ReportVersion(report_id=report.id, version=1, title="M2 report", summary=None)
    db.add(report_version)
    db.commit()

    with pytest.raises(HTTPException) as exc:
        multiverses_api.continue_multiverse(
            multiverse.id,
            MultiverseContinueRequest(max_ticks=4, continued_from_report_version_id=report_version.id),
            db=db,
        )

    assert exc.value.status_code == 422
    assert "must belong to this multiverse report" in exc.value.detail


def test_sibling_multiverse_does_not_inherit_continuation_max_ticks(db: Session):
    big_bang, _multiverse = _seed_completed_multiverse(db)
    sibling = models.Multiverse(
        big_bang_id=big_bang.id,
        parent_multiverse_id=None,
        fork_tick_index=None,
        ui_label="M2",
        depth=0,
        status="active",
        branch_reason=None,
        state={},
        report_status="not_ready",
    )
    db.add(sibling)
    db.flush()
    tick = models.TickSnapshot(
        big_bang_id=big_bang.id,
        multiverse_id=sibling.id,
        tick_index=1,
        ui_label="M2.T1",
        status="final",
        provisional_bundle={},
        final_bundle={},
    )
    db.add(tick)
    db.add(
        models.BigBangConfig(
            big_bang_id=big_bang.id,
            version=2,
            simulation_config={"max_ticks": 4},
            model_config={},
            branch_policy={},
        )
    )
    db.commit()

    returned = run_next_tick(db, multiverse=sibling)

    assert returned.id == tick.id
    assert sibling.status == "completed"
    assert sibling.report_status == "ready"
    assert (
        db.scalar(
            select(func.count())
            .select_from(models.TickSnapshot)
            .where(models.TickSnapshot.multiverse_id == sibling.id)
        )
        == 1
    )


def _seed_completed_multiverse(db: Session):
    big_bang = models.BigBang(
        name="Continuation",
        description=None,
        scenario_input={},
        status="completed",
        current_config_version=1,
    )
    db.add(big_bang)
    db.flush()
    db.add(
        models.BigBangConfig(
            big_bang_id=big_bang.id,
            version=1,
            simulation_config={"max_ticks": 1},
            model_config={},
            branch_policy={},
        )
    )
    multiverse = models.Multiverse(
        big_bang_id=big_bang.id,
        parent_multiverse_id=None,
        fork_tick_index=None,
        ui_label="M1",
        version=1,
        depth=0,
        status="completed",
        branch_reason=None,
        state={},
        report_status="completed",
        ended_at=datetime.now(timezone.utc),
    )
    db.add(multiverse)
    db.flush()
    db.add(
        models.TickSnapshot(
            big_bang_id=big_bang.id,
            multiverse_id=multiverse.id,
            tick_index=1,
            ui_label="M1.T1",
            status="final",
            provisional_bundle={},
            final_bundle={},
        )
    )
    db.flush()
    return big_bang, multiverse


def _seed_active_multiverse_before_horizon(db: Session):
    big_bang = models.BigBang(
        name="Active before horizon",
        description=None,
        scenario_input={},
        status="draft",
        current_config_version=1,
    )
    db.add(big_bang)
    db.flush()
    db.add(
        models.BigBangConfig(
            big_bang_id=big_bang.id,
            version=1,
            simulation_config={"max_ticks": 3},
            model_config={},
            branch_policy={},
        )
    )
    multiverse = models.Multiverse(
        big_bang_id=big_bang.id,
        parent_multiverse_id=None,
        fork_tick_index=None,
        ui_label="M1",
        version=1,
        depth=0,
        status="active",
        branch_reason=None,
        state={},
        report_status="not_ready",
    )
    db.add(multiverse)
    db.flush()
    db.add(
        models.TickSnapshot(
            big_bang_id=big_bang.id,
            multiverse_id=multiverse.id,
            tick_index=1,
            ui_label="M1.T1",
            status="final",
            provisional_bundle={},
            final_bundle={},
        )
    )
    db.flush()
    return big_bang, multiverse
