from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.providers.errors import InvalidJSONError
from backend.app.providers.openrouter import OpenRouterProvider
from backend.app.providers.routing import RoutingTable
from backend.app.schemas.settings import ModelRoutingEntry
from backend.app.schemas.llm import ModelConfig, PromptPacket


OPENROUTER_MODEL = "deepseek/deepseek-v4-flash"


class _Completions:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def _response(content: str):
    return SimpleNamespace(
        id="chatcmpl-test",
        model=OPENROUTER_MODEL,
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=[]),
            )
        ],
        usage=None,
    )


def _provider_with_responses(*contents: str) -> tuple[OpenRouterProvider, _Completions]:
    completions = _Completions([_response(content) for content in contents])
    provider = OpenRouterProvider.__new__(OpenRouterProvider)
    provider._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    return provider, completions


@pytest.fixture
def prompt() -> PromptPacket:
    return PromptPacket(
        system="Return JSON.",
        clock={
            "current_tick": 0,
            "tick_duration_minutes": 120,
            "elapsed_minutes": 0,
            "max_schedule_horizon_ticks": 5,
        },
        actor_id="coh-1",
        actor_kind="cohort",
        output_schema_id="test_schema",
        temperature=0.2,
    )


@pytest.fixture
def config() -> ModelConfig:
    return ModelConfig(
        provider="openrouter",
        model=OPENROUTER_MODEL,
        temperature=0.2,
        top_p=1.0,
        max_tokens=128,
        timeout_seconds=30,
        retry_policy="linear",
    )


@pytest.mark.asyncio
async def test_generate_structured_repairs_non_object_json(
    prompt: PromptPacket, config: ModelConfig
) -> None:
    provider, completions = _provider_with_responses("[]", '{"ok": true}')

    result = await provider.generate_structured(prompt, config)

    assert result.parsed_json == {"ok": True}
    assert result.repaired_once is True
    assert len(completions.calls) == 2
    assert completions.calls[1]["response_format"] == {"type": "json_object"}
    assert "single valid JSON object" in completions.calls[1]["messages"][-1]["content"]


@pytest.mark.parametrize("final_content", ["[]", '"text"', "123"])
@pytest.mark.asyncio
async def test_generate_structured_raises_invalid_json_for_final_non_object_json(
    prompt: PromptPacket, config: ModelConfig, final_content: str
) -> None:
    provider, _completions = _provider_with_responses("[]", final_content)

    with pytest.raises(InvalidJSONError) as exc_info:
        await provider.generate_structured(prompt, config)

    assert exc_info.value.raw_text == final_content
    assert "root must be an object" in (exc_info.value.validator_message or "")


def test_default_routes_use_provider_model_split() -> None:
    routing = RoutingTable.defaults()

    for job_type in (
        "simulate_universe_tick",
        "actor_deliberation_call",
        "execute_due_events",
        "social_propagation",
        "sociology_update",
        "branch_universe",
        "sync_zep_memory",
        "build_review_index",
        "export_run",
        "apply_tick_results",
    ):
        preferred, fallback = routing.route(job_type)
        assert preferred.provider == "openrouter"
        assert preferred.model == OPENROUTER_MODEL
        assert fallback is not None
        assert fallback.provider == "openrouter"
        assert fallback.model == OPENROUTER_MODEL
        assert preferred.fallback_model == OPENROUTER_MODEL

    for job_type in (
        "initialize_big_bang",
        "god_agent_review",
        "aggregate_run_results",
        "evaluate_endpoint_ledger",
        "force_deviation",
    ):
        preferred, fallback = routing.route(job_type)
        assert preferred.provider == "openai-codex"
        assert preferred.model == "gpt-5.4"
        assert fallback is not None
        assert fallback.provider == "openai-codex"
        assert fallback.model == "gpt-5.4"
        assert preferred.fallback_model == "gpt-5.4"


def test_same_provider_fallback_is_openrouter_native_model_hint() -> None:
    routing = RoutingTable(
        {
            "actor_deliberation_call": ModelRoutingEntry(
                job_type="actor_deliberation_call",
                preferred_provider="openrouter",
                preferred_model="primary/model",
                fallback_provider="openrouter",
                fallback_model="fallback/model",
                temperature=0.5,
                top_p=0.95,
                max_tokens=512,
                max_concurrency=4,
                requests_per_minute=60,
                tokens_per_minute=150_000,
                timeout_seconds=120,
                retry_policy="exponential_backoff",
                daily_budget_usd=None,
            )
        }
    )

    preferred, fallback = routing.route("actor_deliberation_call")

    assert preferred.model == "primary/model"
    assert preferred.fallback_model == "fallback/model"
    assert fallback is not None
    assert fallback.model == "fallback/model"


def test_openrouter_extra_body_uses_native_fallback_model(prompt: PromptPacket) -> None:
    provider = OpenRouterProvider.__new__(OpenRouterProvider)
    config = ModelConfig(
        provider="openrouter",
        model="primary/model",
        fallback_model="fallback/model",
        temperature=0.2,
        top_p=1.0,
        max_tokens=128,
        timeout_seconds=30,
        retry_policy="linear",
    )

    kwargs = provider._wrap_call_kwargs(
        config=config,
        messages=[{"role": "system", "content": prompt.system}],
        response_format={"type": "json_object"},
    )

    assert kwargs["extra_body"] == {"models": ["primary/model", "fallback/model"]}


def test_seeded_routes_use_openrouter_gemini_model() -> None:
    from backend.app.scripts.seed import _ROUTING_DEFAULTS

    assert _ROUTING_DEFAULTS
    for row in _ROUTING_DEFAULTS:
        assert row["preferred_provider"] == "openrouter"
        assert row["preferred_model"] == OPENROUTER_MODEL
        assert row["fallback_provider"] == "openrouter"
        assert row["fallback_model"] == OPENROUTER_MODEL
