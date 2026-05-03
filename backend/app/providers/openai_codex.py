"""OpenAI Codex OAuth provider.

This adapter owns the Codex Responses API call shape, OAuth token lookup, JSON
format request translation, response text extraction, and one-shot JSON repair.
The rest of the runtime should only see the provider protocol and ``LLMResult``.
"""
from __future__ import annotations

import base64
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from types import SimpleNamespace
from typing import Any

import httpx

try:
    from openai import (  # type: ignore[import-untyped]
        APIConnectionError,
        APIStatusError,
        APITimeoutError,
        AsyncOpenAI,  # type: ignore[import-untyped]
    )
    from openai import RateLimitError as OpenAIRateLimitError  # type: ignore[import-untyped]
except Exception:  # pragma: no cover - keeps tests importable without openai
    AsyncOpenAI = None  # type: ignore[assignment]
    APIConnectionError = APIStatusError = APITimeoutError = Exception  # type: ignore[assignment]
    OpenAIRateLimitError = Exception  # type: ignore[assignment]

from backend.app.providers.base import BaseProvider
from backend.app.providers.errors import (
    InvalidJSONError,
    ProviderError,
    ProviderTimeoutError,
    RateLimitError,
)
from backend.app.schemas.llm import (
    EmbeddingConfig,
    EmbeddingResult,
    LLMResult,
    ModelConfig,
    PromptPacket,
    ProviderHealth,
)


OPENAI_CODEX_RESPONSES_BASE_URL = "https://chatgpt.com/backend-api/codex"
OPENAI_CODEX_OAUTH_ENV = "OPENAI_CODEX_OAUTH_TOKEN"
OPENAI_CODEX_ACCESS_ENV = "OPENAI_CODEX_ACCESS_TOKEN"
OPENAI_CODEX_AUTH_FILE_ENV = "OPENAI_CODEX_AUTH_FILE"


def _default_worldfork_auth_file() -> Path:
    root = os.environ.get("WORLDFORK_HOME") or os.environ.get("WORLD_FORK_HOME")
    if root:
        return Path(root).expanduser() / "openai-codex-auth.json"
    return Path.home() / ".worldfork" / "openai-codex-auth.json"


def _default_codex_cli_auth_file() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home).expanduser() / "auth.json"
    return Path.home() / ".codex" / "auth.json"


def _candidate_auth_files(auth_file: str | Path | None = None) -> list[Path]:
    if auth_file:
        return [Path(auth_file).expanduser()]
    env_file = os.environ.get(OPENAI_CODEX_AUTH_FILE_ENV)
    if env_file:
        return [Path(env_file).expanduser()]
    return [_default_worldfork_auth_file(), _default_codex_cli_auth_file()]


def _trimmed_string(value: Any) -> str | None:
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None
    return None


def decode_jwt_expiry_seconds(token: str) -> int | None:
    """Return the JWT ``exp`` claim without validating the signature."""
    parts = token.split(".")
    if len(parts) < 2:
        return None
    padded = parts[1] + ("=" * (-len(parts[1]) % 4))
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except Exception:
        return None
    exp = payload.get("exp")
    return int(exp) if isinstance(exp, int) else None


def read_codex_oauth_token(auth_file: str | Path | None = None) -> str | None:
    """Read an OpenAI Codex OAuth access token from WorldFork or Codex CLI auth."""
    for path in _candidate_auth_files(auth_file):
        token = _read_oauth_token_from_file(path)
        if token:
            return token
    return None


def read_codex_cli_oauth_token(auth_file: str | Path | None = None) -> str | None:
    """Backward-compatible alias for Codex OAuth auth-file reads."""
    return read_codex_oauth_token(auth_file)


def _read_oauth_token_from_file(path: Path) -> str | None:
    try:
        data = json.loads(path.read_text())
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    tokens = data.get("tokens")
    if not isinstance(tokens, dict):
        return None
    return _trimmed_string(tokens.get("access_token"))


def _responses_schema_name(value: Any) -> str:
    raw = _trimmed_string(value) or "worldfork_response"
    normalized = re.sub(r"[^A-Za-z0-9_-]", "_", raw).strip("_")
    return (normalized or "worldfork_response")[:64]


