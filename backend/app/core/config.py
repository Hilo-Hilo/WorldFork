from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[2]
OPENROUTER_DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
OPENAI_CODEX_DEFAULT_MODEL = "gpt-5.4"


def _backend_relative(path: str) -> Path:
    return (BACKEND_DIR / path).resolve()


def _is_sqlite_database_url(database_url: str) -> bool:
    return database_url.startswith(("sqlite:", "sqlite+"))


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
    openrouter_prompt_caching_enabled: bool = True
    openai_codex_enabled: bool = True
    openai_codex_oauth_token: str | None = None
    openai_codex_auth_file: str | None = None
    openai_codex_base_url: str = "https://chatgpt.com/backend-api/codex"
    openai_codex_default_model: str = OPENAI_CODEX_DEFAULT_MODEL
    openai_codex_fallback_model: str | None = None
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    celery_task_time_limit: int = Field(default=3600, ge=60, le=86_400)
    celery_task_soft_time_limit: int = Field(default=3300, ge=60, le=86_400)
    default_model: str = OPENROUTER_DEFAULT_MODEL
    fallback_model: str = OPENROUTER_DEFAULT_MODEL
    initializer_agent_model: str = OPENAI_CODEX_DEFAULT_MODEL
    god_agent_model: str = OPENAI_CODEX_DEFAULT_MODEL
    cohort_agent_model: str = OPENROUTER_DEFAULT_MODEL
    hero_agent_model: str = OPENROUTER_DEFAULT_MODEL
    event_summary_model: str = OPENAI_CODEX_DEFAULT_MODEL
    report_agent_model: str = OPENAI_CODEX_DEFAULT_MODEL
    app_name: str = "WorldFork Backend"
    api_prefix: str = "/api"
    default_tick_duration: str = "1 day"
    default_max_ticks: int = 12
    default_max_branch_depth: int = 3
    default_max_active_multiverses: int = 12
    default_max_branches_per_tick: int = 2
    max_parallel_cohort_decisions: int = Field(default=16, ge=1, le=64)
    sqlalchemy_sync_pool_size: int = Field(default=2, ge=1, le=100)
    sqlalchemy_sync_max_overflow: int = Field(default=4, ge=0, le=200)
    sqlalchemy_sync_pool_timeout: float = Field(default=30, ge=0.1, le=600)
    sqlalchemy_sync_pool_recycle: int = Field(default=1800, ge=-1, le=86_400)
    sqlalchemy_async_pool_size: int = Field(default=2, ge=1, le=100)
    sqlalchemy_async_max_overflow: int = Field(default=4, ge=0, le=200)
    sqlalchemy_async_pool_timeout: float = Field(default=30, ge=0.1, le=600)
    sqlalchemy_async_pool_recycle: int = Field(default=1800, ge=-1, le=86_400)
    prompt_event_queue_max_chars: int = Field(default=10_000, ge=4_000, le=200_000)
    prompt_agent_context_max_chars: int = Field(default=18_000, ge=8_000, le=200_000)
    prompt_god_bundle_max_chars: int = Field(default=28_000, ge=8_000, le=400_000)
    prompt_event_summary_max_chars: int = Field(default=8_000, ge=4_000, le=120_000)
    event_summary_max_tokens: int = Field(default=600, ge=300, le=4_000)
    branch_score_threshold: float = 0.7
    initializer_direct_context_char_budget: int = 64_000 * 4
    initializer_chunk_chars: int = 64_000 * 4
    initializer_chunk_overlap_chars: int = 2_048 * 4
    llm_max_retries: int = 10
    llm_retry_backoff_seconds: float = 1.5
    cors_origins: list[str] = Field(default_factory=list)

    @field_validator("artifact_root", "source_of_truth_dir", "run_root", mode="after")
    @classmethod
    def resolve_backend_relative_path(cls, value: Path) -> Path:
        if value.is_absolute():
            return value.resolve()
        return (BACKEND_DIR / value).resolve()

    def sync_database_pool_kwargs(self, database_url: str | None = None) -> dict[str, int | float]:
        url = database_url or self.database_url_sync
        if _is_sqlite_database_url(url):
            return {}
        return {
            "pool_size": self.sqlalchemy_sync_pool_size,
            "max_overflow": self.sqlalchemy_sync_max_overflow,
            "pool_timeout": self.sqlalchemy_sync_pool_timeout,
            "pool_recycle": self.sqlalchemy_sync_pool_recycle,
        }

    def async_database_pool_kwargs(self, database_url: str | None = None) -> dict[str, int | float]:
        url = database_url or self.database_url
        if _is_sqlite_database_url(url):
            return {}
        return {
            "pool_size": self.sqlalchemy_async_pool_size,
            "max_overflow": self.sqlalchemy_async_max_overflow,
            "pool_timeout": self.sqlalchemy_async_pool_timeout,
            "pool_recycle": self.sqlalchemy_async_pool_recycle,
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
