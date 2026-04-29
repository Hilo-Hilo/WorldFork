from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CacheKey:
    """Stable identifier for prompt/LLM cache lookups."""

    namespace: str
    key: str
    version: str = "v1"

    def as_string(self) -> str:
        return f"{self.namespace}:{self.version}:{self.key}"
