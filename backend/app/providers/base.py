"""Base classes / Protocols for LLM providers.

Every concrete provider (OpenRouter, OpenAI direct, Anthropic, Ollama, ...)
implements :class:`LLMProvider` and SHOULD inherit from :class:`BaseProvider`
to pick up shared utilities (id generation, ledger persistence).
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Protocol, runtime_checkable

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
