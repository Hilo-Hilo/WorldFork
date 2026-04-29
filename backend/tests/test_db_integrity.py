from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import models
from app.db.session import create_db_engine


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


def _seed_big_bang_and_multiverse(db: Session) -> tuple[models.BigBang, models.Multiverse]:
    big_bang = models.BigBang(name="DB integrity", description=None, scenario_input={})
    db.add(big_bang)
    db.flush()
    multiverse = models.Multiverse(
        big_bang_id=big_bang.id,
        parent_multiverse_id=None,
        fork_tick_index=None,
        ui_label="M1",
        depth=0,
        branch_reason=None,
        state={},
    )
    db.add(multiverse)
    db.flush()
    return big_bang, multiverse


def test_sqlite_session_engine_enables_foreign_key_enforcement(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'worldfork.sqlite'}")
    try:
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
    finally:
        engine.dispose()


def test_report_rows_are_unique_for_multiverse_and_final_scopes(db):
    big_bang, multiverse = _seed_big_bang_and_multiverse(db)
    db.add(
        models.Report(
            big_bang_id=big_bang.id,
            multiverse_id=multiverse.id,
            report_type="multiverse",
        )
    )
    db.commit()

    db.add(
        models.Report(
            big_bang_id=big_bang.id,
            multiverse_id=multiverse.id,
            report_type="multiverse",
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    db.add(models.Report(big_bang_id=big_bang.id, report_type="final_big_bang"))
    db.commit()
    db.add(models.Report(big_bang_id=big_bang.id, report_type="final_big_bang"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_numeric_columns_hydrate_as_float(db):
    big_bang, multiverse = _seed_big_bang_and_multiverse(db)
    review = models.GodAgentReview(
        big_bang_id=big_bang.id,
        multiverse_id=multiverse.id,
        decision="continue",
        rationale="test",
        confidence=0.875,
    )
    observation = models.EmotionObservation(
        big_bang_id=big_bang.id,
        multiverse_id=multiverse.id,
        tick_index=1,
        emotion="calm",
        value=7.25,
        source="test",
        evidence={},
    )
    edge = models.GraphEdge(
        big_bang_id=big_bang.id,
        multiverse_id=multiverse.id,
        tick_index=1,
        layer="trust",
        weight=0.625,
        payload={},
    )
    db.add_all([review, observation, edge])
    db.commit()
    db.expire_all()

    assert isinstance(db.get(models.GodAgentReview, review.id).confidence, float)
    assert isinstance(db.get(models.EmotionObservation, observation.id).value, float)
    assert isinstance(db.get(models.GraphEdge, edge.id).weight, float)


def test_report_defaults_remain_orm_side_intentionally():
    table = models.Report.__table__

    assert table.c.status.default is not None
    assert table.c.status.server_default is None
    assert table.c.current_version.default is not None
    assert table.c.current_version.server_default is None


def test_tick_execution_allows_only_one_active_slot_per_multiverse_tick(db):
    big_bang, multiverse = _seed_big_bang_and_multiverse(db)

    db.add(
        models.TickExecution(
            big_bang_id=big_bang.id,
            multiverse_id=multiverse.id,
            tick_index=1,
            status="running",
            active_slot="active",
        )
    )
    db.commit()

    db.add(
        models.TickExecution(
            big_bang_id=big_bang.id,
            multiverse_id=multiverse.id,
            tick_index=1,
            status="running",
            active_slot="active",
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    db.add(
        models.TickExecution(
            big_bang_id=big_bang.id,
            multiverse_id=multiverse.id,
            tick_index=1,
            status="interrupted",
            active_slot=None,
        )
    )
    db.commit()


def test_tick_checkpoint_order_is_unique_within_one_execution(db):
    big_bang, multiverse = _seed_big_bang_and_multiverse(db)
    execution = models.TickExecution(
        big_bang_id=big_bang.id,
        multiverse_id=multiverse.id,
        tick_index=1,
        status="running",
        active_slot="active",
    )
    db.add(execution)
    db.flush()

    db.add(
        models.TickCheckpoint(
            tick_execution_id=execution.id,
            checkpoint_key="cohort:1",
            checkpoint_order=1,
            status="complete",
        )
    )
    db.commit()

    db.add(
        models.TickCheckpoint(
            tick_execution_id=execution.id,
            checkpoint_key="cohort:2",
            checkpoint_order=1,
            status="complete",
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()



def test_node_attempt_belongs_to_execution_node_and_intervention_can_anchor_to_runtime_artifacts(db):
    big_bang, multiverse = _seed_big_bang_and_multiverse(db)
    execution = models.TickExecution(
        big_bang_id=big_bang.id,
        multiverse_id=multiverse.id,
        tick_index=2,
        status="running",
        active_slot="active",
    )
    db.add(execution)
    db.flush()

    node = models.ExecutionNode(
        tick_execution_id=execution.id,
        node_key="hero:1",
        node_kind="hero_decision",
        status="running",
    )
    db.add(node)
    db.flush()

    attempt = models.NodeAttempt(
        execution_node_id=node.id,
        attempt_number=1,
        status="running",
        provider="openrouter",
        model="test-model",
    )
    db.add(attempt)
    db.flush()

    checkpoint = models.TickCheckpoint(
        tick_execution_id=execution.id,
        execution_node_id=node.id,
        checkpoint_key="hero:1",
        checkpoint_order=1,
        status="complete",
    )
    db.add(checkpoint)
    db.flush()

    intervention = models.Intervention(
        big_bang_id=big_bang.id,
        multiverse_id=multiverse.id,
        tick_execution_id=execution.id,
        checkpoint_id=checkpoint.id,
        node_attempt_id=attempt.id,
        intervention_type="pause",
        actor="operator",
        reason="Need inspection",
    )
    db.add(intervention)
    db.commit()

    db.expire_all()
    stored = db.get(models.Intervention, intervention.id)
    assert stored.tick_execution_id == execution.id
    assert stored.checkpoint_id == checkpoint.id
    assert stored.node_attempt_id == attempt.id


def test_offline_downgrade_does_not_inspect_mock_connection(monkeypatch):
    migration = importlib.import_module("app.db.migrations.versions.0001_initial")
    calls = []

    class MockConnection:
        dialect = SimpleNamespace(name="postgresql")

    class FakeMetadata:
        def drop_all(self, *, bind, checkfirst=True):
            calls.append((bind, checkfirst))

    monkeypatch.setattr(migration.op, "get_bind", lambda: MockConnection())
    monkeypatch.setattr(migration.Base, "metadata", FakeMetadata())
    monkeypatch.setattr(
        migration,
        "inspect",
        lambda _bind: pytest.fail("offline downgrade must not inspect MockConnection"),
    )

    migration.downgrade()

    assert calls
    assert calls[0][1] is False
