from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    actors,
    agent,
    artifacts,
    big_bangs,
    case_studies,
    emotion_observability,
    god_agent,
    graphs,
    initialization,
    jobs,
    multiverses,
    sample,
    scenario_bank,
    settings,
    sociology,
    ticks,
    workspace,
)
from app.core.config import get_settings
from app.db.models import Base
from app.db.session import engine

settings_obj = get_settings()
app = FastAPI(title=settings_obj.app_name)

if settings_obj.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings_obj.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.on_event("startup")
def startup() -> None:
    if settings_obj.auto_create_tables:
        Base.metadata.create_all(bind=engine)


@app.get("/health")
def health():
    return {"status": "ok"}


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
app.include_router(ticks.router, prefix=prefix)
app.include_router(actors.router, prefix=prefix)
app.include_router(graphs.router, prefix=prefix)
app.include_router(emotion_observability.router, prefix=prefix)
app.include_router(sociology.router, prefix=prefix)
app.include_router(god_agent.router, prefix=prefix)
app.include_router(settings.router, prefix=prefix)
app.include_router(jobs.router, prefix=prefix)
app.include_router(artifacts.router, prefix=prefix)
app.include_router(sample.router, prefix=prefix)
app.include_router(initialization.router, prefix=prefix)
app.include_router(case_studies.router, prefix=prefix)
app.include_router(scenario_bank.router, prefix=prefix)
