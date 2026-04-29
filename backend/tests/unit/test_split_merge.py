"""Unit tests for backend.app.sociology.split_merge.

Validation tests are pure-Pydantic and DB-free.
DB-touching tests use an in-memory SQLite engine with shadow tables that
mirror the production schema (JSON in place of JSONB, no ARRAY columns).
"""
from __future__ import annotations


import pytest
import pytest_asyncio
from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    Integer,
    String,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backend.app.schemas.actors import CohortState, PopulationArchetype
from backend.app.schemas.sociology import (
    ChildSplitSpec,
    MergeProposal,
    SociologyParams,
    SplitProposal,
)
from backend.app.sociology.split_merge import (
    audit_population_conservation,
    commit_merge,
    commit_split,
    evaluate_merge_validity,
    evaluate_split_validity,
)


# ---------------------------------------------------------------------------
# SQLite shadow base + tables matching the prod schema (JSON instead of JSONB).
# These shadow tables are used to validate transactional split/merge logic on
# SQLite. Because production CohortStateModel uses ARRAY/JSONB columns we can't
# use it directly with SQLite; we monkey-patch its __table__ at module import
# so the same model is bound to the shadow base instead.
# ---------------------------------------------------------------------------


class _ShadowBase(DeclarativeBase):
    pass


class _CohortShadow(_ShadowBase):
    __tablename__ = "cohort_states"

    cohort_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tick: Mapped[int] = mapped_column(Integer, primary_key=True)
    universe_id: Mapped[str] = mapped_column(String(64), nullable=False)
    archetype_id: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_cohort_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    child_cohort_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    represented_population: Mapped[int] = mapped_column(Integer, nullable=False)
    population_share_of_archetype: Mapped[float] = mapped_column(Float, nullable=False)

    issue_stance: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    expression_level: Mapped[float] = mapped_column(Float, nullable=False)
    mobilization_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    speech_mode: Mapped[str] = mapped_column(String(32), nullable=False)

    emotions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    behavior_state: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    attention: Mapped[float] = mapped_column(Float, nullable=False)
    fatigue: Mapped[float] = mapped_column(Float, nullable=False)
    grievance: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    perceived_efficacy: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    perceived_majority: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    fear_of_isolation: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    willingness_to_speak: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    identity_salience: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)

    visible_trust_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    exposure_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    dependency_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    memory_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    recent_post_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    queued_event_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    previous_action_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    prompt_temperature: Mapped[float] = mapped_column(Float, nullable=False)
    representation_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    allowed_tools: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class _ArchetypeShadow(_ShadowBase):
    __tablename__ = "population_archetypes"

    archetype_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    population_total: Mapped[int] = mapped_column(Integer, nullable=False)


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


def _make_archetype(archetype_id: str = "arch-1", pop: int = 1000) -> PopulationArchetype:
    return PopulationArchetype(
        archetype_id=archetype_id,
        label="Workers",
        description="Test archetype",
        population_total=pop,
        issue_exposure=0.5,
        material_stake=0.5,
        symbolic_stake=0.5,
        vulnerability_to_policy=0.5,
        ability_to_influence_outcome=0.5,
        attention_capacity=0.6,
        attention_decay_rate=0.15,
        coordination_capacity=0.5,
        mobilization_capacity=0.5,
        legal_or_status_risk_sensitivity=0.5,
        min_split_population=25,
        min_split_share=0.05,
        max_child_cohorts=4,
    )


def _make_cohort_state(
    cohort_id: str = "coh-parent",
    archetype_id: str = "arch-1",
    population: int = 1000,
    stance: dict | None = None,
    expression: float = 0.5,
) -> CohortState:
    return CohortState(
        cohort_id=cohort_id,
        universe_id="u-1",
        tick=0,
        archetype_id=archetype_id,
        represented_population=population,
        population_share_of_archetype=1.0,
        issue_stance=stance or {"axis": 0.0},
        expression_level=expression,
        mobilization_mode="dormant",
        speech_mode="silent",
        attention=0.5,
        fatigue=0.1,
        prompt_temperature=0.7,
        representation_mode="population",
    )


