from __future__ import annotations

from types import SimpleNamespace

import pytest

import backend.app.providers.openai_codex as openai_codex
from backend.app.providers.openai_codex import OpenAICodexProvider, read_codex_oauth_token
from backend.app.schemas.llm import ModelConfig, PromptPacket


CODEX_MODEL = "gpt-5.4"


class _Responses:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _response(content: str):
    return SimpleNamespace(
        id="resp-test",
        model=CODEX_MODEL,
        output_text=content,
        usage=SimpleNamespace(input_tokens=7, output_tokens=5, total_tokens=12),
        status="completed",
    )


def _provider_with_responses(*contents):
    responses = _Responses([
        item if isinstance(item, Exception) else _response(item) for item in contents
    ])
    provider = OpenAICodexProvider(oauth_token="test-oauth-token")
    provider._client = SimpleNamespace(responses=responses)
    provider._client_token = "test-oauth-token"
    return provider, responses


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
        provider="openai-codex",
        model=CODEX_MODEL,
        temperature=0.2,
        top_p=1.0,
        max_tokens=128,
        timeout_seconds=30,
        retry_policy="linear",
    )


@pytest.mark.asyncio
async def test_generate_structured_uses_responses_json_format(
    prompt: PromptPacket, config: ModelConfig
) -> None:
    provider, responses = _provider_with_responses('{"ok": true}')

    result = await provider.generate_structured(prompt, config)

    assert result.provider == "openai-codex"
    assert result.parsed_json == {"ok": True}
    assert result.prompt_tokens == 7
    call = responses.calls[0]
    assert call["model"] == CODEX_MODEL
    assert call["text"] == {"format": {"type": "json_object"}}
    assert call["input"][0]["content"][0]["type"] == "input_text"
    assert "Return exactly one valid JSON object" in call["instructions"]


@pytest.mark.asyncio
async def test_generate_structured_retries_without_text_format_when_rejected(
    monkeypatch: pytest.MonkeyPatch, prompt: PromptPacket, config: ModelConfig
) -> None:
    class _FakeAPIStatusError(Exception):
        status_code = 400

    monkeypatch.setattr(openai_codex, "APIStatusError", _FakeAPIStatusError)
    provider, responses = _provider_with_responses(
        _FakeAPIStatusError("bad format"), '{"ok": true}'
    )

    result = await provider.generate_structured(prompt, config)

    assert result.parsed_json == {"ok": True}
    assert "text" in responses.calls[0]
    assert "text" not in responses.calls[1]


@pytest.mark.asyncio
async def test_generate_structured_repairs_malformed_json_locally_before_llm_retry(
    prompt: PromptPacket, config: ModelConfig
) -> None:
    provider, responses = _provider_with_responses('{"ok": true, "note": "unterminated')

    result = await provider.generate_structured(prompt, config)

    assert result.parsed_json == {"ok": True, "note": "unterminated"}
    assert result.repaired_once is True
    assert len(responses.calls) == 1


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
    provider, responses = _provider_with_responses(
        '{"note": "unterminated',
        '{"ok": true}',
    )

    result = await provider.generate_structured(prompt, config)

    assert result.parsed_json == {"ok": True}
    assert result.repaired_once is True
    assert len(responses.calls) == 2


def test_reads_codex_oauth_token(tmp_path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        '{"auth_mode":"chatgpt","tokens":{"access_token":"oauth-access","refresh_token":"refresh"}}'
    )

    assert read_codex_oauth_token(auth_file) == "oauth-access"


def test_reads_worldfork_auth_without_codex_cli(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    worldfork_home = tmp_path / "worldfork"
    worldfork_home.mkdir()
    (worldfork_home / "openai-codex-auth.json").write_text(
        '{"tokens":{"access_token":"worldfork-access","refresh_token":"refresh"}}'
    )
    monkeypatch.setenv("WORLDFORK_HOME", str(worldfork_home))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "missing-codex-home"))
    monkeypatch.delenv(openai_codex.OPENAI_CODEX_AUTH_FILE_ENV, raising=False)

    assert read_codex_oauth_token() == "worldfork-access"
