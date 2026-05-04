"""Memory provider factory — returns a cached MemoryProvider singleton.

Call reload_memory_provider() to invalidate the singleton (e.g. after settings change).
"""
from __future__ import annotations

from backend.app.memory.base import MemoryProvider
from backend.app.memory.local import LocalMemoryProvider

_provider_singleton: MemoryProvider | None = None


def get_memory() -> MemoryProvider:
    """Return the cached memory provider singleton, creating it if needed."""
    global _provider_singleton
    if _provider_singleton is not None:
        return _provider_singleton
    _provider_singleton = _build_provider()
    return _provider_singleton


def _build_provider() -> MemoryProvider:
    """Construct the configured provider."""
    return LocalMemoryProvider()


async def reload_memory_provider() -> None:
    """Invalidate the singleton so the next call to get_memory() rebuilds it."""
    global _provider_singleton
    _provider_singleton = None
