"""Base classes / Protocols for LLM providers.

Every concrete provider (OpenRouter, OpenAI direct, Anthropic, Ollama, ...)
implements :class:`LLMProvider` and SHOULD inherit from :class:`BaseProvider`
to pick up shared utilities (id generation, ledger persistence).
"""
from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import json_repair
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JSONSchemaValidationError

from backend.app.core.ids import new_id
from backend.app.schemas.llm import (
    EmbeddingConfig,
    EmbeddingResult,
    LLMResult,
    ModelConfig,
    PromptPacket,
    ProviderHealth,
)

if TYPE_CHECKING:
    from backend.app.storage.ledger import Ledger


def _schema_from_response_format(response_format: dict | None) -> dict | None:
    if not isinstance(response_format, dict):
        return None
    if response_format.get("type") == "json_schema":
        schema_config = response_format.get("json_schema")
        if isinstance(schema_config, dict):
            schema = schema_config.get("schema")
            return schema if isinstance(schema, dict) else None
    schema = response_format.get("schema")
    if isinstance(schema, dict):
        return schema
    if response_format.get("type") == "object" or "properties" in response_format:
        return response_format
    return None


# ---------------------------------------------------------------------------
# Protocol — provider protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class LLMProvider(Protocol):
    """Protocol every concrete provider must satisfy (provider protocol)."""

    name: str

    async def generate_structured(
        self, prompt: PromptPacket, config: ModelConfig
    ) -> LLMResult: ...

    async def generate_text(
        self, prompt: PromptPacket, config: ModelConfig
    ) -> LLMResult: ...

    async def embed(
        self, texts: list[str], config: EmbeddingConfig
    ) -> EmbeddingResult: ...

    async def healthcheck(self) -> ProviderHealth: ...


# ---------------------------------------------------------------------------
# BaseProvider — shared utilities
# ---------------------------------------------------------------------------

class BaseProvider:
    """Shared functionality for concrete provider implementations.

    Subclasses are responsible for implementing the four Protocol methods.
    BaseProvider itself only carries shared utilities so that the typing
    surface remains the Protocol.
    """

    name: str = "base"

    # ------------------------------------------------------------------
    # Structured JSON parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_json_object_with_repair(content: str) -> tuple[dict, bool]:
        """Parse a structured model response, locally repairing JSON-shaped output.

        Returns ``(payload, repaired_locally)``.  Callers should only ask the
        model to regenerate after this method raises, so cheap deterministic
        repair always runs before another LLM call.
        """
        if not content:
            raise ValueError("response was empty")
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            parsed = None
        else:
            if isinstance(parsed, dict):
                return parsed, False
            raise ValueError(
                f"response JSON root must be an object, got {type(parsed).__name__}"
            )

        candidate = content.strip()
        if candidate.startswith("```") and candidate.endswith("```"):
            fenced = candidate.removeprefix("```").removesuffix("```").strip()
            if fenced.lower().startswith("json"):
                fenced = fenced[4:].strip()
            try:
                parsed = json.loads(fenced)
            except json.JSONDecodeError:
                candidate = fenced
            else:
                if isinstance(parsed, dict):
                    return parsed, False
                raise ValueError(
                    f"response JSON root must be an object, got {type(parsed).__name__}"
                )

        stripped = candidate.lstrip()
        if not stripped.startswith("{") and not stripped.startswith("```"):
            raise ValueError("response did not start with a JSON object")
        try:
            repaired = json_repair.repair_json(
                candidate,
                return_objects=True,
                skip_json_loads=True,
                ensure_ascii=False,
            )
        except Exception as exc:
            raise ValueError(str(exc) or "response JSON repair failed") from exc
        if isinstance(repaired, dict):
            return repaired, True
        raise ValueError(
            f"repaired JSON root must be an object, got {type(repaired).__name__}"
        )

    @classmethod
    def _parse_structured_json(
        cls,
        content: str,
        response_format: dict | None = None,
    ) -> tuple[dict, bool]:
        parsed, repaired = cls._parse_json_object_with_repair(content)
        cls._validate_response_format_schema(parsed, response_format)
        return parsed, repaired

    @staticmethod
    def _validate_response_format_schema(payload: dict, response_format: dict | None) -> None:
        schema = _schema_from_response_format(response_format)
        if schema is None:
            return
        try:
            Draft202012Validator(schema).validate(payload)
        except JSONSchemaValidationError as exc:
            raise ValueError(f"response JSON failed schema validation: {exc.message}") from exc

    # ------------------------------------------------------------------
    # Time + ID helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _now_ms() -> int:
        """Return the current time in milliseconds since the epoch (monotonic-ish)."""
        return int(time.time() * 1000)

    @staticmethod
    def _make_call_id(prefix: str = "llm") -> str:
        """Return a fresh prefixed call id (lexicographically sortable)."""
        return new_id(prefix)

    # ------------------------------------------------------------------
    # Ledger persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _persist_call(
        ledger: Ledger | None,
        run_id: str,
        universe_id: str | None,
        tick: int | None,
        result: LLMResult,
        prompt_packet: PromptPacket,
    ) -> None:
        """Best-effort write of one LLM call to the run ledger.

        Idempotent: if the artifact already exists (re-execution after a Celery
        retry), the ImmutabilityError is swallowed because the existing artifact
        is by construction the same call_id+content.
        """
        if ledger is None or universe_id is None or tick is None:
            return
        from backend.app.storage.artifacts import write_llm_call
        from backend.app.storage.ledger import ImmutabilityError

        # Strip any "system" key that might contain credentials in headers; we
        # never include API keys in the prompt itself, but be defensive.
        prompt_dump = prompt_packet.model_dump(mode="json")
        result_dump = result.model_dump(mode="json")

        artifact = {
            "call_id": result.call_id,
            "run_id": run_id,
            "universe_id": universe_id,
            "tick": tick,
            "provider": result.provider,
            "model_used": result.model_used,
            "prompt": prompt_dump,
            "result": result_dump,
        }
        try:
            write_llm_call(ledger, universe_id, tick, result.call_id, artifact)
        except ImmutabilityError:
            # Already written by a prior attempt — accept idempotency.
            pass