def _make_child_spec(
    population: int,
    archetype_id: str = "arch-1",
    expression: float = 0.5,
) -> ChildSplitSpec:
    return ChildSplitSpec(
        archetype_id=archetype_id,
        represented_population=population,
        issue_stance={"axis": 0.0},
        expression_level=expression,
        mobilization_mode="dormant",
        speech_mode="silent",
    )


# ---------------------------------------------------------------------------
# Validation tests (pure)
# ---------------------------------------------------------------------------


def test_split_with_mismatched_populations_fails():
    archetype = _make_archetype(pop=1000)
    parent = _make_cohort_state(population=1000)
    proposal = SplitProposal(
        parent_cohort_id="coh-parent",
        children=[
            _make_child_spec(400),
            _make_child_spec(400),  # sums to 800 != 1000
        ],
        split_distance=0.5,
        rationale="test",
    )
    ok, err = evaluate_split_validity(
        parent=parent,
        proposal=proposal,
        archetype=archetype,
        params=SociologyParams(),
    )
    assert ok is False
    assert err is not None
    assert "represented_population" in err


def test_split_too_few_children_rejected_by_schema():
    """The Pydantic SplitProposal model rejects fewer than 2 children
    at construction time."""
    with pytest.raises(Exception):
        SplitProposal(
            parent_cohort_id="coh-parent",
            children=[_make_child_spec(1000)],
            split_distance=0.5,
            rationale="test",
        )


def test_split_with_too_many_children_fails():
    archetype = _make_archetype(pop=1000)
    archetype = archetype.model_copy(update={"max_child_cohorts": 2})
    parent = _make_cohort_state(population=1000)
    proposal = SplitProposal(
        parent_cohort_id="coh-parent",
        children=[
            _make_child_spec(300),
            _make_child_spec(300),
            _make_child_spec(400),
        ],
        split_distance=0.5,
        rationale="test",
    )
    ok, err = evaluate_split_validity(
        parent=parent,
        proposal=proposal,
        archetype=archetype,
        params=SociologyParams(),
    )
    assert ok is False
    assert err is not None
    assert "max_child_cohorts" in err or "limits" in err


def test_split_below_distance_threshold_fails():
    archetype = _make_archetype(pop=1000)
    parent = _make_cohort_state(population=1000)
    proposal = SplitProposal(
        parent_cohort_id="coh-parent",
        children=[_make_child_spec(500), _make_child_spec(500)],
        split_distance=0.05,  # below default 0.30
        rationale="test",
    )
    ok, err = evaluate_split_validity(
        parent=parent,
        proposal=proposal,
        archetype=archetype,
        params=SociologyParams(),
    )
    assert ok is False
    assert err is not None
    assert "split_distance" in err


def test_split_happy_path_validates():
    archetype = _make_archetype(pop=1000)
    parent = _make_cohort_state(population=1000)
    proposal = SplitProposal(
        parent_cohort_id="coh-parent",
        children=[_make_child_spec(600), _make_child_spec(400)],
        split_distance=0.6,
        rationale="test",
    )
    ok, err = evaluate_split_validity(
        parent=parent,
        proposal=proposal,
        archetype=archetype,
        params=SociologyParams(),
    )
    assert ok is True
    assert err is None


def test_merge_different_archetypes_fails():
    a = _make_cohort_state(cohort_id="c1", archetype_id="arch-1")
    b = _make_cohort_state(cohort_id="c2", archetype_id="arch-2")
    proposal = MergeProposal(
        cohort_ids=["c1", "c2"],
        archetype_id="arch-1",
        rationale="test",
    )
    ok, err = evaluate_merge_validity(
        cohorts=[a, b], proposal=proposal, params=SociologyParams()
    )
    assert ok is False
    assert err is not None
    assert "archetype" in err


