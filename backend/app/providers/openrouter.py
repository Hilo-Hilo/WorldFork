"""OpenRouter provider — primary LLM adapter for WorldFork.

Wraps the OpenAI-compatible AsyncOpenAI client pointed at
``https://openrouter.ai/api/v1`` per PRD §16.1 / Plan Appendix A.1.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from time import perf_counter
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
except Exception:  # pragma: no cover — keeps tests importable
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


class OpenRouterProvider(BaseProvider):
    """OpenRouter provider — implements :class:`LLMProvider` Protocol."""

    name = "openrouter"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        default_model: str = "google/gemini-3.1-flash-lite-preview",
        fallback_model: str | None = "google/gemini-3.1-flash-lite-preview",
        http_referer: str = "http://localhost:3003",
        x_title: str = "WorldFork",
        request_timeout: float = 120.0,
    ) -> None:
        if AsyncOpenAI is None:  # pragma: no cover
            raise ImportError(
                "openai>=1.51 is required for OpenRouterProvider — "
                "install with `pip install openai>=1.51`."
            )
        self._api_key = api_key
        self._base_url = base_url
        self.default_model = default_model
        self.fallback_model = fallback_model
        self._http_referer = http_referer
        self._x_title = x_title
        self._request_timeout = request_timeout
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=httpx.Timeout(request_timeout),
            default_headers={
                "HTTP-Referer": http_referer,
                # Plan A.1 explicitly notes the X-OpenRouter-Title header name.
                "X-OpenRouter-Title": x_title,
            },
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_messages(prompt: PromptPacket) -> list[dict[str, str]]:
        """Render ``PromptPacket`` to the OpenAI messages list.

        B2-B uses a deterministic JSON dump of the packet (excluding ``system``).
        B3-A will swap this for a Jinja-rendered prompt; the JSON dump is good
        enough for live-call sanity checks and policy testing today.
        """
        body = json.dumps(
            prompt.model_dump(mode="json", exclude={"system"}),
            indent=2,
            sort_keys=True,
            default=str,
        )
        return [
            {"role": "system", "content": prompt.system},
            {"role": "user", "content": body},
        ]

    @staticmethod
    def _select_response_format(config: ModelConfig) -> dict[str, Any]:
        if config.response_format:
            return config.response_format
        return {"type": "json_object"}

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
    def _extract_usage(response: Any) -> tuple[int, int, int, float | None]:
        usage = getattr(response, "usage", None)
        if usage is None:
            return 0, 0, 0, None
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", prompt_tokens + completion_tokens) or 0)
        # OpenRouter returns `cost` on the usage object when available.
        cost: float | None = None
        for attr in ("cost", "total_cost"):
            v = getattr(usage, attr, None)
            if v is not None:
                try:
                    cost = float(v)
                    break
                except (TypeError, ValueError):
                    continue
        return prompt_tokens, completion_tokens, total_tokens, cost

    def _wrap_call_kwargs(
        self,
        *,
        config: ModelConfig,
        messages: list[dict[str, str]],
        response_format: dict[str, Any] | None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": config.model,
            "messages": messages,
            "temperature": config.temperature,
            "top_p": config.top_p,
            "max_tokens": config.max_tokens,
        }
        if response_format is not None:
            kwargs["response_format"] = response_format
        if config.tools:
            kwargs["tools"] = config.tools
        if config.fallback_model:
            # OpenRouter-specific routing hint via `extra_body`.
            kwargs["extra_body"] = {
                "models": [config.model, config.fallback_model],
            }
        return kwargs

    @staticmethod
    def _map_openai_error(exc: Exception) -> ProviderError:
        # RateLimit → RateLimitError, Timeout → ProviderTimeoutError,
        # everything else → ProviderError preserving status when available.
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
            return ProviderError(f"openrouter status_error[{status}]: {exc}")
        if isinstance(exc, APIConnectionError):
            return ProviderError(f"openrouter connection_error: {exc}")
        return ProviderError(f"openrouter error: {exc}")

    # ------------------------------------------------------------------
    # generate_structured
    # ------------------------------------------------------------------

    async def generate_structured(
        self, prompt: PromptPacket, config: ModelConfig
    ) -> LLMResult:
        messages = self._build_messages(prompt)
        response_format = self._select_response_format(config)

        t0 = perf_counter()
        try:
            response = await self._client.chat.completions.create(
                **self._wrap_call_kwargs(
                    config=config, messages=messages, response_format=response_format
                )
            )
        except (APIStatusError, APIConnectionError, APITimeoutError, OpenAIRateLimitError) as exc:
            # Schema not supported by some models; one fallback to json_object.
            if (
                isinstance(exc, APIStatusError)
                and getattr(exc, "status_code", 0) in (400, 422)
                and isinstance(response_format, dict)
                and response_format.get("type") == "json_schema"
            ):
                response_format = {"type": "json_object"}
                try:
                    response = await self._client.chat.completions.create(
                        **self._wrap_call_kwargs(
                            config=config, messages=messages, response_format=response_format
                        )
                    )
                except Exception as inner:
                    raise self._map_openai_error(inner) from inner
            else:
                raise self._map_openai_error(exc) from exc
        except Exception as exc:  # pragma: no cover — defensive
            raise self._map_openai_error(exc) from exc

        latency_ms = int((perf_counter() - t0) * 1000)
        choice = response.choices[0]
        message = choice.message
        content = message.content or ""

        repaired = False
        parsed: dict[str, Any] | None = None
        try:
            parsed = self._parse_json_object(content)
        except (json.JSONDecodeError, ValueError) as parse_err:
            # One repair attempt — only when caller wanted structured output.
            validator_message = (
                parse_err.msg if isinstance(parse_err, json.JSONDecodeError) else str(parse_err)
            )
            repair_messages = list(messages) + [
                {"role": "assistant", "content": content},
                {
                    "role": "system",
                    "content": (
                        "Your prior response failed JSON validation: "
                        f"{validator_message}. Re-emit a single valid JSON object only, "
                        "no commentary."
                    ),
                },
            ]
            try:
                repair_response = await self._client.chat.completions.create(
                    **self._wrap_call_kwargs(
                        config=config,
                        messages=repair_messages,
                        response_format={"type": "json_object"},
                    )
                )
            except Exception as exc:
                raise self._map_openai_error(exc) from exc
            repaired = True
            response = repair_response
            choice = response.choices[0]
            message = choice.message
            content = message.content or ""
            try:
                parsed = self._parse_json_object(content)
            except (json.JSONDecodeError, ValueError) as final_err:
                raise InvalidJSONError(
                    "OpenRouter response failed JSON parse after one repair attempt",
                    raw_text=content,
                    validator_message=str(final_err),
                ) from final_err
            latency_ms = int((perf_counter() - t0) * 1000)

        prompt_tokens, completion_tokens, total_tokens, cost = self._extract_usage(response)

        tool_calls: list[dict] = []
        raw_tool_calls = getattr(message, "tool_calls", None) or []
        for tc in raw_tool_calls:
            try:
                tool_calls.append(tc.model_dump())  # openai>=1.x objects support this
            except Exception:
                tool_calls.append({"raw": str(tc)})

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
            tool_calls=tool_calls,
            raw_response={"id": getattr(response, "id", None)},
            created_at=datetime.now(UTC),
            repaired_once=repaired,
        )

    # ------------------------------------------------------------------
    # generate_text
    # ------------------------------------------------------------------

    async def generate_text(
        self, prompt: PromptPacket, config: ModelConfig
    ) -> LLMResult:
        messages = self._build_messages(prompt)
        t0 = perf_counter()
        try:
            response = await self._client.chat.completions.create(
                **self._wrap_call_kwargs(
                    config=config, messages=messages, response_format=None
                )
            )
        except Exception as exc:
            raise self._map_openai_error(exc) from exc
        latency_ms = int((perf_counter() - t0) * 1000)

        choice = response.choices[0]
        message = choice.message
        content = message.content or ""
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
            raw_response={"id": getattr(response, "id", None)},
            created_at=datetime.now(UTC),
            repaired_once=False,
        )

    # ------------------------------------------------------------------
    # embed
    # ------------------------------------------------------------------

    async def embed(
        self, texts: list[str], config: EmbeddingConfig
    ) -> EmbeddingResult:
        # OpenRouter passes through OpenAI's /v1/embeddings endpoint.
        try:
            kwargs: dict[str, Any] = {"model": config.model, "input": texts}
            if config.dimensions is not None:
                kwargs["dimensions"] = config.dimensions
            response = await self._client.embeddings.create(**kwargs)
        except Exception as exc:
            raise self._map_openai_error(exc) from exc

        vectors = [list(item.embedding) for item in response.data]
        usage = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        return EmbeddingResult(
            vectors=vectors,
            model_used=getattr(response, "model", config.model),
            prompt_tokens=prompt_tokens,
            cost_usd=None,
        )

    # ------------------------------------------------------------------
    # healthcheck
    # ------------------------------------------------------------------

    async def healthcheck(self) -> ProviderHealth:
        # Use a bare httpx GET so we can enforce a 5-second timeout regardless
        # of the configured request_timeout.
        url = self._base_url.rstrip("/") + "/models"
        t0 = perf_counter()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "HTTP-Referer": self._http_referer,
                        "X-OpenRouter-Title": self._x_title,
                    },
                )
            latency_ms = int((perf_counter() - t0) * 1000)
            ok = resp.status_code == 200
            details: dict[str, Any] = {"status_code": resp.status_code}
            if ok:
                try:
                    body = resp.json()
                    details["model_count"] = len(body.get("data", []) or [])
                except Exception:
                    details["model_count"] = None
            return ProviderHealth(
                provider=self.name, ok=ok, latency_ms=latency_ms, details=details
            )
        except Exception as exc:
            latency_ms = int((perf_counter() - t0) * 1000)
            return ProviderHealth(
                provider=self.name,
                ok=False,
                latency_ms=latency_ms,
                details={"error": str(exc)[:200]},
            )
