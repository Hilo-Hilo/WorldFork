from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models
from app.llm.openai_compatible_provider import OpenAICompatibleProvider
from app.llm.openai_codex_provider import OpenAICodexProvider
from app.llm.openrouter_provider import OpenRouterProvider
from app.llm.provider import DeterministicLLMProvider, LLMProvider, LLMProviderUnavailable
from app.llm.redaction import redact_payload
from app.llm.routing import AuditedLLMRoute, LLMRouteCandidate, resolve_audited_llm_route
from app.llm.schemas import LLMRequest, LLMResponse
from app.storage.artifact_store import ArtifactStore
from backend.app.models.settings import ProviderSettingModel


class LLMCallError(RuntimeError):
    def __init__(self, message: str, *, call_id: Any | None = None):
        super().__init__(message)
        self.call_id = call_id


class LLMJSONParseError(ValueError):
    pass


ProviderFactory = Callable[[], LLMProvider]
_AUDITED_PROVIDER_FACTORIES: dict[str, ProviderFactory] = {}


def register_audited_llm_provider(name: str, factory: ProviderFactory) -> None:
    """Register a provider factory for the direct audited LLM runtime."""
    _AUDITED_PROVIDER_FACTORIES[_normalize_provider_name(name)] = factory


def _register_builtin_provider_factories() -> None:
    _AUDITED_PROVIDER_FACTORIES.setdefault("openrouter", OpenRouterProvider)
    _AUDITED_PROVIDER_FACTORIES.setdefault("openai-codex", OpenAICodexProvider)
    _AUDITED_PROVIDER_FACTORIES.setdefault("deterministic", DeterministicLLMProvider)


def _normalize_provider_name(name: str) -> str:
    return name.strip().lower()


def provider_for_name(provider_name: str, db: Session | None = None) -> LLMProvider:
    _register_builtin_provider_factories()
    normalized = _normalize_provider_name(provider_name)
    factory = _AUDITED_PROVIDER_FACTORIES.get(normalized)
    if factory is not None:
        return factory()
    if db is not None:
        provider = _provider_from_settings_row(normalized, db)
        if provider is not None:
            return provider
    known = ", ".join(sorted(_AUDITED_PROVIDER_FACTORIES))
    raise RuntimeError(f"Unsupported LLM provider: {normalized}. Registered providers: {known}")


def provider_for_settings() -> LLMProvider:
    settings = get_settings()
    return provider_for_name(settings.default_llm_provider)


def _provider_from_settings_row(provider_name: str, db: Session) -> LLMProvider | None:
    try:
        row = db.get(ProviderSettingModel, provider_name)
    except Exception:
        row = None
    if row is None:
        return None
    if not bool(row.enabled):
        raise LLMProviderUnavailable(f"LLM provider {provider_name!r} is disabled")
    payload = row.payload if isinstance(row.payload, dict) else {}
    api_shape = (
        payload.get("provider_api")
        or payload.get("api_shape")
        or payload.get("api")
        or "openai-compatible"
    )
    if str(api_shape) not in {
        "openai-compatible",
        "openai-chat-completions",
        "chat-completions",
    }:
        raise RuntimeError(
            f"Unsupported audited provider API for {provider_name!r}: {api_shape!r}"
        )
    api_key = os.environ.get(row.api_key_env)
    if not api_key:
        raise LLMProviderUnavailable(
            f"LLM provider {provider_name!r} missing API key env {row.api_key_env!r}"
        )
    extra_headers = {
        str(key): str(value)
        for key, value in (row.extra_headers or {}).items()
        if value is not None
    }
    return OpenAICompatibleProvider(
        provider=row.provider,
        api_key=api_key,
        base_url=row.base_url,
        default_model=row.default_model,
        extra_headers=extra_headers,
        chat_completions_url=payload.get("chat_completions_url"),
        request_timeout=float(payload.get("request_timeout_seconds") or 120.0),
    )


def parse_json_object(content: str) -> dict[str, Any]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        first_error = exc
    else:
        if isinstance(parsed, dict):
            return parsed
        raise LLMJSONParseError("LLM response JSON was not an object")

    stripped = content.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        fenced = stripped.removeprefix("```").removesuffix("```").strip()
        if fenced.lower().startswith("json"):
            fenced = fenced[4:].strip()
        try:
            parsed = json.loads(fenced)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(parsed, dict):
                return parsed
            raise LLMJSONParseError("LLM response JSON was not an object")
    raise LLMJSONParseError(
        f"LLM response did not contain a valid JSON object: {first_error}"
    ) from first_error


