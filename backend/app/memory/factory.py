"""
Memory provider factory — returns a cached MemoryProvider singleton.

Priority:
  1. If ZepConfig.enabled and ZEP_API_KEY is set: ZepMemoryProvider with LocalMemoryProvider fallback.
  2. Otherwise: LocalMemoryProvider.

Call reload_memory_provider() to invalidate the singleton (e.g. after settings change).
"""
from __future__ import annotations

import os

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
    """Construct the appropriate provider based on current settings."""
    from backend.app.core.config import settings

    # Attempt to load ZepConfig from the settings table.
    # If unavailable (no DB, no row), fall back to environment settings.
    try:
        zep_cfg = _load_zep_config()
    except Exception:
        zep_cfg = None

    desired_enabled = zep_cfg.enabled if zep_cfg is not None else settings.zep_enabled
    if not desired_enabled:
        return LocalMemoryProvider()

    api_key_env = zep_cfg.api_key_env if zep_cfg is not None else "ZEP_API_KEY"
    api_key = settings.zep_api_key or os.environ.get(api_key_env, "")

    if not api_key:
        return LocalMemoryProvider()

    mode = zep_cfg.mode if zep_cfg is not None else "cohort_memory"

    from backend.app.memory.zep_adapter import ZepMemoryProvider

    return ZepMemoryProvider(
        api_key=api_key,
        mode=mode,
        local_fallback=LocalMemoryProvider(),
    )


def _load_zep_config(session_factory=None):
    """Try to load ZepConfig from the DB settings row. Returns None on any error."""
    try:
        from backend.app.schemas.settings import ZepConfig
        from backend.app.core.db import SyncSessionLocal
        from backend.app.models.settings import ZepSettingModel

        factory = session_factory or SyncSessionLocal
        with factory() as session:
            row = session.get(ZepSettingModel, "default")
            if row is None:
                return None
            return ZepConfig(
                enabled=bool(row.enabled),
                mode=row.mode,
                api_key_env=row.api_key_env,
                cache_ttl_seconds=row.cache_ttl_seconds,
                degraded=bool(row.degraded),
            )
    except Exception:
        return None


async def reload_memory_provider() -> None:
    """Invalidate the singleton so the next call to get_memory() rebuilds it."""
    global _provider_singleton
    _provider_singleton = None
