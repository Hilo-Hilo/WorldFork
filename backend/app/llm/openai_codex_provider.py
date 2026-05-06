from __future__ import annotations

import os
import re
from types import SimpleNamespace
from typing import Any

import httpx

from app.core.config import get_settings
from app.llm.provider import LLMProvider, LLMProviderUnavailable
from app.llm.schemas import LLMRequest, LLMResponse
from backend.app.providers.openai_codex import (
    OPENAI_CODEX_ACCESS_ENV,
    OPENAI_CODEX_OAUTH_ENV,
    OPENAI_CODEX_RESPONSES_BASE_URL,
    read_codex_oauth_token,
)

try:
    from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
except Exception:  # pragma: no cover - keeps imports usable in minimal test envs
    APIConnectionError = APIStatusError = APITimeoutError = Exception  # type: ignore[assignment]
    AsyncOpenAI = None  # type: ignore[assignment]


class OpenAICodexProvider(LLMProvider):
    """Codex OAuth provider for the audited ``app.llm`` runtime path."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        settings = get_settings()
        token = _resolve_oauth_token(settings)
        if not token:
            raise LLMProviderUnavailable(
                "OpenAI Codex OAuth token not found; run "
                "`worldfork settings openai-codex-login` or set OPENAI_CODEX_OAUTH_TOKEN."
            )
        if AsyncOpenAI is None:  # pragma: no cover
            raise LLMProviderUnavailable("openai>=1.51 is required for OpenAI Codex.")

        try:
            timeout_seconds = float(request.metadata.get("timeout_seconds") or 120.0)
        except (TypeError, ValueError):
            timeout_seconds = 120.0
        client = AsyncOpenAI(
            base_url=settings.openai_codex_base_url or OPENAI_CODEX_RESPONSES_BASE_URL,
            api_key=token,
            timeout=httpx.Timeout(max(1.0, min(timeout_seconds, 1800.0))),
        )
        kwargs: dict[str, Any] = {
            "model": request.model or settings.openai_codex_default_model,
            "instructions": _instructions_from_messages(request.messages),
            "input": _input_from_messages(request.messages),
            "store": False,
            "stream": True,
        }
        kwargs["text"] = _text_format(request)

        try:
            try:
                response = await client.responses.create(**kwargs)
                if hasattr(response, "__aiter__"):
                    response = await _collect_response_stream(response)
            except Exception as exc:
                if _is_recoverable_param_error(exc) and "text" in kwargs:
                    try:
                        fallback_kwargs = dict(kwargs)
                        fallback_kwargs.pop("text", None)
                        response = await client.responses.create(**fallback_kwargs)
                        if hasattr(response, "__aiter__"):
                            response = await _collect_response_stream(response)
                    except Exception as fallback_exc:
                        raise _provider_error(fallback_exc) from fallback_exc
                else:
                    raise _provider_error(exc) from exc
        finally:
            await client.close()

        content = _extract_output_text(response)
        return LLMResponse(
            content=content,
            raw={
                "id": _get_field(response, "id"),
                "model": _get_field(response, "model"),
                "status": _get_field(response, "status"),
                "usage": _usage_payload(_get_field(response, "usage")),
            },
        )


def _resolve_oauth_token(settings: Any) -> str | None:
    for value in (
        getattr(settings, "openai_codex_oauth_token", None),
        os.environ.get(OPENAI_CODEX_OAUTH_ENV),
        os.environ.get(OPENAI_CODEX_ACCESS_ENV),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return read_codex_oauth_token(getattr(settings, "openai_codex_auth_file", None))


def _instructions_from_messages(messages: list[dict[str, str]]) -> str:
    system_parts = [
        item.get("content", "")
        for item in messages
        if item.get("role") in {"system", "developer"} and item.get("content")
    ]
    base = "\n\n".join(system_parts).strip()
    if not base:
        base = "You are a WorldFork LLM agent."
    return (
        f"{base}\n\n"
        "Return exactly one valid JSON object. Do not wrap it in Markdown. "
        "Do not include commentary outside the JSON object."
    )


def _input_from_messages(messages: list[dict[str, str]]) -> list[dict[str, Any]]:
    input_items: list[dict[str, Any]] = []
    for item in messages:
        role = item.get("role") or "user"
        if role in {"system", "developer"}:
            continue
        content = item.get("content") or ""
        if role == "assistant":
            content = f"Previous assistant response:\n{content}"
        input_items.append(
            {
                "role": "user",
                "content": [{"type": "input_text", "text": content}],
            }
        )
    if not input_items:
        input_items.append({"role": "user", "content": [{"type": "input_text", "text": ""}]})
    return input_items


def _text_format(request: LLMRequest) -> dict[str, Any]:
    if request.json_schema:
        return {
            "format": {
                "type": "json_schema",
                "name": _responses_schema_name(request.purpose),
                "schema": request.json_schema,
            }
        }
    return {"format": {"type": "json_object"}}


def _responses_schema_name(value: Any) -> str:
    raw = value if isinstance(value, str) and value.strip() else "worldfork_response"
    normalized = re.sub(r"[^A-Za-z0-9_-]", "_", raw).strip("_")
    return (normalized or "worldfork_response")[:64]


def _get_field(value: Any, field: str) -> Any:
    if isinstance(value, dict):
        return value.get(field)
    return getattr(value, field, None)


def _extract_output_text(response: Any) -> str:
    direct = _get_field(response, "output_text")
    if isinstance(direct, str) and direct:
        return direct
    chunks: list[str] = []
    output = _get_field(response, "output") or []
    for item in output:
        item_text = _get_field(item, "text")
        if isinstance(item_text, str):
            chunks.append(item_text)
        content = _get_field(item, "content") or []
        for part in content:
            text = _get_field(part, "text")
            if isinstance(text, str):
                chunks.append(text)
    return "".join(chunks)


async def _collect_response_stream(stream: Any) -> Any:
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
            raise LLMProviderUnavailable(f"OpenAI Codex stream ended with {event_type}")

    return SimpleNamespace(
        id=response_id or _get_field(completed_response, "id"),
        model=model or _get_field(completed_response, "model"),
        output_text="".join(text_parts) or done_text or "",
        usage=_get_field(completed_response, "usage"),
        status=status or _get_field(completed_response, "status"),
    )


def _usage_payload(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    if isinstance(usage, dict):
        return usage
    return {
        "input_tokens": getattr(usage, "input_tokens", None),
        "output_tokens": getattr(usage, "output_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def _provider_error(exc: Exception) -> LLMProviderUnavailable:
    if isinstance(exc, APITimeoutError):
        return LLMProviderUnavailable(f"OpenAI Codex timed out: {exc}")
    if isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", None)
        return LLMProviderUnavailable(f"OpenAI Codex HTTP {status}: {exc}")
    if isinstance(exc, APIConnectionError):
        return LLMProviderUnavailable(f"OpenAI Codex connection error: {exc}")
    return LLMProviderUnavailable(f"OpenAI Codex error: {exc}")


def _is_recoverable_param_error(exc: Exception) -> bool:
    return isinstance(exc, APIStatusError) and getattr(exc, "status_code", 0) in (400, 422)
