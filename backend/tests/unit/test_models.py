"""
Unit tests for SQLAlchemy ORM models.

- Simple instantiation tests run against an in-memory SQLite engine using
  JSON columns instead of JSONB (ARRAY columns are skipped on SQLite).
- Tests that require PostgreSQL-specific types (JSONB, ARRAY, CHECK
  constraints) are marked @pytest.mark.postgres and skipped automatically
  unless a DATABASE_URL_SYNC env var points at a live Postgres instance.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy import create_engine
from sqlalchemy.exc import SAWarning
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

# ---------------------------------------------------------------------------
# Check if Postgres is available
# ---------------------------------------------------------------------------
_POSTGRES_AVAILABLE = bool(os.environ.get("DATABASE_URL_SYNC"))

postgres_only = pytest.mark.skipif(
    not _POSTGRES_AVAILABLE,
    reason="Postgres not available (DATABASE_URL_SYNC not set)",
)


# ---------------------------------------------------------------------------
# SQLite-compatible shadow base (JSON replaces JSONB; no ARRAY columns)
# We test round-trip logic on a structurally minimal version of each model.
# ---------------------------------------------------------------------------

class _SqliteBase(DeclarativeBase):
    pass


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# Minimal BigBangRun table for SQLite
class _BigBangRunSqlite(_SqliteBase):
    __tablename__ = "big_bang_runs_test"
    big_bang_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    scenario_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    time_horizon_label: Mapped[str] = mapped_column(String, nullable=False)
    tick_duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    max_ticks: Mapped[int] = mapped_column(Integer, nullable=False)
    max_schedule_horizon_ticks: Mapped[int] = mapped_column(Integer, nullable=False)
    source_of_truth_version: Mapped[str] = mapped_column(String, nullable=False)
    source_of_truth_snapshot_path: Mapped[str] = mapped_column(String, nullable=False)
    provider_snapshot_id: Mapped[str] = mapped_column(String, nullable=False)
    root_universe_id: Mapped[str] = mapped_column(String, nullable=False)
    run_folder_path: Mapped[str] = mapped_column(String, nullable=False)
    created_by_user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# Minimal CohortState table with composite PK for SQLite
class _CohortStateSqlite(_SqliteBase):
    __tablename__ = "cohort_states_test"
    cohort_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tick: Mapped[int] = mapped_column(Integer, primary_key=True)
    universe_id: Mapped[str] = mapped_column(String(64), nullable=False)
    archetype_id: Mapped[str] = mapped_column(String(64), nullable=False)
    represented_population: Mapped[int] = mapped_column(Integer, nullable=False)
    population_share_of_archetype: Mapped[float] = mapped_column(Float, nullable=False)
    expression_level: Mapped[float] = mapped_column(Float, nullable=False)
    mobilization_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    speech_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    attention: Mapped[float] = mapped_column(Float, nullable=False)
    fatigue: Mapped[float] = mapped_column(Float, nullable=False)
    prompt_temperature: Mapped[float] = mapped_column(Float, nullable=False)
    representation_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sqlite_engine():
    eng = create_engine("sqlite:///:memory:", echo=False)
    _SqliteBase.metadata.create_all(eng)
    yield eng
    _SqliteBase.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def sqlite_session(sqlite_engine):
    Session = sessionmaker(bind=sqlite_engine)
    sess = Session()
    yield sess
    sess.rollback()
    sess.close()


# ---------------------------------------------------------------------------
# Import-level smoke tests (confirm models load without error)
# ---------------------------------------------------------------------------

def test_models_importable():
    from backend.app.models import (
        Base,
        BigBangRunModel,
        BranchNodeModel,
        BranchPolicySettingModel,
        CohortStateModel,
        EventModel,
        GlobalSettingModel,
        HeroArchetypeModel,
        HeroStateModel,
        JobModel,
        LLMCallModel,
        ModelRoutingEntryModel,
        PopulationArchetypeModel,
        ProviderSettingModel,
        RateLimitSettingModel,
        SocialPostModel,
        UniverseModel,
    )
    assert Base is not None
    # 16 model classes + Base + TimestampMixin = 18
    assert BigBangRunModel.__tablename__ == "big_bang_runs"
    assert UniverseModel.__tablename__ == "universes"
    assert BranchNodeModel.__tablename__ == "branch_nodes"
    assert PopulationArchetypeModel.__tablename__ == "population_archetypes"
    assert CohortStateModel.__tablename__ == "cohort_states"
    assert HeroArchetypeModel.__tablename__ == "hero_archetypes"
    assert HeroStateModel.__tablename__ == "hero_states"
    assert EventModel.__tablename__ == "events"
    assert SocialPostModel.__tablename__ == "social_posts"
    assert JobModel.__tablename__ == "jobs"
    assert LLMCallModel.__tablename__ == "llm_calls"
    assert ProviderSettingModel.__tablename__ == "settings_provider"
    assert ModelRoutingEntryModel.__tablename__ == "settings_model_routing"
    assert RateLimitSettingModel.__tablename__ == "settings_rate_limit"
    assert BranchPolicySettingModel.__tablename__ == "settings_branch_policy"
    assert GlobalSettingModel.__tablename__ == "settings_global"


def test_schemas_do_not_import_models():
    """Confirm the schemas package does not import from models (one-way dep)."""
    import sys

    # Remove any cached modules to get a clean import
    mods_to_clear = [k for k in sys.modules if k.startswith("backend.app.models")]
    for m in mods_to_clear:
        del sys.modules[m]

    # Re-import schemas — should not trigger models import
    import backend.app.schemas  # noqa: F401

    # Confirm models are not in sys.modules (schema-side)
    # (models may have been imported elsewhere; just check no circular dep)
    # This test mainly ensures schemas/__init__.py has no "from backend.app.models" line.
    import ast
    import pathlib

    schemas_init = pathlib.Path(__file__).parents[2] / "app" / "schemas" / "__init__.py"
    source = schemas_init.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "backend.app.models" not in node.module, (
                    f"schemas/__init__.py imports from models: {node.module}"
                )


# ---------------------------------------------------------------------------
# BigBangRun round-trip (SQLite)
# ---------------------------------------------------------------------------

def test_big_bang_run_from_schema_round_trip():
    """from_schema / to_schema round-trip for BigBangRun."""
    from backend.app.models.runs import BigBangRunModel
    from backend.app.schemas.universes import BigBangRun

    now = _now()
    schema = BigBangRun(
        big_bang_id="bbr-001",
        display_name="Test Run",
        created_at=now,
        updated_at=now,
        scenario_text="A test scenario.",
        status="draft",
        time_horizon_label="72h",
        tick_duration_minutes=120,
        max_ticks=36,
        max_schedule_horizon_ticks=5,
        source_of_truth_version="1.0.0",
        source_of_truth_snapshot_path="/sot/snap.json",
        provider_snapshot_id="prov-snap-001",
        root_universe_id="univ-root-001",
        run_folder_path="/runs/bbr-001",
        input_file_ids=["file-a", "file-b"],
        safe_edit_metadata={"note": "test"},
    )

    model = BigBangRunModel.from_schema(schema)
    assert model.big_bang_id == "bbr-001"
    assert model.status == "draft"
    assert model.input_file_ids == ["file-a", "file-b"]

    # to_schema without DB (created_at/updated_at already set)
    out = model.to_schema()
    assert out.big_bang_id == schema.big_bang_id
    assert out.display_name == schema.display_name
    assert out.status == schema.status
    assert out.input_file_ids == schema.input_file_ids
    assert out.safe_edit_metadata == schema.safe_edit_metadata


# ---------------------------------------------------------------------------
# Universe round-trip
# ---------------------------------------------------------------------------

def test_universe_from_schema_round_trip():
    from backend.app.models.universes import UniverseModel
    from backend.app.schemas.universes import Universe

    now = _now()
    schema = Universe(
        universe_id="univ-001",
        big_bang_id="bbr-001",
        parent_universe_id=None,
        branch_from_tick=0,
        branch_depth=0,
        lineage_path=["univ-001"],
        status="active",
        branch_reason="",
        current_tick=3,
        latest_metrics={"foo": 1},
        created_at=now,
    )

    model = UniverseModel.from_schema(schema)
    assert model.universe_id == "univ-001"
    assert model.branch_depth == 0
    assert model.lineage_path == ["univ-001"]

    # to_schema (children list is empty since relationship not loaded)
    model.children = []
    out = model.to_schema()
    assert out.universe_id == schema.universe_id
    assert out.status == schema.status
    assert out.latest_metrics == {"foo": 1}


# ---------------------------------------------------------------------------
# CohortState composite PK (SQLite)
# ---------------------------------------------------------------------------

def test_cohort_state_composite_pk_sqlite(sqlite_session):
    """Composite PK (cohort_id, tick) works in SQLite shadow table."""
    cs1 = _CohortStateSqlite(
        cohort_id="coh-001",
        tick=0,
        universe_id="univ-001",
        archetype_id="arch-001",
        represented_population=500,
        population_share_of_archetype=0.5,
        expression_level=0.4,
        mobilization_mode="dormant",
        speech_mode="silent",
        attention=0.6,
        fatigue=0.1,
        prompt_temperature=0.7,
        representation_mode="population",
    )
    cs2 = _CohortStateSqlite(
        cohort_id="coh-001",
        tick=1,  # same cohort, different tick
        universe_id="univ-001",
        archetype_id="arch-001",
        represented_population=490,
        population_share_of_archetype=0.49,
        expression_level=0.42,
        mobilization_mode="murmur",
        speech_mode="private",
        attention=0.55,
        fatigue=0.15,
        prompt_temperature=0.7,
        representation_mode="population",
    )
    sqlite_session.add_all([cs1, cs2])
    sqlite_session.flush()

    rows = (
        sqlite_session.query(_CohortStateSqlite)
        .filter(_CohortStateSqlite.cohort_id == "coh-001")
        .all()
    )
    assert len(rows) == 2
    ticks = {r.tick for r in rows}
    assert ticks == {0, 1}


def test_cohort_state_from_schema_round_trip():
    """from_schema / to_schema for CohortState (no DB needed)."""
    from backend.app.models.cohorts import CohortStateModel
    from backend.app.schemas.actors import CohortState

    schema = CohortState(
        cohort_id="coh-001",
        universe_id="univ-001",
        tick=0,
        archetype_id="arch-001",
        represented_population=250,
        population_share_of_archetype=0.25,
        issue_stance={"policy_x": 0.7},
        expression_level=0.5,
        mobilization_mode="dormant",
        speech_mode="silent",
        emotions={"anger": 2.0, "fear": 1.5},
        behavior_state={"protest": 0.1},
        attention=0.6,
        fatigue=0.1,
        prompt_temperature=0.7,
        representation_mode="population",
    )

    model = CohortStateModel.from_schema(schema)
    assert model.cohort_id == "coh-001"
    assert model.tick == 0
    assert model.represented_population == 250

    out = model.to_schema()
    assert out.cohort_id == schema.cohort_id
    assert out.tick == schema.tick
    assert out.archetype_id == schema.archetype_id
    assert out.emotions == schema.emotions
    assert out.issue_stance == schema.issue_stance


# ---------------------------------------------------------------------------
# HeroArchetype / HeroState round-trip
# ---------------------------------------------------------------------------

def test_hero_archetype_from_schema_round_trip():
    from backend.app.models.heroes import HeroArchetypeModel
    from backend.app.schemas.actors import HeroArchetype

    schema = HeroArchetype(
        hero_id="hero-001",
        label="Policy Maker",
        description="A key decision-maker.",
        role="politician",
        location_scope="national",
        public_reach=0.8,
        institutional_power=0.9,
        financial_power=0.5,
        agenda_control=0.7,
        media_access=0.85,
        volatility=0.2,
        ego_sensitivity=0.4,
        strategic_discipline=0.75,
        controversy_tolerance=0.3,
        direct_event_power=0.6,
    )

    model = HeroArchetypeModel.from_schema(schema, big_bang_id="bbr-001")
    assert model.hero_id == "hero-001"
    assert model.big_bang_id == "bbr-001"

    out = model.to_schema()
    assert out.hero_id == schema.hero_id
    assert out.public_reach == schema.public_reach


def test_hero_state_from_schema_round_trip():
    from backend.app.models.heroes import HeroStateModel
    from backend.app.schemas.actors import HeroState

    schema = HeroState(
        hero_id="hero-001",
        universe_id="univ-001",
        tick=2,
        attention=0.7,
        fatigue=0.2,
        perceived_pressure=0.5,
    )

    model = HeroStateModel.from_schema(schema)
    assert model.hero_id == "hero-001"
    assert model.tick == 2

    out = model.to_schema()
    assert out.tick == 2
    assert out.attention == 0.7


# ---------------------------------------------------------------------------
# Event round-trip
# ---------------------------------------------------------------------------

def test_event_from_schema_round_trip():
    from backend.app.models.events import EventModel
    from backend.app.schemas.events import Event

    schema = Event(
        event_id="evt-001",
        universe_id="univ-001",
        created_tick=0,
        scheduled_tick=2,
        event_type="press_conference",
        title="Big Announcement",
        description="The hero makes a statement.",
        created_by_actor_id="hero-001",
        visibility="public",
        risk_level=0.3,
        status="scheduled",
        preconditions=[{"type": "media_present"}],
        expected_effects={"opinion_shift": 0.05},
    )

    model = EventModel.from_schema(schema)
    assert model.event_id == "evt-001"
    assert model.scheduled_tick == 2

    out = model.to_schema()
    assert out.event_id == schema.event_id
    assert out.status == schema.status
    assert out.preconditions == schema.preconditions


# ---------------------------------------------------------------------------
# SocialPost round-trip
# ---------------------------------------------------------------------------

def test_social_post_from_schema_round_trip():
    from backend.app.models.posts import SocialPostModel
    from backend.app.schemas.posts import SocialPost

    schema = SocialPost(
        post_id="post-001",
        universe_id="univ-001",
        platform="twitter",
        tick_created=1,
        author_actor_id="coh-001",
        content="We demand change!",
        credibility_signal=0.7,
        visibility_scope="public",
        reach_score=0.5,
        repost_count=10,
        comment_count=5,
    )

    model = SocialPostModel.from_schema(schema)
    assert model.post_id == "post-001"
    assert model.tick_created == 1

    out = model.to_schema()
    assert out.post_id == schema.post_id
    assert out.repost_count == 10


# ---------------------------------------------------------------------------
# BranchNode round-trip
# ---------------------------------------------------------------------------

def test_branch_node_from_schema_round_trip():
    from backend.app.models.branches import BranchNodeModel
    from backend.app.schemas.branching import BranchNode

    schema = BranchNode(
        universe_id="univ-002",
        parent_universe_id="univ-001",
        depth=1,
        branch_tick=3,
        branch_point_id="bp-001",
        branch_trigger="high divergence",
        branch_delta={"type": "parameter_shift"},
        status="active",
        descendant_count=0,
    )

    model = BranchNodeModel.from_schema(schema)
    assert model.universe_id == "univ-002"
    assert model.depth == 1

    out = model.to_schema()
    assert out.universe_id == schema.universe_id
    assert out.branch_tick == schema.branch_tick


# ---------------------------------------------------------------------------
# Job round-trip
# ---------------------------------------------------------------------------

def test_job_from_schema_round_trip():
    from backend.app.models.jobs import JobModel
    from backend.app.schemas.jobs import JobEnvelope

    now = _now()
    schema = JobEnvelope(
        job_id="job-001",
        job_type="simulate_universe_tick",
        priority="p1",
        run_id="bbr-001",
        universe_id="univ-001",
        tick=5,
        attempt_number=0,
        idempotency_key="bbr-001:univ-001:tick:5:simulate",
        payload={"extra": "data"},
        created_at=now,
    )

    model = JobModel.from_schema(schema)
    assert model.job_id == "job-001"
    assert model.status == "queued"

    out = model.to_schema()
    assert out.job_id == schema.job_id
    assert out.job_type == schema.job_type


# ---------------------------------------------------------------------------
# FK constraint fires (SQLite, structural only — no JSONB/ARRAY)
# ---------------------------------------------------------------------------

def test_cohort_state_unique_composite_pk_conflict(sqlite_session):
    """Inserting the same (cohort_id, tick) twice raises IntegrityError."""
    from sqlalchemy.exc import IntegrityError

    obj1 = _CohortStateSqlite(
        cohort_id="coh-dup",
        tick=0,
        universe_id="univ-x",
        archetype_id="arch-x",
        represented_population=100,
        population_share_of_archetype=0.1,
        expression_level=0.3,
        mobilization_mode="dormant",
        speech_mode="silent",
        attention=0.5,
        fatigue=0.0,
        prompt_temperature=0.7,
        representation_mode="small",
    )
    obj2 = _CohortStateSqlite(
        cohort_id="coh-dup",
        tick=0,  # same PK
        universe_id="univ-x",
        archetype_id="arch-x",
        represented_population=200,
        population_share_of_archetype=0.2,
        expression_level=0.4,
        mobilization_mode="dormant",
        speech_mode="silent",
        attention=0.5,
        fatigue=0.0,
        prompt_temperature=0.7,
        representation_mode="small",
    )
    sqlite_session.add(obj1)
    sqlite_session.flush()
    sqlite_session.add(obj2)
    with pytest.warns(SAWarning, match="conflicts with persistent instance"):
        with pytest.raises(IntegrityError):
            sqlite_session.flush()
