from __future__ import annotations

from typing import Any

import httpx

from app.core.config import get_settings
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
        if request.json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": request.json_schema,
            }
        else:
            payload["response_format"] = {"type": "json_object"}
        for key in ("temperature", "max_tokens", "top_p"):
            if key in request.metadata:
                payload[key] = request.metadata[key]

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

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
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
