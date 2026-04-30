"""Live smoke test against real OpenRouter.

Prefer the maintained CLI smoke: worldfork smoke live
Requires OPENROUTER_API_KEY in .env. Zep is intentionally disabled by default.
"""
import asyncio

from backend.app.core.config import settings
from backend.app.memory.local import LocalMemoryProvider
from backend.app.providers.openrouter import OpenRouterProvider
from backend.app.schemas.llm import Clock, ModelConfig, PromptPacket


async def main():
    print("== WorldFork Live Smoke ==")
    # 1. Provider healthcheck
    print("\n[1] OpenRouter healthcheck")
    provider = OpenRouterProvider(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        default_model=settings.default_model,
        fallback_model=settings.fallback_model,
        http_referer=settings.openrouter_http_referer,
        x_title=settings.openrouter_title,
    )
    health = await provider.healthcheck()
    print(f"  ok={health.ok} latency_ms={health.latency_ms}")
    if health.details:
        print(f"  details={health.details}")
    assert health.ok, f"healthcheck failed: {health}"

    # 2. Tiny structured generation
    print(f"\n[2] Structured generation ({settings.default_model})")
    packet = PromptPacket(
        system="You are a concise assistant. Return JSON: {\"answer\": string, \"confidence\": number}.",
        clock=Clock(current_tick=0, tick_duration_minutes=60, elapsed_minutes=0,
                    previous_tick_minutes=None, max_schedule_horizon_ticks=5),
        actor_id="smoke",
        actor_kind="god",
        archetype=None,
        state={"prompt": "What color is the sky on a clear day? One word."},
        sot_excerpt={},
        visible_feed=[],
        visible_events=[],
        own_queued_events=[],
        own_recent_actions=[],
        retrieved_memory=None,
        allowed_tools=[],
        output_schema_id="generic",
        temperature=0.3,
        metadata={},
    )
    cfg = ModelConfig(
        provider="openrouter",
        model=settings.default_model,
        fallback_model=None,
        temperature=0.3,
        top_p=1.0,
        max_tokens=100,
        response_format={"type":"json_object"},
        tools=None,
        timeout_seconds=30,
        retry_policy="exponential_backoff",
    )
    result = await provider.generate_structured(packet, cfg)
    print(f"  model_used={result.model_used} tokens={result.total_tokens} cost=${result.cost_usd or 0:.5f}")
    print(f"  parsed={result.parsed_json}")
    assert result.parsed_json is not None
    assert "answer" in result.parsed_json

    # 3. Local memory roundtrip. Zep is never used unless explicitly enabled.
    if settings.zep_enabled and settings.zep_api_key and settings.zep_api_key != "z_REPLACE_ME":
        print("\n[3] Zep memory roundtrip")
        import uuid

        from backend.app.memory.zep_adapter import ZepMemoryProvider
        local_fb = LocalMemoryProvider()
        zep = ZepMemoryProvider(api_key=settings.zep_api_key, mode="cohort_memory", local_fallback=local_fb)
        h = await zep.healthcheck()
        print(f"  zep healthcheck ok={h['ok']} latency_ms={h.get('latency_ms')}")
        if h["ok"]:
            aid = f"smoke-cohort-{uuid.uuid4().hex[:8]}"
            uid = f"smoke-universe-{uuid.uuid4().hex[:8]}"
            await zep.ensure_user(actor_id=aid, actor_kind="cohort", metadata={"smoke": True})
            sid = await zep.ensure_session(actor_id=aid, universe_id=uid, metadata={"smoke": True})
            await zep.add_episode(session_id=sid, role="cohort_smoke", role_type="user",
                                  content="Smoke test: this is a tick-1 thought.")
            ctx = await zep.get_context(session_id=sid, max_tokens=200)
            print(f"  context_len={len(ctx)} chars")
            print(f"  context_excerpt={ctx[:120]!r}")
    else:
        print("\n[3] Zep skipped (disabled); local memory active")
        local = LocalMemoryProvider()
        health = await local.healthcheck()
        assert health.get("ok", False)

    print("\n== Smoke OK ==")

if __name__ == "__main__":
    asyncio.run(main())
