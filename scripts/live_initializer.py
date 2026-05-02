"""Live end-to-end test of initialize_big_bang against real OpenAI Codex.

Prefer the CLI initialization path:
    worldfork init --name "Live initializer" --scenario "<scenario text>"

Requires:
    - OpenAI Codex OAuth via `worldfork settings openai-codex-login`,
      `codex login --device-auth`, or OPENAI_CODEX_OAUTH_TOKEN
    - fakeredis installed (for fake Redis-backed ProviderRateLimiter)
    - aiosqlite installed (for in-memory SQLite async engine)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

CODEX_MODEL = "gpt-5.4"

# ---------------------------------------------------------------------------
# Bootstrap .env before importing anything that reads settings
# ---------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parents[1]
_ENV = _REPO / ".env"
if _ENV.exists():
    for _line in _ENV.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip())

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s %(message)s",
    stream=sys.stderr,
)
# Reduce noise from httpx / openai
for _noisy in ("httpx", "openai", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.ERROR)

# ---------------------------------------------------------------------------
# SQLite shim — must run before any ORM model is imported
# ---------------------------------------------------------------------------
from sqlalchemy import JSON  # noqa: E402
from sqlalchemy.dialects.postgresql import ARRAY, JSONB  # noqa: E402

import backend.app.models  # noqa: E402,F401 — trigger all submodule imports
from backend.app.models.base import Base  # noqa: E402 registers all models via __init__


def _patch_sqlite_types() -> None:
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, (JSONB, ARRAY)):
                col.type = JSON()

_patch_sqlite_types()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    import fakeredis.aioredis
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from backend.app.providers import clear_registry, register_provider
    from backend.app.providers.openai_codex import OpenAICodexProvider
    from backend.app.providers.rate_limits import ProviderRateLimiter
    from backend.app.providers.routing import RoutingTable
    from backend.app.schemas.settings import ModelRoutingEntry
    from backend.app.simulation.initializer import InitializerInput, initialize_big_bang
    from backend.app.storage.sot_loader import load_sot

    print("=" * 60)
    print("WorldFork Big Bang — live end-to-end test")
    print("=" * 60)

    # ---- 1. DB setup (in-memory SQLite) ------------------------------------
    print("\n[1] Setting up in-memory SQLite DB ...")
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    print("    DB created OK")

    # ---- 2. SoT ------------------------------------------------------------
    print("\n[2] Loading SoT ...")
    sot = load_sot()
    print(f"    SoT version={sot.version!r} sha256={sot.snapshot_sha256[:16]}...")

    # ---- 3. Provider -------------------------------------------------------
    print("\n[3] Constructing OpenAICodexProvider ...")
    clear_registry()
    provider = OpenAICodexProvider(
        oauth_token=os.environ.get("OPENAI_CODEX_OAUTH_TOKEN"),
        codex_auth_file=os.environ.get("OPENAI_CODEX_AUTH_FILE"),
        base_url=os.environ.get("OPENAI_CODEX_BASE_URL", "https://chatgpt.com/backend-api/codex"),
        default_model=CODEX_MODEL,
        fallback_model=CODEX_MODEL,
    )
    if not provider.has_oauth_token():
        print(
            "ERROR: OpenAI Codex OAuth token not found. Run "
            "`worldfork settings openai-codex-login`, run `codex login --device-auth`, "
            "or set OPENAI_CODEX_OAUTH_TOKEN."
        )
        sys.exit(1)
    register_provider("openai-codex", provider)
    print("    Provider registered")

    # ---- 4. Rate limiter (fakeredis) ---------------------------------------
    print("\n[4] Constructing fakeredis ProviderRateLimiter ...")
    fake_redis = fakeredis.aioredis.FakeRedis()
    limiter = ProviderRateLimiter(
        fake_redis,
        provider="openai-codex",
        rpm_limit=60,
        tpm_limit=150_000,
        max_concurrency=4,
        burst_multiplier=1.2,
    )
    print("    Rate limiter ready")

    # ---- 5. Routing table — pin initialize_big_bang to the approved live model ---
    print(f"\n[5] Building RoutingTable with {CODEX_MODEL} for initialize_big_bang ...")
    routing = RoutingTable.defaults()
    routing._entries["initialize_big_bang"] = ModelRoutingEntry(
        job_type="initialize_big_bang",
        preferred_provider="openai-codex",
        preferred_model=CODEX_MODEL,
        fallback_provider="openai-codex",
        fallback_model=CODEX_MODEL,
        temperature=0.6,
        top_p=0.95,
        max_tokens=16384,  # big bang output is large; 4096 causes truncation
        max_concurrency=4,
        requests_per_minute=60,
        tokens_per_minute=150_000,
        timeout_seconds=120,
        retry_policy="exponential_backoff",
        daily_budget_usd=None,
    )
    print("    Routing table ready")

    # ---- 6. Build InitializerInput ----------------------------------------
    scenario_text = (
        "A mid-sized US college town debates a proposed 2-year ban on new short-term rentals. "
        "City council split. Local landlords, long-term residents, students, and tourism workers all weigh in."
    )
    inp = InitializerInput(
        scenario_text=scenario_text,
        display_name="Short-Term Rental Ban Debate (Live Test)",
        time_horizon_label="2 weeks",
        tick_duration_minutes=240,
        max_ticks=6,
        max_schedule_horizon_ticks=5,
    )

    # ---- 7. Run initialize_big_bang ----------------------------------------
    print("\n[6] Running initialize_big_bang (live LLM call) ...")
    run_root = _REPO / "runs"
    run_root.mkdir(exist_ok=True)

    async with session_factory() as session:
        try:
            result = await initialize_big_bang(
                inp,
                session=session,
                sot=sot,
                provider_rate_limiter=limiter,
                run_root=run_root,
                routing=routing,
            )
        except Exception as exc:
            print(f"\nERROR [{type(exc).__name__}]: {exc}")
            # Print extra detail for known error types
            report = getattr(exc, "report", None)
            if report:
                print(f"  Validation report: {report}")
            raise

    # ---- 8. Print results --------------------------------------------------
    bbr = result.big_bang_run
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"  BigBangRun.status : {bbr.status}")
    print(f"  run_folder        : {result.run_folder}")
    print(f"  archetypes count  : {len(result.archetypes)}")
    print(f"  heroes count      : {len(result.heroes)}")
    print(f"  channels count    : {len(result.channels)}")
    print(f"  events count      : {len(result.initial_events)}")

    if result.archetypes:
        print("\n  First 2 archetypes:")
        for arch in result.archetypes[:2]:
            print(f"    - label={arch.label!r}  population_total={arch.population_total}")

    # ---- 9. Token / cost from LLM artifact in ledger ----------------------
    # The LLM result is persisted to the run ledger; read from the raw response
    # artifact which was written by the initializer.
    raw_response_path = result.run_folder / "initialization" / "initializer_response_raw.json"
    total_tokens = 0
    approx_cost = 0.0
    if raw_response_path.exists():
        import json
        raw_data = json.loads(raw_response_path.read_text())
        total_tokens = raw_data.get("total_tokens", 0) or 0
        approx_cost = raw_data.get("cost_usd") or 0.0
    print(f"\n  Total LLM tokens  : {total_tokens}")
    print(f"  Approx cost (USD) : ${approx_cost:.5f}")

    # ---- 10. Run folder tree ----------------------------------------------
    print("\n  Run folder structure (depth 2):")
    run_folder = Path(result.run_folder)
    for root, dirs, files in os.walk(run_folder):
        # Limit depth
        depth = len(Path(root).relative_to(run_folder).parts)
        if depth > 2:
            dirs.clear()
            continue
        indent = "    " + "  " * depth
        print(f"{indent}{Path(root).name}/")
        if depth < 2:
            for f in sorted(files):
                print(f"{indent}  {f}")

    # ---- 11. Assertions ----------------------------------------------------
    print("\n" + "=" * 60)
    print("ASSERTIONS")
    assert len(result.archetypes) >= 3, (
        f"Expected >=3 archetypes, got {len(result.archetypes)}"
    )
    assert len(result.initial_events) >= 1, (
        f"Expected >=1 event, got {len(result.initial_events)}"
    )
    print("  [PASS] >= 3 archetypes")
    print("  [PASS] >= 1 event")
    print("=" * 60)
    print("\nAll assertions passed. Big Bang initializer succeeded!")

    await engine.dispose()
    await fake_redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
