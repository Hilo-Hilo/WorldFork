from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings
from app.llm.openrouter_payload import (
    build_openrouter_response_format,
    openrouter_options_from_metadata,
    response_format_override_from_metadata,
)
from app.llm.provider import LLMProvider, LLMProviderUnavailable
from app.llm.schemas import LLMRequest, LLMResponse


class OpenRouterProvider(LLMProvider):
    """OpenAI-compatible OpenRouter chat-completions provider."""

    async def complete(self, request: LLMRequest) -> LLMResponse:
        settings = get_settings()
        if not settings.openrouter_api_key:
            raise LLMProviderUnavailable("LLM unavailable")

        payload = {
            "model": request.model or settings.default_model,
            "messages": request.messages,
        }
        if getattr(settings, "openrouter_prompt_caching_enabled", True):
            cache_control = _prompt_cache_control(request.metadata)
            if cache_control is not None:
                payload["cache_control"] = cache_control
        response_format_override = response_format_override_from_metadata(request.metadata)
        response_format_name = (
            request.metadata.get("openrouter_response_schema_name")
            or request.metadata.get("response_schema_name")
            or request.purpose
        )
        payload["response_format"] = build_openrouter_response_format(
            json_schema=request.json_schema,
            response_format=response_format_override,
            name=str(response_format_name),
        )
        payload.update(openrouter_options_from_metadata(request.metadata))
        for key in ("temperature", "max_tokens", "top_p"):
            if key in request.metadata:
                payload[key] = request.metadata[key]
        reasoning = request.metadata.get("reasoning")
        if isinstance(reasoning, dict):
            payload["reasoning"] = reasoning
        elif "include_reasoning" in request.metadata:
            payload["include_reasoning"] = bool(request.metadata["include_reasoning"])

        headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://worldfork.local",
            "X-Title": "WorldFork",
        }
        try:
            timeout = float(request.metadata.get("timeout_seconds") or 60.0)
        except (TypeError, ValueError):
            timeout = 60.0
        async with httpx.AsyncClient(timeout=max(1.0, min(timeout, 1800.0))) as client:
            try:
                response = await client.post(
                    settings.openrouter_chat_completions_url,
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if (
                    exc.response.status_code in (400, 422)
                    and isinstance(payload.get("response_format"), dict)
                    and payload["response_format"].get("type") == "json_schema"
                    and response_format_override != "json_schema"
                ):
                    retry_payload = dict(payload)
                    retry_payload["response_format"] = {"type": "json_object"}
                    try:
                        response = await client.post(
                            settings.openrouter_chat_completions_url,
                            headers=headers,
                            json=retry_payload,
                        )
                        response.raise_for_status()
                    except httpx.HTTPStatusError as retry_exc:
                        raise _unavailable_from_status_error(retry_exc) from retry_exc
                    except httpx.TimeoutException as retry_exc:
                        raise LLMProviderUnavailable(f"LLM unavailable: request timed out: {retry_exc}") from retry_exc
                    except httpx.HTTPError as retry_exc:
                        raise LLMProviderUnavailable(f"LLM unavailable: {retry_exc}") from retry_exc
                    else:
                        payload = retry_payload
                        data = response.json()
                        content = data.get("choices", [{}])[0].get("message", {}).get("content") or ""
                        return LLMResponse(content=content, raw=data)
                status = exc.response.status_code
                reason = exc.response.reason_phrase
                body = exc.response.text.strip().replace("\n", " ")[:500]
                detail = f"LLM unavailable: HTTP {status} {reason}"
                if body:
                    detail = f"{detail}: {body}"
                raise LLMProviderUnavailable(detail) from exc
            except httpx.TimeoutException as exc:
                raise LLMProviderUnavailable(f"LLM unavailable: request timed out: {exc}") from exc
            except httpx.HTTPError as exc:
                raise LLMProviderUnavailable(f"LLM unavailable: {exc}") from exc
            data = response.json()

        content = data.get("choices", [{}])[0].get("message", {}).get("content") or ""
        return LLMResponse(content=content, raw=data)


def _prompt_cache_control(metadata: dict[str, Any]) -> dict[str, Any] | None:
    value = metadata.get("openrouter_cache_control") or metadata.get("cache_control")
    if value is False or value is None:
        return None
    if isinstance(value, dict):
        cache_type = value.get("type") or "ephemeral"
        if cache_type != "ephemeral":
            return None
        cache_control = {"type": "ephemeral"}
        if value.get("ttl") in {"1h"}:
            cache_control["ttl"] = "1h"
        return cache_control
    if value is True:
        return {"type": "ephemeral"}
    return None


def _unavailable_from_status_error(exc: httpx.HTTPStatusError) -> LLMProviderUnavailable:
    status = exc.response.status_code
    reason = exc.response.reason_phrase
    body = exc.response.text.strip().replace("\n", " ")[:500]
    detail = f"LLM unavailable: HTTP {status} {reason}"
    if body:
        detail = f"{detail}: {body}"
    return LLMProviderUnavailable(detail)
