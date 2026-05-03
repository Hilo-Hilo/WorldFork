"""Canonical ASGI surface for WorldFork's CLI-first runtime."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.api import agent
from app.core.config import get_settings
from app.db.models import Base
from app.db.session import engine
from app.domains.actor import routes as actors
from app.domains.artifacts import routes as artifacts
from app.domains.big_bang import (
    case_studies_routes as case_studies,
    initialization_routes as initialization,
    routes as big_bangs,
    sample_routes as sample,
    scenario_bank_routes as scenario_bank,
)
from app.domains.endpoint_ledger import routes as endpoint_ledgers
from app.domains.governance import routes as god_agent
from app.domains.integrations import routes as integrations
from app.domains.jobs import routes as jobs
from app.domains.logs import routes as logs
from app.domains.multiverse import routes as multiverses
from app.domains.multiverse import workspace_routes as workspace
from app.domains.report import routes as reports
from app.domains.settings import routes as settings
from app.domains.sociology import emotion_routes as emotion_observability
from app.domains.sociology import graph_routes as graphs
from app.domains.sociology import routes as sociology
from app.domains.tick import routes as ticks
from app.domains.tick.tick_bundles import TickBundleHydrationError
from backend.app.core.db import sync_engine
from backend.app.core.redis_client import get_redis_client
from backend.app.integrations.zep import zep_status_summary
from backend.app.observability.router import router as observability_router
from backend.app.providers.openai_codex import read_codex_oauth_token

settings_obj = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    if settings_obj.auto_create_tables:
        Base.metadata.create_all(bind=engine)
        _create_runtime_control_tables()
    yield


app = FastAPI(
    title=settings_obj.app_name,
    description="Canonical WorldFork runtime surface for the CLI-first control plane.",
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


def _create_runtime_control_tables() -> None:
    """Create mutable runtime control tables that are not part of app.db yet."""

    import backend.app.models  # noqa: F401 - populate runtime control metadata
    from backend.app.models.base import Base as RuntimeControlBase

    table_names = {
        "settings_branch_policy",
        "settings_global",
        "settings_model_routing",
        "settings_provider",
        "settings_rate_limit",
        "settings_zep",
    }
    tables = [
        RuntimeControlBase.metadata.tables[name]
        for name in table_names
        if name in RuntimeControlBase.metadata.tables
    ]
    RuntimeControlBase.metadata.create_all(bind=engine, tables=tables)


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


def _llm_ready_checks() -> dict[str, bool]:
    openrouter_ready = bool(settings_obj.openrouter_api_key)
    codex_ready = bool(
        settings_obj.openai_codex_oauth_token
        or os.environ.get("OPENAI_CODEX_OAUTH_TOKEN")
        or os.environ.get("OPENAI_CODEX_ACCESS_TOKEN")
        or read_codex_oauth_token(settings_obj.openai_codex_auth_file)
    )
    provider = settings_obj.default_llm_provider
    if provider == "openai-codex":
        return {"openai-codex": codex_ready}
    if provider == "openrouter":
        return {"openrouter": openrouter_ready}
    return {provider: True}


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
        "zep": not bool(zep_status.get("degraded", False)),
        **_llm_ready_checks(),
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
app.include_router(settings.router, prefix=prefix)
app.include_router(jobs.router, prefix=prefix)
app.include_router(logs.router, prefix=prefix)
app.include_router(artifacts.router, prefix=prefix)
app.include_router(sample.router, prefix=prefix)
app.include_router(initialization.router, prefix=prefix)
app.include_router(case_studies.router, prefix=prefix)
app.include_router(scenario_bank.router, prefix=prefix)
app.include_router(observability_router)
app.include_router(integrations.router)
app.include_router(integrations.webhooks_router)


def create_app() -> FastAPI:
    """Return the canonical ASGI app."""

    return app
