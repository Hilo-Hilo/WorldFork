from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.providers.errors import InvalidJSONError
from backend.app.providers.openrouter import OpenRouterProvider
from backend.app.providers.routing import RoutingTable
from backend.app.core.config import settings
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


@pytest.mark.asyncio
async def test_generate_structured_repairs_malformed_json_locally_before_llm_retry(
    prompt: PromptPacket, config: ModelConfig
) -> None:
    provider, completions = _provider_with_responses('{"ok": true, "note": "unterminated')

    result = await provider.generate_structured(prompt, config)

    assert result.parsed_json == {"ok": True, "note": "unterminated"}
    assert result.repaired_once is True
    assert len(completions.calls) == 1


@pytest.mark.asyncio
async def test_generate_structured_regenerates_when_local_repair_fails_schema(
    prompt: PromptPacket, config: ModelConfig
) -> None:
    config.response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "test_response",
            "schema": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
            },
        },
    }
    provider, completions = _provider_with_responses(
        '{"note": "unterminated',
        '{"ok": true}',
    )

    result = await provider.generate_structured(prompt, config)

    assert result.parsed_json == {"ok": True}
    assert result.repaired_once is True
    assert len(completions.calls) == 2


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

    expected_model_by_job_type = {
        "initialize_big_bang": settings.initializer_agent_model,
        "god_agent_review": settings.god_agent_model,
        "aggregate_run_results": settings.report_agent_model,
        "evaluate_endpoint_ledger": settings.god_agent_model,
        "force_deviation": settings.god_agent_model,
    }
    powerful_job_types = set(expected_model_by_job_type)
    for job_type in (
        "initialize_big_bang",
        "simulate_universe_tick",
        "actor_deliberation_call",
        "execute_due_events",
        "social_propagation",
        "sociology_update",
        "god_agent_review",
        "branch_universe",
        "build_review_index",
        "export_run",
        "apply_tick_results",
        "aggregate_run_results",
        "evaluate_endpoint_ledger",
        "force_deviation",
    ):
        expected_model = expected_model_by_job_type.get(job_type, OPENROUTER_MODEL)
        expected_provider = "openai-codex" if job_type in powerful_job_types else "openrouter"
        preferred, fallback = routing.route(job_type)
        assert preferred.provider == expected_provider
        assert preferred.model == expected_model
        assert fallback is not None
        assert fallback.provider == expected_provider
        assert fallback.model == expected_model
        assert preferred.fallback_model == expected_model


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


def test_openrouter_wraps_raw_response_schema_for_strict_mode(prompt: PromptPacket) -> None:
    provider = OpenRouterProvider.__new__(OpenRouterProvider)
    raw_schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }
    config = ModelConfig(
        provider="openrouter",
        model=OPENROUTER_MODEL,
        temperature=0.2,
        top_p=1.0,
        max_tokens=128,
        timeout_seconds=30,
        retry_policy="linear",
        response_format=raw_schema,
    )

    kwargs = provider._wrap_call_kwargs(
        config=config,
        messages=[{"role": "system", "content": prompt.system}],
        response_format=provider._select_response_format(config),
    )

    assert kwargs["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "worldfork_response",
            "strict": True,
            "schema": raw_schema,
        },
    }


def test_openrouter_supports_provider_and_response_format_overrides(prompt: PromptPacket) -> None:
    provider = OpenRouterProvider.__new__(OpenRouterProvider)
    config = ModelConfig(
        provider="openrouter",
        model=OPENROUTER_MODEL,
        temperature=0.2,
        top_p=1.0,
        max_tokens=128,
        timeout_seconds=30,
        retry_policy="linear",
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "ignored_when_json_object_forced",
                "schema": {"type": "object", "properties": {}},
            },
            "openrouter": {
                "response_format": "json_object",
                "provider": {"allow_fallbacks": False},
            },
        },
    )

    kwargs = provider._wrap_call_kwargs(
        config=config,
        messages=[{"role": "system", "content": prompt.system}],
        response_format=provider._select_response_format(config),
    )

    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["extra_body"] == {"provider": {"allow_fallbacks": False}}


def test_seeded_routes_derive_from_settings_provider_defaults() -> None:
    from backend.app.scripts.seed import _ROUTING_DEFAULTS, _routing_model_defaults

    assert _ROUTING_DEFAULTS
    expected_model_by_job_type = {
        "initialize_big_bang": settings.initializer_agent_model,
        "initializer_chunk_extractor": settings.initializer_agent_model,
        "initializer_agent": settings.initializer_agent_model,
        "god_agent_review": settings.god_agent_model,
        "god_agent": settings.god_agent_model,
        "endpoint_ledger": settings.god_agent_model,
        "evaluate_endpoint_ledger": settings.god_agent_model,
        "aggregate_run_results": settings.report_agent_model,
        "report_agent": settings.report_agent_model,
        "event_summary": settings.event_summary_model,
        "force_deviation": settings.god_agent_model,
    }
    for row in _ROUTING_DEFAULTS:
        routed = _routing_model_defaults(row)
        expected_model = expected_model_by_job_type.get(row["job_type"], OPENROUTER_MODEL)
        expected_provider = (
            "openai-codex"
            if expected_model == settings.openai_codex_default_model
            else settings.default_llm_provider
        )
        assert routed["preferred_provider"] == expected_provider
        assert routed["preferred_model"] == expected_model
        assert routed["fallback_provider"] == expected_provider
        assert routed["fallback_model"] == expected_model


def test_seed_routing_preserves_existing_rows(monkeypatch) -> None:
    from backend.app.scripts import seed

    calls = []

    def insert_missing(session, model, pk_col, rows):
        calls.append((session, model, pk_col, rows))
        return len(rows)

    def fail_upsert(*_args, **_kwargs):
        pytest.fail("_seed_routing should not overwrite existing model-routing rows")

    monkeypatch.setattr(seed, "_insert_missing", insert_missing, raising=False)
    monkeypatch.setattr(seed, "_upsert", fail_upsert)

    session = object()
    seed._seed_routing(session)

    assert calls
    assert calls[0][0] is session
    assert calls[0][2] == "job_type"