class OpenAICodexProvider(BaseProvider):
    """Provider for the ChatGPT Codex Responses endpoint using OAuth."""

    name = "openai-codex"

    def __init__(
        self,
        *,
        oauth_token: str | None = None,
        oauth_token_env: str = OPENAI_CODEX_OAUTH_ENV,
        codex_auth_file: str | Path | None = None,
        base_url: str = OPENAI_CODEX_RESPONSES_BASE_URL,
        default_model: str = "deepseek/deepseek-v4-flash",
        fallback_model: str | None = None,
        request_timeout: float = 120.0,
    ) -> None:
        if AsyncOpenAI is None:  # pragma: no cover
            raise ImportError(
                "openai>=1.51 is required for OpenAICodexProvider - "
                "install with `pip install openai>=1.51`."
            )
        self._oauth_token = _trimmed_string(oauth_token)
        self._oauth_token_env = oauth_token_env
        self._codex_auth_file = Path(codex_auth_file).expanduser() if codex_auth_file else None
        self._base_url = base_url
        self.default_model = default_model
        self.fallback_model = fallback_model
        self._request_timeout = request_timeout
        self._client: Any | None = None
        self._client_token: str | None = None

    # ------------------------------------------------------------------
    # OAuth
    # ------------------------------------------------------------------

    def _resolve_oauth_token_with_source(self) -> tuple[str | None, str]:
        if self._oauth_token:
            return self._oauth_token, "constructor"
        env_value = os.environ.get(self._oauth_token_env)
        if _trimmed_string(env_value):
            return env_value.strip(), self._oauth_token_env
        fallback_env_value = os.environ.get(OPENAI_CODEX_ACCESS_ENV)
        if _trimmed_string(fallback_env_value):
            return fallback_env_value.strip(), OPENAI_CODEX_ACCESS_ENV
        for auth_file in _candidate_auth_files(self._codex_auth_file):
            token = _read_oauth_token_from_file(auth_file)
            if token:
                return token, str(auth_file)
        return None, ", ".join(str(path) for path in _candidate_auth_files(self._codex_auth_file))

    def has_oauth_token(self) -> bool:
        token, _source = self._resolve_oauth_token_with_source()
        return bool(token)

    def _client_for_token(self) -> Any:
        token, _source = self._resolve_oauth_token_with_source()
        if not token:
            raise ProviderError(
                "openai-codex OAuth token not found; run "
                "`worldfork settings openai-codex-login`, run `codex login --device-auth`, "
                f"or set {self._oauth_token_env}."
            )
        if self._client is None or self._client_token != token:
            self._client = AsyncOpenAI(
                base_url=self._base_url,
                api_key=token,
                timeout=httpx.Timeout(self._request_timeout),
            )
            self._client_token = token
        return self._client

    # ------------------------------------------------------------------
    # Request / response helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _prompt_body(prompt: PromptPacket) -> str:
        return json.dumps(
            prompt.model_dump(mode="json", exclude={"system"}),
            indent=2,
            sort_keys=True,
            default=str,
        )

    @classmethod
    def _build_input(cls, prompt: PromptPacket) -> list[dict[str, Any]]:
        return [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": cls._prompt_body(prompt),
                    }
                ],
            }
        ]

    @staticmethod
    def _json_instructions(system_prompt: str) -> str:
        return (
            f"{system_prompt}\n\n"
            "Return exactly one valid JSON object. Do not wrap it in Markdown. "
            "Do not include commentary outside the JSON object."
        )

    @staticmethod
    def _responses_text_config(config: ModelConfig) -> dict[str, Any] | None:
        response_format = config.response_format or {"type": "json_object"}
        if not isinstance(response_format, dict):
            return {"format": {"type": "json_object"}}

        format_type = response_format.get("type")
        if format_type == "json_object":
            return {"format": {"type": "json_object"}}
        if format_type != "json_schema":
            return {"format": {"type": "json_object"}}

        schema_config = response_format.get("json_schema")
        if isinstance(schema_config, dict):
            schema = schema_config.get("schema")
            name = schema_config.get("name") or "worldfork_response"
            description = schema_config.get("description")
            strict = schema_config.get("strict")
        else:
            schema = response_format.get("schema")
            name = response_format.get("name") or "worldfork_response"
            description = response_format.get("description")
            strict = response_format.get("strict")

        if not isinstance(schema, dict):
            return {"format": {"type": "json_object"}}

        fmt: dict[str, Any] = {
            "type": "json_schema",
            "name": _responses_schema_name(name),
            "schema": schema,
        }
        if isinstance(description, str) and description.strip():
            fmt["description"] = description.strip()
        if isinstance(strict, bool):
            fmt["strict"] = strict
        return {"format": fmt}

    def _wrap_call_kwargs(
        self,
        *,
        prompt: PromptPacket,
        config: ModelConfig,
        structured: bool,
        repair_text: str | None = None,
        validator_message: str | None = None,
    ) -> dict[str, Any]:
        instructions = prompt.system
        input_payload = self._build_input(prompt)
        if structured:
            instructions = self._json_instructions(prompt.system)
        if repair_text is not None:
            instructions = self._json_instructions(prompt.system)
            input_payload = input_payload + [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "Previous invalid response:\n"
                                f"{repair_text}\n\n"
                                "Your prior response failed JSON validation: "
                                f"{validator_message or 'invalid JSON'}. Re-emit a single "
                                "valid JSON object only, no commentary."
                            ),
                        }
                    ],
                },
            ]

        kwargs: dict[str, Any] = {
            "model": config.model,
            "instructions": instructions,
            "input": input_payload,
            "store": False,
            "stream": True,
        }
        if structured:
            kwargs["text"] = self._responses_text_config(config)
        if config.tools:
            kwargs["tools"] = config.tools
        return kwargs

    @staticmethod
    def _parse_json_object(content: str) -> dict[str, Any]:
        if not content:
            raise ValueError("response was empty")
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise ValueError(
                f"response JSON root must be an object, got {type(parsed).__name__}"
            )
        return parsed

    @staticmethod
    def _get_field(value: Any, field: str) -> Any:
        if isinstance(value, dict):
            return value.get(field)
        return getattr(value, field, None)

    @classmethod
    def _extract_output_text(cls, response: Any) -> str:
        direct = cls._get_field(response, "output_text")
        if isinstance(direct, str) and direct:
            return direct

        chunks: list[str] = []
        output = cls._get_field(response, "output") or []
        for item in output:
            item_text = cls._get_field(item, "text")
            if isinstance(item_text, str):
                chunks.append(item_text)
            content = cls._get_field(item, "content") or []
            for part in content:
                text = cls._get_field(part, "text")
                if isinstance(text, str):
                    chunks.append(text)
        return "".join(chunks)

    @staticmethod
    def _extract_usage(response: Any) -> tuple[int, int, int, float | None]:
        usage = getattr(response, "usage", None)
        if usage is None and isinstance(response, dict):
            usage = response.get("usage")
        if usage is None:
            return 0, 0, 0, None
        get = usage.get if isinstance(usage, dict) else lambda key, default=None: getattr(usage, key, default)
        prompt_tokens = int(get("input_tokens", get("prompt_tokens", 0)) or 0)
        completion_tokens = int(get("output_tokens", get("completion_tokens", 0)) or 0)
        total_tokens = int(get("total_tokens", prompt_tokens + completion_tokens) or 0)
        return prompt_tokens, completion_tokens, total_tokens, None

    @staticmethod
    def _raw_response_summary(response: Any) -> dict[str, Any]:
        return {
            "id": getattr(response, "id", None)
            if not isinstance(response, dict)
            else response.get("id"),
            "status": getattr(response, "status", None)
            if not isinstance(response, dict)
            else response.get("status"),
        }

    @staticmethod
    def _map_openai_error(exc: Exception) -> ProviderError:
        if isinstance(exc, OpenAIRateLimitError):
            retry_after = None
            resp = getattr(exc, "response", None)
            if resp is not None:
                hdr = getattr(resp, "headers", {}) or {}
                ra = hdr.get("retry-after") or hdr.get("Retry-After")
                if ra is not None:
                    try:
                        retry_after = float(ra)
                    except (TypeError, ValueError):
                        retry_after = None
            return RateLimitError(str(exc), retry_after=retry_after)
        if isinstance(exc, APITimeoutError):
            return ProviderTimeoutError(str(exc))
        if isinstance(exc, APIStatusError):
            status = getattr(exc, "status_code", None)
            if status == 429:
                return RateLimitError(str(exc))
            return ProviderError(f"openai-codex status_error[{status}]: {exc}")
        if isinstance(exc, APIConnectionError):
            return ProviderError(f"openai-codex connection_error: {exc}")
        return ProviderError(f"openai-codex error: {exc}")

    @staticmethod
    def _is_recoverable_param_error(exc: Exception) -> bool:
        return isinstance(exc, APIStatusError) and getattr(exc, "status_code", 0) in (400, 422)

    async def _create_response(self, kwargs: dict[str, Any]) -> Any:
        client = self._client_for_token()
        attempts: list[dict[str, Any]] = [kwargs]
        if "text" in kwargs:
            without_text = dict(kwargs)
            without_text.pop("text", None)
            attempts.append(without_text)

        last_exc: Exception | None = None
        for index, attempt in enumerate(attempts):
            try:
                response = await client.responses.create(**attempt)
                if attempt.get("stream") is True and hasattr(response, "__aiter__"):
                    return await self._collect_response_stream(response)
                return response
            except Exception as exc:
                last_exc = exc
                if index == len(attempts) - 1 or not self._is_recoverable_param_error(exc):
                    raise self._map_openai_error(exc) from exc
        raise self._map_openai_error(last_exc or ProviderError("openai-codex call failed"))

    async def _collect_response_stream(self, stream: Any) -> Any:
        text_parts: list[str] = []
        done_text: str | None = None
        completed_response: Any | None = None
        response_id: str | None = None
        model: str | None = None
        status: str | None = None

        async for event in stream:
            event_type = getattr(event, "type", None)
            event_response = getattr(event, "response", None)
            if event_response is not None:
                completed_response = event_response
                response_id = response_id or getattr(event_response, "id", None)
                model = model or getattr(event_response, "model", None)
                status = getattr(event_response, "status", status)
            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", None)
                if isinstance(delta, str):
                    text_parts.append(delta)
            elif event_type == "response.output_text.done":
                text = getattr(event, "text", None)
                if isinstance(text, str):
                    done_text = text
            elif event_type == "response.completed":
                status = "completed"
            elif event_type in {"response.failed", "response.incomplete"}:
                raise ProviderError(f"openai-codex stream ended with {event_type}")

        text = "".join(text_parts) or done_text or ""
        if completed_response is not None:
            return SimpleNamespace(
                id=response_id or getattr(completed_response, "id", None),
                model=model or getattr(completed_response, "model", None),
                output_text=text,
                usage=getattr(completed_response, "usage", None),
                status=status or getattr(completed_response, "status", None),
            )
        return SimpleNamespace(
            id=response_id,
            model=model,
            output_text=text,
            usage=None,
            status=status,
        )

    # ------------------------------------------------------------------
    # Provider protocol
    # ------------------------------------------------------------------

    async def generate_structured(
        self, prompt: PromptPacket, config: ModelConfig
    ) -> LLMResult:
        t0 = perf_counter()
        response = await self._create_response(
            self._wrap_call_kwargs(prompt=prompt, config=config, structured=True)
        )
        latency_ms = int((perf_counter() - t0) * 1000)
        content = self._extract_output_text(response)

        repaired = False
        try:
            parsed = self._parse_json_object(content)
        except (json.JSONDecodeError, ValueError) as parse_err:
            validator_message = (
                parse_err.msg if isinstance(parse_err, json.JSONDecodeError) else str(parse_err)
            )
            response = await self._create_response(
                self._wrap_call_kwargs(
                    prompt=prompt,
                    config=config,
                    structured=True,
                    repair_text=content,
                    validator_message=validator_message,
                )
            )
            repaired = True
            content = self._extract_output_text(response)
            try:
                parsed = self._parse_json_object(content)
            except (json.JSONDecodeError, ValueError) as final_err:
                raise InvalidJSONError(
                    "OpenAI Codex response failed JSON parse after one repair attempt",
                    raw_text=content,
                    validator_message=str(final_err),
                ) from final_err
            latency_ms = int((perf_counter() - t0) * 1000)

        prompt_tokens, completion_tokens, total_tokens, cost = self._extract_usage(response)
        return LLMResult(
            call_id=self._make_call_id("llm"),
            provider=self.name,
            model_used=getattr(response, "model", config.model),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            parsed_json=parsed,
            tool_calls=[],
            raw_response=self._raw_response_summary(response),
            created_at=datetime.now(UTC),
            repaired_once=repaired,
        )

    async def generate_text(
        self, prompt: PromptPacket, config: ModelConfig
    ) -> LLMResult:
        t0 = perf_counter()
        response = await self._create_response(
            self._wrap_call_kwargs(prompt=prompt, config=config, structured=False)
        )
        latency_ms = int((perf_counter() - t0) * 1000)
        content = self._extract_output_text(response)
        prompt_tokens, completion_tokens, total_tokens, cost = self._extract_usage(response)
        return LLMResult(
            call_id=self._make_call_id("llm"),
            provider=self.name,
            model_used=getattr(response, "model", config.model),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            parsed_json={"text": content},
            tool_calls=[],
            raw_response=self._raw_response_summary(response),
            created_at=datetime.now(UTC),
            repaired_once=False,
        )

    async def embed(
        self, texts: list[str], config: EmbeddingConfig
    ) -> EmbeddingResult:
        raise ProviderError("openai-codex provider does not expose embeddings in WorldFork")

    async def healthcheck(self) -> ProviderHealth:
        t0 = perf_counter()
        token, source = self._resolve_oauth_token_with_source()
        latency_ms = int((perf_counter() - t0) * 1000)
        expiry = decode_jwt_expiry_seconds(token) if token else None
        expires_at = datetime.fromtimestamp(expiry, UTC).isoformat() if expiry else None
        expired = expiry is not None and datetime.now(UTC).timestamp() >= expiry
        return ProviderHealth(
            provider=self.name,
            ok=bool(token) and not expired,
            latency_ms=latency_ms,
            details={
                "base_url": self._base_url,
                "auth_source": source,
                "token_present": bool(token),
                "token_expires_at": expires_at,
                "token_expired": expired,
            },
        )
