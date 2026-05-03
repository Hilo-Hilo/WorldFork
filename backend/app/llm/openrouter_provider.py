from __future__ import annotations

import json

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
            "messages": _messages_with_json_schema_contract(request.messages, request.json_schema),
        }
        # DeepSeek through OpenRouter currently accepts OpenAI's json_object
        # response mode but rejects json_schema envelopes for chat completions.
        # Keep the schema as prompt-side contract and request valid JSON here.
        payload["response_format"] = {"type": "json_object"}
        for key in ("temperature", "max_tokens", "top_p"):
            if key in request.metadata:
                payload[key] = request.metadata[key]

        headers = {
            "Authorization": f"Bearer {settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": getattr(settings, "openrouter_http_referer", "https://worldfork.local"),
            "X-Title": getattr(settings, "openrouter_title", "WorldFork"),
            "X-OpenRouter-Title": getattr(settings, "openrouter_title", "WorldFork"),
        }
        try:
            timeout = float(
                request.metadata.get("request_timeout_seconds")
                or request.metadata.get("timeout_seconds")
                or 60.0
            )
        except (TypeError, ValueError):
            timeout = 60.0
        async with httpx.AsyncClient(timeout=max(1.0, min(timeout, 1800.0))) as client:
            response = await client.post(
                settings.openrouter_chat_completions_url,
                headers=headers,
                json=payload,
            )
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                reason = exc.response.reason_phrase
                body = exc.response.text.strip().replace("\n", " ")[:500]
                detail = f"LLM unavailable: HTTP {status} {reason}"
                if body:
                    detail = f"{detail}: {body}"
                raise LLMProviderUnavailable(detail) from exc
            except httpx.HTTPError as exc:
                raise LLMProviderUnavailable(f"LLM unavailable: {exc}") from exc
            data = response.json()

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return LLMResponse(content=content, raw=data)


def _messages_with_json_schema_contract(
    messages: list[dict[str, str]],
    json_schema: dict | None,
) -> list[dict[str, str]]:
    if not json_schema:
        return messages
    return [
        *messages,
        {
            "role": "user",
            "content": (
                "Return exactly one JSON object and nothing else. The object must satisfy this "
                "JSON Schema contract:\n"
                f"{json.dumps(json_schema, ensure_ascii=True, sort_keys=True, default=str)}"
            ),
        },
    ]
