from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[2]
FAST_MODEL_DEFAULT = "deepseek/deepseek-v4-flash"
SMART_MODEL_DEFAULT = "moonshotai/kimi-k2.6"
OPENROUTER_DEFAULT_MODEL = FAST_MODEL_DEFAULT
OPENAI_CODEX_DEFAULT_MODEL = OPENROUTER_DEFAULT_MODEL


def _backend_relative(path: str) -> Path:
    return (BACKEND_DIR / path).resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://worldfork:worldfork@localhost:5432/worldfork"
    database_url_sync: str = "postgresql+psycopg://worldfork:worldfork@localhost:5432/worldfork"
    artifact_root: Path = _backend_relative("../artifacts")
    source_of_truth_dir: Path = _backend_relative("../source_of_truth")
    run_root: Path = _backend_relative("../runs")
    environment: str = "development"
    log_level: str = "INFO"
    auto_create_tables: bool = False
    default_llm_provider: str = "openrouter"
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_chat_completions_url: str = "https://openrouter.ai/api/v1/chat/completions"
    openrouter_http_referer: str = "https://worldfork.local"
    openrouter_title: str = "WorldFork"
    openai_codex_enabled: bool = False
    openai_codex_oauth_token: str | None = None
    openai_codex_auth_file: str | None = None
    openai_codex_base_url: str = "https://chatgpt.com/backend-api/codex"
    openai_codex_default_model: str = OPENAI_CODEX_DEFAULT_MODEL
    openai_codex_fallback_model: str | None = None
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    smart_model: str = SMART_MODEL_DEFAULT
    fast_model: str = FAST_MODEL_DEFAULT
    default_model: str = FAST_MODEL_DEFAULT
    fallback_model: str = FAST_MODEL_DEFAULT
    initializer_agent_model: str = SMART_MODEL_DEFAULT
    god_agent_model: str = SMART_MODEL_DEFAULT
    cohort_agent_model: str = FAST_MODEL_DEFAULT
    hero_agent_model: str = FAST_MODEL_DEFAULT
    event_summary_model: str = FAST_MODEL_DEFAULT
    report_agent_model: str = SMART_MODEL_DEFAULT
    app_name: str = "WorldFork Backend"
    api_prefix: str = "/api"
    default_tick_duration: str = "1 day"
    default_max_ticks: int = 12
    default_max_branch_depth: int = 3
    default_max_active_multiverses: int = 12
    default_max_branches_per_tick: int = 2
    branch_score_threshold: float = 0.7
    initializer_direct_context_char_budget: int = 18000
    initializer_chunk_chars: int = 12000
    initializer_chunk_overlap_chars: int = 800
    llm_max_retries: int = 10
    llm_retry_backoff_seconds: float = 1.5
    zep_enabled: bool = False
    zep_api_key: str | None = None
    cors_origins: list[str] = Field(default_factory=list)

    @field_validator("artifact_root", "source_of_truth_dir", "run_root", mode="after")
    @classmethod
    def resolve_backend_relative_path(cls, value: Path) -> Path:
        if value.is_absolute():
            return value.resolve()
        return (BACKEND_DIR / value).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
