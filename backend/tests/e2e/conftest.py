"""Shared fixtures for B11-B end-to-end tests.

These tests exercise larger slices of the system than `tests/integration/`.
Most fixtures are reused or duplicated from `tests/integration/conftest.py`
(SQLite shadow engine, ASGI client) so the suite runs without Postgres or a
live Redis broker.

External services that the PRD requires in production (OpenRouter, Zep,
Celery broker) are stubbed:

* OpenRouter        -> `_CannedProvider` registered via
                       `register_provider("openrouter", ...)`.
* Zep               -> default off; tests that need it monkey-patch the
                       memory factory.
* Celery broker     -> `enqueue` is wrapped to record calls and return the
                       envelope job_id without contacting Redis.
* Redis (rate limit + lineage cache) -> `fakeredis.aioredis.FakeRedis`.

Any test that fundamentally cannot run without the real broker should
declare `@pytest.mark.requires_broker` and `pytest.skip(...)` at top.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

import fakeredis.aioredis
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.models.base import Base


# ---------------------------------------------------------------------------
# SQLite JSONB / ARRAY shim — same trick the integration conftest uses.
# ---------------------------------------------------------------------------


def _patch_sqlite_types() -> None:
    """Replace Postgres-specific column types with SQLite-compatible JSON."""
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, JSONB):
                col.type = JSON()
            elif isinstance(col.type, ARRAY):
                col.type = JSON()


_patch_sqlite_types()


TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


# ---------------------------------------------------------------------------
# Async event loop / asyncio backend
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Engine + session
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def test_engine():
    """Function-scoped engine so each E2E test gets a fresh DB.

    E2E tests insert + branch + export, so a shared engine across tests
    leaks state. Function scope keeps tests independent.
    """
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(test_engine) -> AsyncGenerator[AsyncSession, None]:
    """Function-scoped async session — rolls back after each test."""
    factory = async_sessionmaker(
        test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with factory() as session:
        yield session
        await session.rollback()


# ---------------------------------------------------------------------------
# Live ASGI app + client
# ---------------------------------------------------------------------------


@pytest.fixture
def live_app(test_engine, db_session):
    """Return the production FastAPI app with the DB dependency overridden."""
    from backend.app.core.db import get_session
    from backend.app.main import app

    async def _override_get_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    yield app
    app.dependency_overrides.pop(get_session, None)


@pytest_asyncio.fixture
async def e2e_client(live_app) -> AsyncGenerator[AsyncClient, None]:
    """`httpx.AsyncClient` over an `ASGITransport` of the live app."""
    async with AsyncClient(
        transport=ASGITransport(app=live_app), base_url="http://test"
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# Fake redis for rate limiter, lineage cache, idempotency keys.
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def redis_client():
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose(close_connection_pool=True)


@pytest.fixture
def rate_limiter(redis_client):
    from backend.app.providers import ProviderRateLimiter

    return ProviderRateLimiter(
        redis_client,
        provider="openrouter",
        rpm_limit=600,
        tpm_limit=1_000_000,
        max_concurrency=8,
        daily_budget_usd=None,
        jitter=False,
    )


@pytest.fixture
def routing():
    from backend.app.providers import RoutingTable

    return RoutingTable.defaults()


@pytest.fixture(autouse=True)
def _provider_registry_reset():
    """Drop any registered providers between tests (parity with integration suite)."""
    from backend.app.providers import clear_registry

    clear_registry()
    yield
    clear_registry()


# ---------------------------------------------------------------------------
# Mocked provider that returns canned responses keyed by job_type.
# ---------------------------------------------------------------------------


from backend.app.providers.base import BaseProvider  # noqa: E402
from backend.app.schemas.llm import LLMResult, ProviderHealth  # noqa: E402


class CannedProvider(BaseProvider):
    """Mock LLM provider that returns canned responses by job_type."""

    def __init__(
        self,
        *,
        responses: dict[str, dict | list[dict]] | None = None,
        default_response: dict | None = None,
        name: str = "openrouter",
    ) -> None:
        self.name = name
        self._responses = responses or {}
        self._default = default_response or {}
        # rotating cursor per job_type when responses[k] is a list
        self._cursors: dict[str, int] = {}
        self.calls: list[dict] = []

    def _next(self, job_type: str) -> dict:
        candidate = self._responses.get(job_type)
        if candidate is None:
            return dict(self._default)
        if isinstance(candidate, list):
            cur = self._cursors.get(job_type, 0)
            payload = candidate[cur % len(candidate)]
            self._cursors[job_type] = cur + 1
            return dict(payload)
        return dict(candidate)

    async def generate_structured(self, prompt, config):
        # Locate the job_type — initializer/god/etc. encode it via metadata
        # or through the output_schema_id. Fall back to actor_kind.
        meta = getattr(prompt, "metadata", None) or {}
        job_type = meta.get("job_type") or ""
        if not job_type:
            schema_id = getattr(prompt, "output_schema_id", "") or ""
            schema_to_job = {
                "initializer_schema": "initialize_big_bang",
                "god_review_schema": "god_agent_review",
                "cohort_decision_schema": "agent_deliberation_batch",
                "hero_decision_schema": "agent_deliberation_batch",
            }
            job_type = schema_to_job.get(schema_id, "")
        if not job_type:
            actor_kind = getattr(prompt, "actor_kind", "")
            job_type = {
                "god": "god_agent_review",
                "cohort": "agent_deliberation_batch",
                "hero": "agent_deliberation_batch",
            }.get(actor_kind, "")
        payload = self._next(job_type)
        self.calls.append({"job_type": job_type, "model": config.model})
        return LLMResult(
            call_id=self._make_call_id("mock"),
            provider=self.name,
            model_used=config.model,
            prompt_tokens=120,
            completion_tokens=400,
            total_tokens=520,
            cost_usd=0.001,
            latency_ms=42,
            parsed_json=payload,
            tool_calls=[],
            raw_response={"id": "mock-resp", "model": config.model},
            created_at=datetime.now(UTC),
            repaired_once=False,
        )

    async def generate_text(self, prompt, config):
        return await self.generate_structured(prompt, config)

    async def embed(self, texts, config):
        raise NotImplementedError

    async def healthcheck(self):
        return ProviderHealth(provider=self.name, ok=True, latency_ms=1, details={})


# ---------------------------------------------------------------------------
# Canned LLM payloads
# ---------------------------------------------------------------------------


def canned_initializer_payload() -> dict:
    """Minimal but schema-valid initializer payload — 1 archetype, 1 hero,
    1 channel, 1 event."""
    return {
        "scenario_summary": (
            "Bay Area gig-workers dispute a sudden pay-cut announcement by a "
            "delivery operator. Couriers debate whether to call a strike, "
            "while local press covers organizer statements and the operator "
            "weighs whether to rescind the cut after public backlash."
        ),
        "archetypes": [
            {
                "id": "arch_workers",
                "label": "Gig Workers",
                "description": "Drivers and couriers contesting pay cuts.",
                "population_total": 1000,
                "geography": {"region_label": "Bay Area", "scope": "metro"},
                "issue_exposure": 0.8,
                "material_stake": 0.9,
                "symbolic_stake": 0.5,
                "vulnerability_to_policy": 0.7,
                "ability_to_influence_outcome": 0.4,
                "ideology_axes": {"economic": -0.4},
                "value_priors": {},
                "behavior_axes": {"protest_propensity": 0.5},
                "baseline_media_diet": {"twitter": 0.6},
                "preferred_channels": ["twitter"],
                "platform_access": {"twitter": 0.8},
                "attention_capacity": 0.7,
                "attention_decay_rate": 0.15,
                "baseline_trust_priors": {},
                "identity_tags": ["worker"],
                "ingroup_affinities": {"workers": 0.8},
                "outgroup_distances": {},
                "allowed_action_classes": ["read", "social", "event_minor"],
                "coordination_capacity": 0.6,
                "mobilization_capacity": 0.6,
                "legal_or_status_risk_sensitivity": 0.4,
                "min_split_population": 50,
                "min_split_share": 0.1,
                "max_child_cohorts": 4,
            },
            {
                "id": "arch_managers",
                "label": "Operator Management",
                "description": "Decision-makers at the delivery operator weighing the cut.",
                "population_total": 50,
                "geography": {"region_label": "Bay Area", "scope": "metro"},
                "issue_exposure": 0.7,
                "material_stake": 0.8,
                "symbolic_stake": 0.4,
                "vulnerability_to_policy": 0.3,
                "ability_to_influence_outcome": 0.85,
                "ideology_axes": {"economic": 0.3},
                "value_priors": {},
                "behavior_axes": {"strategic_caution": 0.6},
                "baseline_media_diet": {"twitter": 0.3},
                "preferred_channels": ["press_release"],
                "platform_access": {"twitter": 0.4},
                "attention_capacity": 0.6,
                "attention_decay_rate": 0.18,
                "baseline_trust_priors": {},
                "identity_tags": ["manager"],
                "ingroup_affinities": {"managers": 0.7},
                "outgroup_distances": {"workers": 0.4},
                "allowed_action_classes": ["read", "social", "event_minor"],
                "coordination_capacity": 0.7,
                "mobilization_capacity": 0.3,
                "legal_or_status_risk_sensitivity": 0.6,
                "min_split_population": 5,
                "min_split_share": 0.1,
                "max_child_cohorts": 3,
            },
        ],
        "heroes": [
            {
                "id": "hero_organizer",
                "label": "Organizer Mia Reyes",
                "description": "Veteran labor organizer.",
                "role": "organizer",
                "institution": "labor_council",
                "location_scope": "metro",
                "public_reach": 0.6,
                "institutional_power": 0.3,
                "financial_power": 0.2,
                "agenda_control": 0.4,
                "media_access": 0.5,
                "ideology_axes": {"economic": -0.5},
                "value_priors": {},
                "trust_priors": {},
                "behavioral_axes": {"strategic_caution": 0.4},
                "volatility": 0.4,
                "ego_sensitivity": 0.3,
                "strategic_discipline": 0.6,
                "controversy_tolerance": 0.5,
                "direct_event_power": 0.5,
                "scheduling_permissions": ["public_meeting"],
                "allowed_channels": ["twitter"],
            }
        ],
        "channels": [
            {
                "id": "ch_twitter",
                "type": "social_microblog",
                "label": "Local Twitter",
                "audience_size": 5000,
                "credibility_prior": 0.4,
            }
        ],
        "initial_events": [
            {
                "event_type": "policy_announcement",
                "title": "Pay-cut announcement",
                "description": "Operator announces a pay cut.",
                "scheduled_tick": 0,
                "duration_ticks": 1,
                "created_by_actor_id": "hero_organizer",
                "participants": ["hero_organizer"],
                "target_audience": ["arch_workers"],
                "visibility": "public",
                "risk_level": 0.2,
                "expected_effects": {"attention_boost": 0.3},
            }
        ],
    }


def canned_god_continue_payload() -> dict:
    return {
        "decision": "continue",
        "branch_delta": None,
        "marked_key_events": [],
        "tick_summary": "Status quo holds.",
        "rationale": {"main_factors": ["low_divergence"], "uncertainty": "low"},
    }


def canned_god_spawn_active_payload() -> dict:
    return {
        "decision": "spawn_active",
        "branch_delta": {
            "type": "counterfactual_event_rewrite",
            "target_event_id": "evt-1",
            "parent_version": "Pay-cut announcement",
            "child_version": "Pay-cut rescinded after audit",
        },
        "marked_key_events": [],
        "tick_summary": "Counterfactual: operator rescinds the cut.",
        "rationale": {
            "main_factors": ["high_divergence_potential"],
            "uncertainty": "medium",
        },
    }


def canned_god_kill_payload() -> dict:
    return {
        "decision": "kill",
        "branch_delta": None,
        "marked_key_events": [],
        "tick_summary": "Universe stalled — kill.",
        "rationale": {"main_factors": ["dead_end"], "uncertainty": "low"},
    }


# ---------------------------------------------------------------------------
# Mock provider response fixture (parametrised)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_provider_response():
    """Return a callable that registers a CannedProvider with the given mapping.

    Usage:
        def test_x(mock_provider_response):
            provider = mock_provider_response({
                "initialize_big_bang": canned_initializer_payload(),
                "god_agent_review": canned_god_spawn_active_payload(),
            })
            # provider.calls now records every dispatch.
    """
    from backend.app.providers import register_provider

    def _factory(
        responses: dict[str, dict | list[dict]] | None = None,
        *,
        default_response: dict | None = None,
        name: str = "openrouter",
    ) -> CannedProvider:
        provider = CannedProvider(
            responses=responses,
            default_response=default_response or canned_god_continue_payload(),
            name=name,
        )
        register_provider(name, provider)
        return provider

    return _factory


# ---------------------------------------------------------------------------
# Patch enqueue() so tests don't need a live broker
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def captured_enqueues(monkeypatch):
    """Replace `enqueue` in scheduler / branch_engine with a capture stub.

    Returns a list that records every JobEnvelope passed to enqueue.
    """
    captured: list[Any] = []

    async def _fake_enqueue(envelope, **kwargs):
        captured.append(envelope)
        return envelope.job_id

    # Patch the canonical location and any module-local rebinds.
    from backend.app.workers import scheduler as sched

    monkeypatch.setattr(sched, "enqueue", _fake_enqueue)

    yield captured


# ---------------------------------------------------------------------------
# Tiny helper: register an OpenRouter mock that supports both initializer
# and god_agent_review.
# ---------------------------------------------------------------------------


@pytest.fixture
def installed_openrouter(mock_provider_response):
    """Convenience: register a mock that handles both initializer and god."""
    return mock_provider_response(
        {
            "initialize_big_bang": canned_initializer_payload(),
            "god_agent_review": canned_god_continue_payload(),
        },
        default_response=canned_god_continue_payload(),
    )


# ---------------------------------------------------------------------------
# Initializer input builder
# ---------------------------------------------------------------------------


@pytest.fixture
def initializer_input():
    from backend.app.simulation.initializer import InitializerInput

    return InitializerInput(
        scenario_text=(
            "Bay Area gig-worker labor dispute over a pay-cut, 6-month horizon."
        ),
        display_name="E2E test scenario",
        uploaded_docs=[],
        time_horizon_label="6 months",
        tick_duration_minutes=60,
        max_ticks=10,
        max_schedule_horizon_ticks=5,
        provider_snapshot_id="provsnap-e2e",
        created_by_user_id="user-e2e",
    )


__all__ = [
    "CannedProvider",
    "canned_initializer_payload",
    "canned_god_continue_payload",
    "canned_god_spawn_active_payload",
    "canned_god_kill_payload",
]