def ensure_response_json_object(response: LLMResponse) -> dict[str, Any]:
    if isinstance(response.parsed, dict):
        return response.parsed
    if response.parsed is not None:
        raise LLMJSONParseError("LLM response JSON was not an object")
    return parse_json_object(response.content)


def _json_repair_messages(
    messages: list[dict[str, str]],
    error_message: str,
    invalid_content: str | None = None,
) -> list[dict[str, str]]:
    content = (
        "Your previous response was invalid for WorldFork's machine parser: "
        f"{error_message}. Return exactly one JSON object and nothing else. "
        "Do not return a JSON array, markdown, prose, comments, or multiple objects."
    )
    if invalid_content:
        content = (
            f"{content}\n\nInvalid response excerpt:\n"
            f"{_truncate_invalid_content(invalid_content)}"
        )
    return [
        *messages,
        {
            "role": "user",
            "content": content,
        },
    ]


def _truncate_invalid_content(content: str, *, limit: int = 2000) -> str:
    stripped = content.strip()
    if len(stripped) <= limit:
        return stripped
    return f"{stripped[:limit]}...[truncated]"


def _failure_response(message: str) -> LLMResponse:
    return LLMResponse(
        content=json.dumps({"error": message, "fallback": True}),
        parsed={"error": message, "fallback": True},
        raw={"error": message},
    )


