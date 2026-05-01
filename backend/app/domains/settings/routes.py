from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
def settings():
    s = get_settings()
    return {
        "app_name": s.app_name,
        "default_llm_provider": s.default_llm_provider,
        "openrouter_base_url": s.openrouter_base_url,
        "openrouter_chat_completions_url": s.openrouter_chat_completions_url,
        "default_model": s.default_model,
        "agent_models": {
            "initializer_agent": s.initializer_agent_model,
            "god_agent": s.god_agent_model,
            "cohort_agent": s.cohort_agent_model,
            "hero_agent": s.hero_agent_model,
            "event_summary": s.event_summary_model,
            "report_agent": s.report_agent_model,
        },
        "artifact_root": str(s.artifact_root),
        "source_of_truth_dir": str(s.source_of_truth_dir),
    }
