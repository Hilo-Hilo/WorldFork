from __future__ import annotations

import json
from typing import Any

import httpx

from app.llm.provider import LLMProvider, LLMProviderUnavailable
from app.llm.schemas import LLMRequest, LLMResponse


class OpenAICompatibleProvider(LLMProvider):
    """Generic audited provider for OpenAI-compatible chat-completions APIs."""

    def __init__(
        self,
        *,
        provider: str,
        api_key: str,
        base_url: str,
        default_model: str,
        extra_headers: dict[str, str] | None = None,
        chat_completions_url: str | None = None,
        request_timeout: float = 120.0,
        omit_auth_header: bool = False,
    ) -> None:
        self.provider = provider
        self.api_key = api_key
        self.base_url = base_url
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self.chat_completions_url = chat_completions_url or f"{base_url.rstrip('/')}/chat/completions"
        self.request_timeout = request_timeout
        self.omit_auth_header = omit_auth_header

    async def complete(self, request: LLMRequest) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": request.model or self.default_model,
            "messages": _messages_with_json_schema_contract(request.messages, request.json_schema),
        }
        payload["response_format"] = {"type": "json_object"}
        for key in ("temperature", "max_tokens", "top_p"):
            if key in request.metadata:
                payload[key] = request.metadata[key]

        headers = {"Content-Type": "application/json", **self.extra_headers}
        if not self.omit_auth_header:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            timeout = float(
                request.metadata.get("request_timeout_seconds")
                or request.metadata.get("timeout_seconds")
                or self.request_timeout
            )
        except (TypeError, ValueError):
            timeout = self.request_timeout
        timeout = max(1.0, min(timeout, 1800.0))
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                response = await client.post(self.chat_completions_url, headers=headers, json=payload)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                reason = exc.response.reason_phrase
                body = exc.response.text.strip().replace("\n", " ")[:500]
                detail = f"{self.provider} unavailable: HTTP {status} {reason}"
                if body:
                    detail = f"{detail}: {body}"
                raise LLMProviderUnavailable(detail) from exc
            except httpx.TimeoutException as exc:
                raise LLMProviderUnavailable(f"{self.provider} unavailable: request timed out: {exc}") from exc
            except httpx.HTTPError as exc:
                raise LLMProviderUnavailable(f"{self.provider} unavailable: {exc}") from exc
            data = response.json()

        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        return LLMResponse(content=content, raw=data)


def _messages_with_json_schema_contract(
    messages: list[dict[str, str]],
    json_schema: dict[str, Any] | None,
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
