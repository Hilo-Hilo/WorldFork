"""Canonical ASGI surface for the LangGraph runtime rewrite.

This module is the authoritative mounted API surface for the
`revamp/langgraph-runtime-v2` branch. The canonical runtime family is the
`app.*` package plus `/api/agent/*`, `/api/jobs*`, and the queue-controlled
tick execution paths mounted below. Duplicate or older runtime surfaces such as
`/api/runs` remain transitional until they are either explicitly re-homed here
or deleted.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import (
    actors,
    agent,
    artifacts,
    big_bangs,
    case_studies,
    emotion_observability,
    endpoint_ledgers,
    god_agent,
    graphs,
    initialization,
    jobs,
    multiverses,
    reports,
    sample,
    scenario_bank,
    settings,
    sociology,
    ticks,
    workspace,
)
from backend.app.api import integrations as legacy_integrations
from backend.app.api import jobs_legacy
from backend.app.api import logs as legacy_logs
from backend.app.api import multiverse as legacy_multiverse
from backend.app.api import runs as legacy_runs
from backend.app.api import settings_legacy
from backend.app.api import universes as legacy_universes
from backend.app.api import websockets as legacy_websockets
from app.core.config import get_settings
from app.db.models import Base
from app.db.session import engine
from app.domains.tick.tick_bundles import TickBundleHydrationError
from backend.app.core.db import sync_engine
from backend.app.core.redis_client import get_redis_client
from backend.app.integrations.zep import zep_status_summary
from backend.app.observability.router import router as observability_router

settings_obj = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    if settings_obj.auto_create_tables:
        Base.metadata.create_all(bind=engine)
        _create_legacy_compat_tables()
    yield


app = FastAPI(
    title=settings_obj.app_name,
    description=(
        "Canonical WorldFork runtime surface for the LangGraph rewrite. "
        "Legacy duplicate routes such as /api/runs are transitional until "
        "removed or re-homed behind the app.* control plane."
    ),
    lifespan=lifespan,
)

if settings_obj.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings_obj.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def _create_legacy_compat_tables() -> None:
    """Create non-conflicting transitional tables used by mounted legacy routes."""

    import backend.app.models  # noqa: F401 - populate legacy model metadata
    from backend.app.models.base import Base as LegacyBase

    table_names = {
        "big_bang_runs",
        "run_results",
        "settings_branch_policy",
        "settings_global",
        "settings_model_routing",
        "settings_provider",
        "settings_rate_limit",
        "settings_zep",
    }
    tables = [LegacyBase.metadata.tables[name] for name in table_names if name in LegacyBase.metadata.tables]
    LegacyBase.metadata.create_all(bind=engine, tables=tables)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.exception_handler(TickBundleHydrationError)
def tick_bundle_hydration_error(_request, exc: TickBundleHydrationError) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"detail": f"tick bundle hydration failed: {exc}"},
    )


@app.get("/healthz")
def healthz() -> dict[str, bool]:
    return {"ok": True}


@app.get("/readyz")
async def readyz() -> JSONResponse:
    async def check_database() -> bool:
        def ping_database() -> None:
            with sync_engine.connect() as conn:
                conn.execute(text("SELECT 1"))

        try:
            await asyncio.to_thread(ping_database)
            return True
        except Exception:
            return False

    async def check_redis() -> bool:
        try:
            redis = get_redis_client()
            return bool(await redis.ping())
        except Exception:
            return False

    zep_status = await zep_status_summary()
    database_result, redis_result = await asyncio.gather(
        check_database(),
        asyncio.wait_for(check_redis(), timeout=2.0),
        return_exceptions=True,
    )
    database_ok = database_result is True
    redis_ok = redis_result is True
    checks = {
        "database": database_ok,
        "redis": redis_ok,
        "openrouter": bool(settings_obj.openrouter_api_key),
        "zep": not bool(zep_status.get("degraded", False)),
    }
    ok = all(checks.values())
    status_code = 200 if ok else 503
    return JSONResponse(status_code=status_code, content={"ok": ok, "checks": checks})


@app.get("/")
def root():
    return {
        "name": settings_obj.app_name,
        "status": "ok",
        "interface": "cli-first",
        "agent_status": f"{settings_obj.api_prefix}/agent/status",
        "agent_discover": f"{settings_obj.api_prefix}/agent/discover",
    }


prefix = settings_obj.api_prefix
app.include_router(agent.router, prefix=prefix)
app.include_router(big_bangs.router, prefix=prefix)
app.include_router(workspace.router, prefix=prefix)
app.include_router(multiverses.router, prefix=prefix)
app.include_router(reports.router, prefix=prefix)
app.include_router(ticks.router, prefix=prefix)
app.include_router(actors.router, prefix=prefix)
app.include_router(graphs.router, prefix=prefix)
app.include_router(endpoint_ledgers.router, prefix=prefix)
app.include_router(emotion_observability.router, prefix=prefix)
app.include_router(sociology.router, prefix=prefix)
app.include_router(god_agent.router, prefix=prefix)
# Legacy /api/settings and /api/jobs compatibility surfaces need to win route
# precedence during the runtime rewrite because large parts of the backend test
# suite still exercise the async DB-backed control-plane contracts.
app.include_router(settings_legacy.router)
app.include_router(jobs_legacy.router)
app.include_router(settings.router, prefix=prefix)
app.include_router(jobs.router, prefix=prefix)
app.include_router(artifacts.router, prefix=prefix)
app.include_router(sample.router, prefix=prefix)
app.include_router(initialization.router, prefix=prefix)
app.include_router(case_studies.router, prefix=prefix)
app.include_router(scenario_bank.router, prefix=prefix)
app.include_router(observability_router)

# Transitional compatibility routers retained while the runtime rewrite
# converges. These paths are exercised by the current backend test suite and
# remain intentionally mounted until their replacements are fully re-homed.
app.include_router(legacy_runs.router)
app.include_router(legacy_universes.router)
app.include_router(legacy_multiverse.router)
app.include_router(legacy_logs.router)
app.include_router(legacy_integrations.router)
app.include_router(legacy_integrations.webhooks_router)
app.include_router(legacy_websockets.router)


def create_app() -> FastAPI:
    """Return the canonical ASGI app, including transitional compatibility routes."""

    return app
