"""Integration tests for backend.app.branching.branch_engine.

Uses an async SQLite engine bound to *shadow* tables that mirror the
production ORM classes (JSON instead of JSONB, no ARRAY columns, no FKs to
unrelated tables).  The production model classes are monkey-patched in
each module that imports them so the engine code under test transparently
operates on the shadow tables.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backend.app.branching.branch_engine import commit_branch
from backend.app.branching.namespacing import namespace_id, suffix_for
from backend.app.schemas.branching import (
    ActorStateOverrideDelta,
    BranchPolicyResult,
    CounterfactualEventRewriteDelta,
    HeroDecisionOverrideDelta,
    ParameterShiftDelta,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Shadow tables (SQLite-friendly subset of production schema)
# ---------------------------------------------------------------------------


class _ShadowBase(DeclarativeBase):
    pass


class _UniverseShadow(_ShadowBase):
    __tablename__ = "universes"

    universe_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    big_bang_id: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_universe_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lineage_path: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    branch_from_tick: Mapped[int | None] = mapped_column(Integer, nullable=True)
    branch_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    branch_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    branch_delta: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    current_tick: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latest_metrics: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    child_universe_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    killed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class _BranchNodeShadow(_ShadowBase):
    __tablename__ = "branch_nodes"

    universe_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    parent_universe_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    child_universe_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    branch_tick: Mapped[int] = mapped_column(Integer, nullable=False)
    branch_point_id: Mapped[str] = mapped_column(String(64), nullable=False)
    branch_trigger: Mapped[str] = mapped_column(Text, nullable=False)
    branch_delta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    metrics_summary: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    cost_estimate: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    descendant_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lineage_path: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)

    def to_schema(self):  # mimic BranchNodeModel.to_schema
        from backend.app.schemas.branching import BranchNode

        return BranchNode(
            universe_id=self.universe_id,
            parent_universe_id=self.parent_universe_id,
            child_universe_ids=list(self.child_universe_ids or []),
            depth=self.depth,
            branch_tick=self.branch_tick,
            branch_point_id=self.branch_point_id,
            branch_trigger=self.branch_trigger,
            branch_delta=dict(self.branch_delta or {}),
            status=self.status,  # type: ignore[arg-type]
            metrics_summary=dict(self.metrics_summary or {}),
            cost_estimate=dict(self.cost_estimate or {}),
            descendant_count=self.descendant_count,
        )


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


class _HeroShadow(_ShadowBase):
    __tablename__ = "hero_states"

    hero_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tick: Mapped[int] = mapped_column(Integer, primary_key=True)
    universe_id: Mapped[str] = mapped_column(String(64), nullable=False)
    current_emotions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    current_issue_stances: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    attention: Mapped[float] = mapped_column(Float, nullable=False)
    fatigue: Mapped[float] = mapped_column(Float, nullable=False)
    perceived_pressure: Mapped[float] = mapped_column(Float, nullable=False)
    current_strategy: Mapped[str] = mapped_column(Text, nullable=False, default="")
    queued_events: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    recent_posts: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    memory_session_id: Mapped[str | None] = mapped_column(String, nullable=True)


class _EventShadow(_ShadowBase):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    universe_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_tick: Mapped[int] = mapped_column(Integer, nullable=False)
    scheduled_tick: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_ticks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    participants: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    target_audience: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False)
    preconditions: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    expected_effects: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    actual_effects: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    risk_level: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    parent_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_llm_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class _PostShadow(_ShadowBase):
    __tablename__ = "social_posts"

    post_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    universe_id: Mapped[str] = mapped_column(String(64), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    tick_created: Mapped[int] = mapped_column(Integer, nullable=False)
    author_actor_id: Mapped[str] = mapped_column(String(64), nullable=False)
    author_avatar_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    stance_signal: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    emotion_signal: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    credibility_signal: Mapped[float] = mapped_column(Float, nullable=False)
    visibility_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    reach_score: Mapped[float] = mapped_column(Float, nullable=False)
    hot_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    reactions: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    repost_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    upvote_power_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    downvote_power_total: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(_ShadowBase.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def _patch_models(monkeypatch):
    """Replace production model classes with the shadow tables for the
    duration of each test.  Both ``backend.app.models.<x>.<Model>`` and any
    re-exported alias are patched so the engine code transparently sees the
    shadows when it calls ``from backend.app.models.<x> import <Model>``.
    """
    import backend.app.models.branches as bm
    import backend.app.models.cohorts as cm
    import backend.app.models.events as em
    import backend.app.models.heroes as hm
    import backend.app.models.posts as pm
    import backend.app.models.universes as um

    monkeypatch.setattr(um, "UniverseModel", _UniverseShadow)
    monkeypatch.setattr(bm, "BranchNodeModel", _BranchNodeShadow)
    monkeypatch.setattr(cm, "CohortStateModel", _CohortShadow)
    monkeypatch.setattr(hm, "HeroStateModel", _HeroShadow)
    monkeypatch.setattr(em, "EventModel", _EventShadow)
    monkeypatch.setattr(pm, "SocialPostModel", _PostShadow)
    yield


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


async def _make_root_universe(session: AsyncSession, *, universe_id="U_root_universe", big_bang_id="bb-1") -> _UniverseShadow:
    row = _UniverseShadow(
        universe_id=universe_id,
        big_bang_id=big_bang_id,
        parent_universe_id=None,
        lineage_path=[universe_id],
        branch_from_tick=0,
        branch_depth=0,
        status="active",
        branch_reason="",
        branch_delta=None,
        current_tick=3,
        latest_metrics={"divergence": 0.5, "expression_mass": 1.0},
        child_universe_ids=[],
        created_at=_now(),
    )
    session.add(row)
    await session.flush()
    return row


async def _make_seed_cohorts(session: AsyncSession, universe_id: str, tick: int = 3):
    a = _CohortShadow(
        cohort_id="coh-A",
        tick=tick,
        universe_id=universe_id,
        archetype_id="arch-1",
        parent_cohort_id=None,
        child_cohort_ids=[],
        represented_population=600,
        population_share_of_archetype=0.6,
        issue_stance={"axis": 0.4},
        expression_level=0.5,
        mobilization_mode="dormant",
        speech_mode="silent",
        emotions={"anger": 2.0},
        behavior_state={"protest": 0.1},
        attention=0.5,
        fatigue=0.1,
        grievance=0.2,
        perceived_efficacy=0.5,
        perceived_majority={"axis": 0.5},
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
    b = _CohortShadow(
        cohort_id="coh-B",
        tick=tick,
        universe_id=universe_id,
        archetype_id="arch-1",
        parent_cohort_id=None,
        child_cohort_ids=[],
        represented_population=400,
        population_share_of_archetype=0.4,
        issue_stance={"axis": 0.6},
        expression_level=0.4,
        mobilization_mode="dormant",
        speech_mode="silent",
        emotions={"anger": 1.0},
        behavior_state={"protest": 0.0},
        attention=0.4,
        fatigue=0.2,
        grievance=0.1,
        perceived_efficacy=0.5,
        perceived_majority={"axis": 0.5},
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
    session.add_all([a, b])
    await session.flush()


async def _make_seed_event(
    session: AsyncSession,
    universe_id: str,
    *,
    event_id: str = "evt-1",
    description: str = "defensive statement",
    status: str = "completed",
    created_tick: int = 2,
    scheduled_tick: int = 3,
):
    row = _EventShadow(
        event_id=event_id,
        universe_id=universe_id,
        created_tick=created_tick,
        scheduled_tick=scheduled_tick,
        duration_ticks=None,
        event_type="press_conference",
        title="Statement",
        description=description,
        created_by_actor_id="hero-1",
        participants=[],
        target_audience=[],
        visibility="public",
        preconditions=[],
        expected_effects={"opinion_shift": 0.05},
        actual_effects={"opinion_shift": 0.04},
        risk_level=0.2,
        status=status,
        parent_event_id=None,
        source_llm_call_id=None,
    )
    session.add(row)
    await session.flush()


def _approve(divergence: float = 0.6) -> BranchPolicyResult:
    return BranchPolicyResult(
        decision="approve",
        reason="ok",
        cost_estimate={"tokens": 1000, "calls": 10},
        divergence_score=divergence,
    )


def _candidate(divergence: float = 0.4) -> BranchPolicyResult:
    return BranchPolicyResult(
        decision="downgrade_to_candidate",
        reason="capacity",
        cost_estimate={"tokens": 1000, "calls": 10},
        divergence_score=divergence,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_commit_branch_counterfactual_event_rewrite_happy_path(db_session):
    parent = await _make_root_universe(db_session)
    await _make_seed_cohorts(db_session, parent.universe_id, tick=3)
    await _make_seed_event(db_session, parent.universe_id, event_id="evt-1", status="completed")

    delta = CounterfactualEventRewriteDelta(
        type="counterfactual_event_rewrite",
        target_event_id="evt-1",
        parent_version="defensive statement",
        child_version="apology plus independent audit",
    )

    result = await commit_branch(
        session=db_session,
        parent_universe=parent,
        branch_from_tick=3,
        delta=delta,
        branch_reason="god_review_spawn_active",
        policy_result=_approve(),
    )

    # ----- Universe row -----
    child_row = await db_session.get(_UniverseShadow, result.child_universe_id)
    assert child_row is not None
    assert child_row.status == "active"
    assert child_row.lineage_path == [parent.universe_id, result.child_universe_id]
    assert child_row.branch_from_tick == 3
    assert child_row.branch_depth == 1
    assert child_row.parent_universe_id == parent.universe_id
    assert child_row.current_tick == 3
    assert child_row.branch_delta == delta.model_dump()

    # ----- BranchNode row -----
    bn_row = await db_session.get(_BranchNodeShadow, result.child_universe_id)
    assert bn_row is not None
    assert bn_row.depth == 1
    assert bn_row.branch_tick == 3
    assert bn_row.parent_universe_id == parent.universe_id
    assert bn_row.status == "active"
    assert bn_row.branch_point_id == f"{parent.universe_id}@t3"
    assert bn_row.cost_estimate == {"tokens": 1000, "calls": 10}

    # ----- Parent's child_universe_ids appended -----
    await db_session.refresh(parent)
    assert result.child_universe_id in (parent.child_universe_ids or [])

    # ----- Child cohort_states copied at tick=3 -----
    suffix = suffix_for(result.child_universe_id)
    from sqlalchemy import select

    child_cohorts = (
        await db_session.execute(
            select(_CohortShadow).where(
                _CohortShadow.universe_id == result.child_universe_id,
                _CohortShadow.tick == 3,
            )
        )
    ).scalars().all()
    assert len(child_cohorts) == 2
    expected_ids = {namespace_id("coh-A", suffix), namespace_id("coh-B", suffix)}
    assert {c.cohort_id for c in child_cohorts} == expected_ids
    # All should map back to the new universe
    assert all(c.universe_id == result.child_universe_id for c in child_cohorts)

    # ----- Child event copied + rewritten -----
    namespaced_evt = namespace_id("evt-1", suffix)
    child_evt = await db_session.get(_EventShadow, namespaced_evt)
    assert child_evt is not None
    assert child_evt.universe_id == result.child_universe_id
    assert child_evt.description == "apology plus independent audit"
    assert child_evt.actual_effects is None
    # was completed → reset to scheduled
    assert child_evt.status == "scheduled"

    # ----- Result payload -----
    assert result.delta_apply_summary["applied"] is True
    assert result.delta_apply_summary["delta_type"] == "counterfactual_event_rewrite"


async def test_commit_branch_actor_state_override_sets_attention(db_session):
    parent = await _make_root_universe(db_session, universe_id="U_root_aso000")
    await _make_seed_cohorts(db_session, parent.universe_id, tick=3)

    delta = ActorStateOverrideDelta(
        type="actor_state_override",
        actor_id="coh-A",
        field="attention",
        new_value=0.95,
    )

    result = await commit_branch(
        session=db_session,
        parent_universe=parent,
        branch_from_tick=3,
        delta=delta,
        branch_reason="hero override",
        policy_result=_approve(),
    )

    suffix = suffix_for(result.child_universe_id)
    namespaced = namespace_id("coh-A", suffix)
    child_cohort = await db_session.get(_CohortShadow, (namespaced, 3))
    assert child_cohort is not None
    assert child_cohort.attention == 0.95
    assert result.delta_apply_summary["applied"] is True
    assert result.delta_apply_summary["new_value"] == 0.95


async def test_commit_branch_rolls_back_when_delta_not_applied(db_session):
    parent = await _make_root_universe(db_session, universe_id="U_root_noop00")
    parent_id = parent.universe_id
    await _make_seed_cohorts(db_session, parent.universe_id, tick=3)

    delta = ActorStateOverrideDelta(
        type="actor_state_override",
        actor_id="missing-cohort",
        field="attention",
        new_value=0.95,
    )

    with pytest.raises(ValueError, match="refusing to commit no-op branch"):
        await commit_branch(
            session=db_session,
            parent_universe=parent,
            branch_from_tick=3,
            delta=delta,
            branch_reason="missing actor",
            policy_result=_approve(),
        )

    from sqlalchemy import select

    children = (
        await db_session.execute(
            select(_UniverseShadow).where(_UniverseShadow.parent_universe_id == parent_id)
        )
    ).scalars().all()
    assert children == []


async def test_commit_branch_depth_2_has_lineage_length_3(db_session):
    parent = await _make_root_universe(db_session, universe_id="U_root_d2t000")
    await _make_seed_cohorts(db_session, parent.universe_id, tick=3)

    delta = ParameterShiftDelta(
        type="parameter_shift",
        target="news_channel.local_press.bias",
        delta={"risk_salience": 0.2},
    )
    first = await commit_branch(
        session=db_session,
        parent_universe=parent,
        branch_from_tick=3,
        delta=delta,
        branch_reason="depth-1 branch",
        policy_result=_approve(),
    )
    # Re-fetch the freshly-inserted child as the new parent for depth-2.
    child_row = await db_session.get(_UniverseShadow, first.child_universe_id)
    # Cohort_states at child tick 3 are required for the next branch's seed
    # (commit_branch copies them).  They were already cloned by the first
    # commit_branch call, so we can branch off the child immediately.

    delta2 = ParameterShiftDelta(
        type="parameter_shift",
        target="sociology.attention_decay",
        delta={"alpha": 0.05},
    )
    second = await commit_branch(
        session=db_session,
        parent_universe=child_row,
        branch_from_tick=3,
        delta=delta2,
        branch_reason="depth-2 branch",
        policy_result=_approve(),
    )

    assert len(second.lineage_path) == 3
    grand_row = await db_session.get(_UniverseShadow, second.child_universe_id)
    assert grand_row is not None
    assert grand_row.branch_depth == 2
    assert grand_row.lineage_path == [
        parent.universe_id,
        first.child_universe_id,
        second.child_universe_id,
    ]
    # parameter_shift recorded on Universe.branch_delta.parameter_overrides
    assert grand_row.branch_delta is not None
    overrides = grand_row.branch_delta.get("parameter_overrides")
    assert overrides == {"sociology.attention_decay": {"alpha": 0.05}}


async def test_commit_branch_candidate_status_does_not_enqueue(db_session, monkeypatch):
    parent = await _make_root_universe(db_session, universe_id="U_root_cnd000")
    await _make_seed_cohorts(db_session, parent.universe_id, tick=3)

    enqueue_calls: list = []

    async def _spy_enqueue(envelope, **kw):  # pragma: no cover - called only on failure
        enqueue_calls.append(envelope)
        return "fake-id"

    def _spy_make_envelope(**kw):  # pragma: no cover
        return kw

    # Patch the scheduler so we can assert it is NOT called for candidates.
    import backend.app.workers.scheduler as sched
    monkeypatch.setattr(sched, "enqueue", _spy_enqueue)
    monkeypatch.setattr(sched, "make_envelope", _spy_make_envelope)

    delta = ActorStateOverrideDelta(
        type="actor_state_override",
        actor_id="coh-B",
        field="grievance",
        new_value=0.9,
    )
    result = await commit_branch(
        session=db_session,
        parent_universe=parent,
        branch_from_tick=3,
        delta=delta,
        branch_reason="capacity backoff",
        policy_result=_candidate(),
    )

    assert result.status == "candidate"
    assert result.enqueued is False
    assert enqueue_calls == []
    # Universe row reflects candidate status.
    child_row = await db_session.get(_UniverseShadow, result.child_universe_id)
    assert child_row.status == "candidate"
    bn_row = await db_session.get(_BranchNodeShadow, result.child_universe_id)
    assert bn_row.status == "candidate"


async def test_commit_branch_hero_decision_override_records_on_universe(db_session):
    parent = await _make_root_universe(db_session, universe_id="U_root_hdo000")
    await _make_seed_cohorts(db_session, parent.universe_id, tick=3)

    delta = HeroDecisionOverrideDelta(
        type="hero_decision_override",
        hero_id="hero-7",
        tick=4,
        new_decision={"action": "press_conference", "stance": "concede"},
    )
    result = await commit_branch(
        session=db_session,
        parent_universe=parent,
        branch_from_tick=3,
        delta=delta,
        branch_reason="hero override test",
        policy_result=_approve(),
    )
    child_row = await db_session.get(_UniverseShadow, result.child_universe_id)
    overrides = (child_row.branch_delta or {}).get("hero_decision_overrides") or {}
    assert "hero-7@t4" in overrides
    assert overrides["hero-7@t4"]["action"] == "press_conference"
