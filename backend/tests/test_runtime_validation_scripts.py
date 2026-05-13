import argparse

from scripts import full_runtime_smoke, run_test_big_bang


LLM_CONFIG = {
    "effective_model_routing": [
        {
            "route": "cohort_agent",
            "preferred_provider": "openrouter",
            "preferred_model": "deepseek/deepseek-v4-pro",
            "fallback_provider": "openrouter-claude",
            "fallback_model": "anthropic/claude-sonnet-4.5",
        },
        {
            "route": "report_agent",
            "preferred_provider": "openrouter-openai",
            "preferred_model": "openai/gpt-5.4",
            "fallback_provider": None,
            "fallback_model": None,
        },
    ]
}


def test_demo_expected_pairs_default_to_effective_llm_routing(monkeypatch) -> None:
    monkeypatch.delenv("WORLDFORK_DEMO_EXPECTED_PAIRS", raising=False)
    args = argparse.Namespace(expected_provider=None, expected_model=None)

    assert run_test_big_bang._expected_pairs(args, LLM_CONFIG) == {
        ("openrouter", "deepseek/deepseek-v4-pro"),
        ("openrouter-claude", "anthropic/claude-sonnet-4.5"),
        ("openrouter-openai", "openai/gpt-5.4"),
    }


def test_demo_explicit_expected_pair_overrides_effective_llm_routing(monkeypatch) -> None:
    monkeypatch.delenv("WORLDFORK_DEMO_EXPECTED_PAIRS", raising=False)
    args = argparse.Namespace(expected_provider="openrouter", expected_model="qwen/qwen3-coder")

    assert run_test_big_bang._expected_pairs(args, LLM_CONFIG) == {
        ("openrouter", "qwen/qwen3-coder")
    }


def test_smoke_expected_pairs_default_to_effective_llm_routing(monkeypatch) -> None:
    monkeypatch.delenv("WORLDFORK_SMOKE_EXPECTED_PROVIDER_MODELS", raising=False)

    assert full_runtime_smoke.expected_provider_models(LLM_CONFIG) == {
        ("openrouter", "deepseek/deepseek-v4-pro"),
        ("openrouter-claude", "anthropic/claude-sonnet-4.5"),
        ("openrouter-openai", "openai/gpt-5.4"),
    }


def test_demo_provider_availability_accepts_catalog_alias_without_codex_ready() -> None:
    llm_config = {
        "provider_catalog": [
            {
                "provider": "openrouter-claude",
                "enabled": True,
                "configured": True,
                "supported": True,
            }
        ]
    }

    run_test_big_bang.assert_expected_providers_available(
        llm_config,
        {("openrouter-claude", "anthropic/claude-sonnet-4.5")},
        ready={"checks": {"database": True, "redis": True}},
    )


def test_smoke_provider_availability_accepts_catalog_alias_without_codex_ready() -> None:
    llm_config = {
        "provider_catalog": [
            {
                "provider": "openrouter-claude",
                "enabled": True,
                "configured": True,
                "supported": True,
            }
        ]
    }

    full_runtime_smoke.assert_expected_providers_available(
        llm_config,
        {("openrouter-claude", "anthropic/claude-sonnet-4.5")},
        ready={"checks": {"database": True, "redis": True}},
    )