def test_merge_similar_validates():
    a = _make_cohort_state(cohort_id="c1", expression=0.5, stance={"axis": 0.4})
    b = _make_cohort_state(cohort_id="c2", expression=0.55, stance={"axis": 0.45})
    proposal = MergeProposal(
        cohort_ids=["c1", "c2"],
        archetype_id="arch-1",
        rationale="test",
    )
    ok, err = evaluate_merge_validity(
        cohorts=[a, b],
        proposal=proposal,
        params=SociologyParams(),
        low_divergence_window_ok=True,
    )
    assert ok is True


def test_merge_low_divergence_window_required():
    a = _make_cohort_state(cohort_id="c1", expression=0.5)
    b = _make_cohort_state(cohort_id="c2", expression=0.5)
    proposal = MergeProposal(
        cohort_ids=["c1", "c2"],
        archetype_id="arch-1",
        rationale="test",
    )
    ok, err = evaluate_merge_validity(
        cohorts=[a, b],
        proposal=proposal,
        params=SociologyParams(),
        low_divergence_window_ok=False,
    )
    assert ok is False
    assert "divergence" in err


# ---------------------------------------------------------------------------
# DB-bound tests (SQLite shadow tables)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session():
    """Yield a fresh in-memory async SQLite session bound to the shadow tables."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(_ShadowBase.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


async def _insert_archetype(
    session: AsyncSession, archetype_id: str, pop: int
) -> _ArchetypeShadow:
    arch = _ArchetypeShadow(archetype_id=archetype_id, population_total=pop)
    session.add(arch)
    await session.flush()
    return arch


async def _insert_parent_cohort(
    session: AsyncSession,
    cohort_id: str = "coh-parent",
    archetype_id: str = "arch-1",
    population: int = 1000,
    tick: int = 0,
) -> _CohortShadow:
    row = _CohortShadow(
        cohort_id=cohort_id,
        tick=tick,
        universe_id="u-1",
        archetype_id=archetype_id,
        parent_cohort_id=None,
        child_cohort_ids=[],
        represented_population=population,
        population_share_of_archetype=1.0,
        issue_stance={"axis": 0.0},
        expression_level=0.5,
        mobilization_mode="dormant",
        speech_mode="silent",
        emotions={"anger": 1.0},
        behavior_state={"x": 0.5},
        attention=0.5,
        fatigue=0.1,
        grievance=0.2,
        perceived_efficacy=0.5,
        perceived_majority={},
        fear_of_isolation=0.1,
        willingness_to_speak=0.5,
        identity_salience=0.5,
        visible_trust_summary={},
        exposure_summary={},
        dependency_summary={},
        memory_session_id=None,
        recent_post_ids=[],
        queued_event_ids=[],
        previous_action_ids=[],
        prompt_temperature=0.7,
        representation_mode="population",
        allowed_tools=[],
        is_active=True,
    )
    session.add(row)
    await session.flush()
    return row


@pytest.fixture(autouse=True)
def _patch_cohort_model(monkeypatch):
    """Patch backend.app.sociology.split_merge.CohortStateModel to use the
    SQLite-friendly shadow row class for the duration of each test."""
    import backend.app.sociology.split_merge as sm
    import backend.app.sociology.transitions as tr

    monkeypatch.setattr(sm, "CohortStateModel", _CohortShadow)
    monkeypatch.setattr(tr, "CohortStateModel", _CohortShadow)
    yield


async def test_commit_split_happy_path_conserves_population(db_session):
    archetype = _make_archetype(pop=1000)
    await _insert_archetype(db_session, "arch-1", 1000)
    parent_row = await _insert_parent_cohort(db_session, population=1000)

    proposal = SplitProposal(
        parent_cohort_id="coh-parent",
        children=[
            _make_child_spec(600),
            _make_child_spec(400),
        ],
        split_distance=0.5,
        rationale="test",
    )
    children = await commit_split(
        db_session,
        parent=parent_row,
        proposal=proposal,
        current_tick=1,
        archetype=archetype,
        params=SociologyParams(),
    )

    assert len(children) == 2
    # Parent marked inactive.
    await db_session.refresh(parent_row)
    assert parent_row.is_active is False
    # Children rows present and conserve population.
    assert sum(c.represented_population for c in children) == 1000
    assert all(c.is_active for c in children)
    # Parent records its child IDs.
    assert sorted(parent_row.child_cohort_ids) == sorted(c.cohort_id for c in children)


async def test_commit_split_invalid_population_raises(db_session):
    archetype = _make_archetype(pop=1000)
    await _insert_archetype(db_session, "arch-1", 1000)
    parent_row = await _insert_parent_cohort(db_session, population=1000)

    proposal = SplitProposal(
        parent_cohort_id="coh-parent",
        children=[
            _make_child_spec(600),
            _make_child_spec(300),  # 900 != 1000
        ],
        split_distance=0.5,
        rationale="test",
    )
    with pytest.raises(ValueError):
        await commit_split(
            db_session,
            parent=parent_row,
            proposal=proposal,
            current_tick=1,
            archetype=archetype,
            params=SociologyParams(),
        )


async def test_audit_population_conservation_balanced(db_session):
    """Active cohorts at the current tick sum to archetype.population_total -> no errors."""
    await _insert_archetype(db_session, "arch-1", 1000)

    # Add two active cohorts at tick=2 summing to 1000.
    a = _CohortShadow(
        cohort_id="a", tick=2, universe_id="u-1", archetype_id="arch-1",
        parent_cohort_id=None, child_cohort_ids=[],
        represented_population=600, population_share_of_archetype=0.6,
        issue_stance={}, expression_level=0.5, mobilization_mode="dormant",
        speech_mode="silent", emotions={}, behavior_state={}, attention=0.5,
        fatigue=0.0, grievance=0.0, perceived_efficacy=0.5, perceived_majority={},
        fear_of_isolation=0.0, willingness_to_speak=0.5, identity_salience=0.5,
        visible_trust_summary={}, exposure_summary={}, dependency_summary={},
        memory_session_id=None, recent_post_ids=[], queued_event_ids=[],
        previous_action_ids=[], prompt_temperature=0.7,
        representation_mode="population", allowed_tools=[], is_active=True,
    )
    b = _CohortShadow(
        cohort_id="b", tick=2, universe_id="u-1", archetype_id="arch-1",
        parent_cohort_id=None, child_cohort_ids=[],
        represented_population=400, population_share_of_archetype=0.4,
        issue_stance={}, expression_level=0.5, mobilization_mode="dormant",
        speech_mode="silent", emotions={}, behavior_state={}, attention=0.5,
        fatigue=0.0, grievance=0.0, perceived_efficacy=0.5, perceived_majority={},
        fear_of_isolation=0.0, willingness_to_speak=0.5, identity_salience=0.5,
        visible_trust_summary={}, exposure_summary={}, dependency_summary={},
        memory_session_id=None, recent_post_ids=[], queued_event_ids=[],
        previous_action_ids=[], prompt_temperature=0.7,
        representation_mode="population", allowed_tools=[], is_active=True,
    )
    db_session.add_all([a, b])
    await db_session.flush()

    # Patch the loader inside split_merge to use shadow archetype too.
    import backend.app.models.cohorts as cm
    orig = cm.PopulationArchetypeModel
    cm.PopulationArchetypeModel = _ArchetypeShadow
    try:
        errors = await audit_population_conservation(db_session, "u-1", 2)
    finally:
        cm.PopulationArchetypeModel = orig

    assert errors == []


async def test_audit_population_conservation_detects_mismatch(db_session):
    await _insert_archetype(db_session, "arch-1", 1000)
    # Only 500 active — should flag
    a = _CohortShadow(
        cohort_id="a", tick=2, universe_id="u-1", archetype_id="arch-1",
        parent_cohort_id=None, child_cohort_ids=[],
        represented_population=500, population_share_of_archetype=0.5,
        issue_stance={}, expression_level=0.5, mobilization_mode="dormant",
        speech_mode="silent", emotions={}, behavior_state={}, attention=0.5,
        fatigue=0.0, grievance=0.0, perceived_efficacy=0.5, perceived_majority={},
        fear_of_isolation=0.0, willingness_to_speak=0.5, identity_salience=0.5,
        visible_trust_summary={}, exposure_summary={}, dependency_summary={},
        memory_session_id=None, recent_post_ids=[], queued_event_ids=[],
        previous_action_ids=[], prompt_temperature=0.7,
        representation_mode="population", allowed_tools=[], is_active=True,
    )
    db_session.add(a)
    await db_session.flush()

    import backend.app.models.cohorts as cm
    orig = cm.PopulationArchetypeModel
    cm.PopulationArchetypeModel = _ArchetypeShadow
    try:
        errors = await audit_population_conservation(db_session, "u-1", 2)
    finally:
        cm.PopulationArchetypeModel = orig

    assert len(errors) == 1
    assert "population conservation" in errors[0].lower()


async def test_commit_merge_combines_two_cohorts(db_session):
    archetype = _make_archetype(pop=1000)
    await _insert_archetype(db_session, "arch-1", 1000)

    # Two similar cohorts to merge.
    a = _CohortShadow(
        cohort_id="ca", tick=3, universe_id="u-1", archetype_id="arch-1",
        parent_cohort_id=None, child_cohort_ids=[],
        represented_population=600, population_share_of_archetype=0.6,
        issue_stance={"axis": 0.5}, expression_level=0.5,
        mobilization_mode="dormant", speech_mode="silent",
        emotions={"anger": 2.0}, behavior_state={"x": 0.5}, attention=0.5,
        fatigue=0.1, grievance=0.2, perceived_efficacy=0.5, perceived_majority={},
        fear_of_isolation=0.1, willingness_to_speak=0.5, identity_salience=0.5,
        visible_trust_summary={}, exposure_summary={}, dependency_summary={},
        memory_session_id=None, recent_post_ids=[], queued_event_ids=[],
        previous_action_ids=[], prompt_temperature=0.7,
        representation_mode="population", allowed_tools=[], is_active=True,
    )
    b = _CohortShadow(
        cohort_id="cb", tick=3, universe_id="u-1", archetype_id="arch-1",
        parent_cohort_id=None, child_cohort_ids=[],
        represented_population=400, population_share_of_archetype=0.4,
        issue_stance={"axis": 0.55}, expression_level=0.55,
        mobilization_mode="dormant", speech_mode="silent",
        emotions={"anger": 1.0}, behavior_state={"x": 0.4}, attention=0.5,
        fatigue=0.15, grievance=0.3, perceived_efficacy=0.5, perceived_majority={},
        fear_of_isolation=0.1, willingness_to_speak=0.5, identity_salience=0.5,
        visible_trust_summary={}, exposure_summary={}, dependency_summary={},
        memory_session_id=None, recent_post_ids=[], queued_event_ids=[],
        previous_action_ids=[], prompt_temperature=0.7,
        representation_mode="population", allowed_tools=[], is_active=True,
    )
    db_session.add_all([a, b])
    await db_session.flush()

    proposal = MergeProposal(
        cohort_ids=["ca", "cb"], archetype_id="arch-1", rationale="similar"
    )
    merged = await commit_merge(
        db_session,
        cohorts=[a, b],
        proposal=proposal,
        current_tick=4,
        archetype=archetype,
    )

    await db_session.refresh(a)
    await db_session.refresh(b)
    assert a.is_active is False
    assert b.is_active is False
    assert merged.is_active is True
    assert merged.represented_population == 1000
    # Weighted average emotions
    assert merged.emotions["anger"] == pytest.approx(0.6 * 2.0 + 0.4 * 1.0)
