from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.domains.big_bang import initializer_agent
from app.llm.schemas import LLMResponse


def _capture_initializer_user_message(monkeypatch, *, scenario_input: dict, plain_text_corpus: dict) -> str:
    captured: dict[str, list[dict[str, str]]] = {}

    def fake_complete_with_audit(
        db,
        *,
        big_bang_id,
        purpose,
        model,
        messages,
        metadata,
        json_schema=None,
        route=None,
    ):
        captured["messages"] = messages
        return LLMResponse(content="{}", parsed={}, raw={}), SimpleNamespace(id=uuid4(), model=model or "test-model")

    monkeypatch.setattr(initializer_agent, "complete_with_audit", fake_complete_with_audit)

    initializer_agent.run_initializer_agent(
        object(),
        big_bang_id=uuid4(),
        scenario_input=scenario_input,
        plain_text_corpus=plain_text_corpus,
    )

    return captured["messages"][1]["content"]


def test_initializer_prompt_does_not_duplicate_direct_scenario_text(monkeypatch):
    scenario_text = "Harbor operators, nurses, and freight dispatchers dispute a new evacuation protocol."

    user_message = _capture_initializer_user_message(
        monkeypatch,
        scenario_input={
            "scenario_text": scenario_text,
            "prompt": scenario_text,
            "max_ticks": 12,
            "tick_duration": "1 day",
        },
        plain_text_corpus={
            "raw_text_artifact_id": "raw-artifact-id",
            "raw_char_count": len(scenario_text),
            "simulation_brief": {"mode": "direct", "text": scenario_text, "chunk_summaries": []},
        },
    )

    assert user_message.count(scenario_text) == 1
    assert "max_ticks" in user_message
    assert "tick_duration" in user_message


def test_initializer_prompt_omits_storage_and_audit_bookkeeping(monkeypatch):
    user_message = _capture_initializer_user_message(
        monkeypatch,
        scenario_input={"max_ticks": 20},
        plain_text_corpus={
            "raw_text_artifact_id": "raw-artifact-id",
            "simulation_brief_artifact_id": "brief-artifact-id",
            "raw_char_count": 24000,
            "chunk_artifacts": [{"index": 0, "artifact_id": "chunk-artifact-id"}],
            "chunk_summaries": [
                {
                    "chunk_index": 0,
                    "llm_call_id": "chunk-llm-call-id",
                    "artifact_id": "summary-artifact-id",
                    "summary": {
                        "entities": ["Harbor Board"],
                        "events": ["Emergency ferry schedule"],
                    },
                }
            ],
            "simulation_brief": {
                "mode": "chunked",
                "raw_text_artifact_id": "raw-artifact-id",
                "raw_char_count": 24000,
                "chunk_count": 1,
                "chunk_summaries": [
                    {
                        "chunk_index": 0,
                        "llm_call_id": "chunk-llm-call-id",
                        "artifact_id": "summary-artifact-id",
                        "summary": {"groups": ["Night-shift nurses"]},
                    }
                ],
            },
        },
    )

    assert "Harbor Board" in user_message
    assert "Night-shift nurses" in user_message
    assert "raw-artifact-id" not in user_message
    assert "brief-artifact-id" not in user_message
    assert "chunk-artifact-id" not in user_message
    assert "chunk-llm-call-id" not in user_message
    assert "summary-artifact-id" not in user_message
