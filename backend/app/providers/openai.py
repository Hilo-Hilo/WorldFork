"""OpenAI direct provider — thin shim that points the OpenAI SDK at api.openai.com.

Nearly all of the actual logic lives in :mod:`backend.app.providers.openrouter`;
this class subclasses it so we get identical generate_structured / generate_text
behaviour with just a different base_url. the provider interface requires that all enabled
providers expose the LLMProvider Protocol; this satisfies that.

TODO B5+: split out OpenAI-specific quirks (e.g. `o3` reasoning param) once
those are needed.
"""
from __future__ import annotations

from backend.app.providers.openrouter import OpenRouterProvider


class OpenAIProvider(OpenRouterProvider):
    """OpenAI direct adapter — same Protocol, different base URL."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        default_model: str = "gpt-4o",
        fallback_model: str | None = "gpt-4o-mini",
        http_referer: str = "http://localhost:3003",
        x_title: str = "WorldFork",
        request_timeout: float = 120.0,
    ) -> None:
        super().__init__(
            api_key=api_key,
            base_url=base_url,
            default_model=default_model,
            fallback_model=fallback_model,
            http_referer=http_referer,
            x_title=x_title,
            request_timeout=request_timeout,
        )