def _llm_error_message(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "LLM call timed out"
    if isinstance(exc, LLMProviderUnavailable):
        return str(exc) or "LLM unavailable"
    return str(exc)


def _route_retry_attempts(settings: Any, metadata: dict[str, Any]) -> int:
    retry_policy = str(metadata.get("retry_policy") or "exponential_backoff")
    if retry_policy == "none":
        return 1
    return max(1, int(getattr(settings, "llm_max_retries", 1)))


def _retry_delay(base_seconds: float, attempt: int, retry_policy: str) -> float:
    if retry_policy == "none":
        return 0.0
    if retry_policy == "linear":
        return base_seconds * attempt
    return base_seconds * (2 ** max(0, attempt - 1))


def _timeout_seconds(metadata: dict[str, Any]) -> float:
    try:
        value = float(metadata.get("timeout_seconds") or 120.0)
    except (TypeError, ValueError):
        value = 120.0
    return max(1.0, min(value, 1800.0))


def complete_with_audit(
    db: Session,
    *,
    big_bang_id,
    purpose: str,
    model: str | None,
    messages: list[dict[str, str]],
    metadata: dict[str, Any] | None = None,
    json_schema: dict[str, Any] | None = None,
    route: AuditedLLMRoute | str | None = None,
) -> tuple[LLMResponse, models.LLMCall]:
    store = ArtifactStore()
    metadata = metadata or {}
    settings = get_settings()
    resolved_route = resolve_audited_llm_route(
        db,
        route=route,
        fallback_provider=settings.default_llm_provider,
        fallback_model=model,
    )
    initial_candidate = resolved_route.primary
    request_payload = {
        "purpose": purpose,
        "route": resolved_route.audit_meta(),
        "provider": initial_candidate.provider,
        "model": initial_candidate.model,
        "messages": messages,
        "json_schema": json_schema,
        "metadata": metadata,
    }
    sanitized_request = redact_payload(request_payload)
    request_artifact = store.write_json(
        db,
        big_bang_id=big_bang_id,
        relative_path=f"big_bang_{big_bang_id}/sanitized_llm_calls/{purpose}_request.json",
        payload=sanitized_request,
        kind="llm_request_sanitized",
    )
    raw_request_artifact = store.write_json(
        db,
        big_bang_id=big_bang_id,
        relative_path=f"big_bang_{big_bang_id}/raw_llm_calls/{purpose}_request.json",
        payload=request_payload,
        kind="llm_request_raw",
        debug_only=True,
    )
    call = models.LLMCall(
        big_bang_id=big_bang_id,
        provider=initial_candidate.provider,
        model=initial_candidate.model,
        purpose=purpose,
        status="running",
        request_artifact_id=request_artifact.id,
        meta={
            **metadata,
            "request_metadata": metadata,
            "raw_request_artifact_id": str(raw_request_artifact.id),
            "llm_route": resolved_route.audit_meta(),
        },
    )
    db.add(call)
    db.flush()
    _commit_audit_progress(db)
    attempts: list[dict[str, Any]] = []
    response: LLMResponse | None = None
    last_error: Exception | None = None
    successful_candidate: LLMRouteCandidate | None = None
    for candidate in resolved_route.candidates():
        attempt_messages = messages
        request_metadata = resolved_route.metadata_for(candidate, metadata)
        retry_policy = str(request_metadata.get("retry_policy") or "exponential_backoff")
        max_attempts = _route_retry_attempts(settings, request_metadata)
        try:
            provider = (
                provider_for_settings()
                if route is None and candidate.provider == settings.default_llm_provider
                else provider_for_name(candidate.provider, db=db)
            )
        except Exception as exc:
            last_error = exc
            attempts.append(
                {
                    "attempt": 0,
                    "provider": candidate.provider,
                    "model": candidate.model,
                    "status": "failed",
                    "error": _llm_error_message(exc),
                }
            )
            continue
        for attempt in range(1, max_attempts + 1):
            try:
                response = asyncio.run(
                    asyncio.wait_for(
                        provider.complete(
                            LLMRequest(
                                purpose=purpose,
                                model=candidate.model,
                                messages=attempt_messages,
                                json_schema=json_schema,
                                metadata=request_metadata,
                            )
                        ),
                        timeout=_timeout_seconds(request_metadata),
                    )
                )
                if not response.content and not response.parsed:
                    raise RuntimeError("LLM response was empty")
                response.parsed = ensure_response_json_object(response)
                attempts.append(
                    {
                        "attempt": attempt,
                        "provider": candidate.provider,
                        "model": candidate.model,
                        "status": "succeeded",
                    }
                )
                successful_candidate = candidate
                break
            except Exception as exc:
                last_error = exc
                failed_response = response
                response = None
                error_message = _llm_error_message(exc)
                attempts.append(
                    {
                        "attempt": attempt,
                        "provider": candidate.provider,
                        "model": candidate.model,
                        "status": "failed",
                        "error": error_message,
                    }
                )
                if attempt < max_attempts:
                    if isinstance(exc, LLMJSONParseError):
                        invalid_content = (
                            failed_response.content if failed_response is not None else None
                        )
                        attempt_messages = _json_repair_messages(
                            messages,
                            error_message,
                            invalid_content,
                        )
                    delay = _retry_delay(
                        float(getattr(settings, "llm_retry_backoff_seconds", 0)),
                        attempt,
                        retry_policy,
                    )
                    if delay > 0:
                        time.sleep(delay)
        if successful_candidate is not None:
            break
    try:
        if response is None or successful_candidate is None:
            if isinstance(last_error, LLMProviderUnavailable):
                raise last_error
            raise RuntimeError(str(last_error) if last_error else "LLM call failed")
        call.provider = successful_candidate.provider
        call.model = successful_candidate.model
        response.parsed = ensure_response_json_object(response)
        response_payload = {
            "content": response.content,
            "parsed": response.parsed,
            "raw": response.raw,
            "provider": successful_candidate.provider,
            "model": successful_candidate.model,
        }
        response_artifact = store.write_json(
            db,
            big_bang_id=big_bang_id,
            relative_path=f"big_bang_{big_bang_id}/sanitized_llm_calls/{purpose}_response.json",
            payload=redact_payload(response_payload),
            kind="llm_response_sanitized",
        )
        raw_response_artifact = store.write_json(
            db,
            big_bang_id=big_bang_id,
            relative_path=f"big_bang_{big_bang_id}/raw_llm_calls/{purpose}_response.json",
            payload=response_payload,
            kind="llm_response_raw",
            debug_only=True,
        )
        call.status = "succeeded"
        call.response_artifact_id = response_artifact.id
        call.meta = {
            **call.meta,
            "raw_response_artifact_id": str(raw_response_artifact.id),
            "attempts": attempts,
            "effective_provider": successful_candidate.provider,
            "effective_model": successful_candidate.model,
        }
        db.flush()
        _commit_audit_progress(db)
        return response, call
    except Exception as exc:
        error_message = _llm_error_message(exc)
        fallback = _failure_response(error_message)
        response_artifact = store.write_json(
            db,
            big_bang_id=big_bang_id,
            relative_path=f"big_bang_{big_bang_id}/sanitized_llm_calls/{purpose}_error.json",
            payload=redact_payload(fallback.model_dump()),
            kind="llm_response_sanitized",
        )
        call.status = "failed"
        call.response_artifact_id = response_artifact.id
        call.meta = {**call.meta, "error": error_message, "attempts": attempts}
        db.flush()
        _commit_audit_progress(db)
        raise LLMCallError(error_message, call_id=call.id) from exc


def _commit_audit_progress(db: Session) -> None:
    """Commit LLM audit state before/after long provider waits.

    The tick runtime calls audited providers from inside request and job flows.
    Committing the running LLM call before the provider request prevents an
    open database transaction from sitting idle while the network call runs.
    """
    commit = getattr(db, "commit", None)
    if callable(commit):
        commit()
