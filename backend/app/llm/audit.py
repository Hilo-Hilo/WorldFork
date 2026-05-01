from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db import models
from app.llm.openai_codex_provider import OpenAICodexProvider
from app.llm.openrouter_provider import OpenRouterProvider
from app.llm.provider import DeterministicLLMProvider, LLMProvider, LLMProviderUnavailable
from app.llm.redaction import redact_payload
from app.llm.schemas import LLMRequest, LLMResponse
from app.storage.artifact_store import ArtifactStore


class LLMCallError(RuntimeError):
    def __init__(self, message: str, *, call_id: Any | None = None):
        super().__init__(message)
        self.call_id = call_id


class LLMJSONParseError(ValueError):
    pass


def provider_for_settings() -> LLMProvider:
    settings = get_settings()
    provider_name = settings.default_llm_provider.strip().lower()
    if provider_name == "openrouter":
        return OpenRouterProvider()
    if provider_name == "openai-codex":
        return OpenAICodexProvider()
    if provider_name == "deterministic":
        return DeterministicLLMProvider()
    raise RuntimeError(f"Unsupported LLM provider: {settings.default_llm_provider}")


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


def _json_repair_messages(messages: list[dict[str, str]], error_message: str) -> list[dict[str, str]]:
    return [
        *messages,
        {
            "role": "user",
            "content": (
                "Your previous response was invalid for WorldFork's machine parser: "
                f"{error_message}. Return exactly one JSON object and nothing else. "
                "Do not return a JSON array, markdown, prose, comments, or multiple objects."
            ),
        },
    ]


def _failure_response(message: str) -> LLMResponse:
    return LLMResponse(
        content=json.dumps({"error": message, "fallback": True}),
        parsed={"error": message, "fallback": True},
        raw={"error": message},
    )


def complete_with_audit(
    db: Session,
    *,
    big_bang_id,
    purpose: str,
    model: str,
    messages: list[dict[str, str]],
    metadata: dict[str, Any] | None = None,
    json_schema: dict[str, Any] | None = None,
) -> tuple[LLMResponse, models.LLMCall]:
    store = ArtifactStore()
    metadata = metadata or {}
    request_payload = {
        "purpose": purpose,
        "model": model,
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
        provider=get_settings().default_llm_provider,
        model=model,
        purpose=purpose,
        status="running",
        request_artifact_id=request_artifact.id,
        meta={"raw_request_artifact_id": str(raw_request_artifact.id), **metadata},
    )
    db.add(call)
    db.flush()
    settings = get_settings()
    attempts: list[dict[str, Any]] = []
    response: LLMResponse | None = None
    last_error: Exception | None = None
    max_retries = max(1, settings.llm_max_retries)
    attempt_messages = messages
    for attempt in range(1, max_retries + 1):
        try:
            response = asyncio.run(
                provider_for_settings().complete(
                    LLMRequest(
                        purpose=purpose,
                        model=model,
                        messages=attempt_messages,
                        json_schema=json_schema,
                        metadata=metadata,
                    )
                )
            )
            if not response.content and not response.parsed:
                raise RuntimeError("LLM response was empty")
            response.parsed = ensure_response_json_object(response)
            attempts.append({"attempt": attempt, "status": "succeeded"})
            break
        except Exception as exc:
            last_error = exc
            response = None
            error_message = "LLM unavailable" if isinstance(exc, LLMProviderUnavailable) else str(exc)
            attempts.append({"attempt": attempt, "status": "failed", "error": error_message})
            if attempt < max_retries:
                if isinstance(exc, LLMJSONParseError):
                    attempt_messages = _json_repair_messages(messages, error_message)
                time.sleep(settings.llm_retry_backoff_seconds * attempt)
    try:
        if response is None:
            if isinstance(last_error, LLMProviderUnavailable):
                raise last_error
            raise RuntimeError(str(last_error) if last_error else "LLM call failed")
        response.parsed = ensure_response_json_object(response)
        response_payload = {
            "content": response.content,
            "parsed": response.parsed,
            "raw": response.raw,
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
        }
        db.flush()
        return response, call
    except Exception as exc:
        error_message = "LLM unavailable" if isinstance(exc, LLMProviderUnavailable) else str(exc)
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
        raise LLMCallError(error_message, call_id=call.id) from exc
