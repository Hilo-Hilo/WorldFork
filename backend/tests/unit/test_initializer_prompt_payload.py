from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.core.config import Settings
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
        json_response_transform=None,
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


def _capture_initializer_json_schema(monkeypatch) -> dict:
    captured: dict[str, dict] = {}

    def fake_complete_with_audit(
        db,
        *,
        big_bang_id,
        purpose,
        model,
        messages,
        metadata,
        json_schema=None,
        json_response_transform=None,
        route=None,
    ):
        captured["json_schema"] = json_schema
        return LLMResponse(content="{}", parsed={}, raw={}), SimpleNamespace(id=uuid4(), model=model or "test-model")

    monkeypatch.setattr(initializer_agent, "complete_with_audit", fake_complete_with_audit)

    initializer_agent.run_initializer_agent(
        object(),
        big_bang_id=uuid4(),
        scenario_input={"premise": "A public crisis forces institutional decisions."},
        plain_text_corpus={},
    )

    return captured["json_schema"]


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


def test_initializer_call_requires_endpoint_questions_and_ledger_schema(monkeypatch):
    schema = _capture_initializer_json_schema(monkeypatch)

    assert "important_questions" in schema["required"]
    assert "endpoint_ledger" in schema["required"]
    assert "population_archetypes" in schema["required"]
    assert "cohort_states" in schema["required"]
    assert schema["properties"]["important_questions"]["maxItems"] == 5
    assert schema["properties"]["endpoint_ledger"]["maxItems"] == 5
    assert "population_total" in schema["properties"]["population_archetypes"]["items"]["required"]
    assert {
        "represented_population",
        "population_share_of_archetype",
        "representation_mode",
    } <= set(schema["properties"]["cohort_states"]["items"]["required"])
    assert {"endpoint_key", "label", "status", "realization_criteria"} <= set(
        schema["properties"]["endpoint_ledger"]["items"]["required"]
    )


def test_initializer_default_chunk_budget_is_at_least_64k_token_equivalent():
    settings = Settings()

    assert settings.initializer_direct_context_char_budget >= 64_000 * 4
    assert settings.initializer_chunk_chars >= 64_000 * 4


def test_initializer_output_preserves_endpoint_questions_and_parseable_ledger():
    normalized = initializer_agent.normalize_initializer_output(
        {
            "important_questions": [
                "Does institutional transparency restore public trust?",
                "Do community clinics accept ACCS allocation decisions?",
                "Does mutual aid complement or replace formal authority?",
                "Do courts constrain emergency command powers?",
                "Does rumor correction prevent mobilization?",
                "This sixth question should be dropped.",
            ],
            "endpoint_ledger": [
                {
                    "endpoint_key": "institutional_repair",
                    "label": "Institutional repair",
                    "description": "Formal institutions restore legitimacy through auditable allocation.",
                    "status": "active",
                    "realization_criteria": [
                        "ACCS decisions are publicly auditable.",
                        "Clinics and mutual-aid actors accept the allocation process.",
                    ],
                    "authority_refs": ["Atlas Regional Council", "Emergency Court Panel"],
                    "evidence_refs": ["scenario:ACCS", "scenario:trust"],
                    "blockers": ["data-smoothing scandal"],
                    "rationale": "This endpoint answers the primary trust-repair question.",
                }
            ],
        },
        {"premise": "Atlas crisis"},
    )

    assert normalized["important_questions"] == [
        "Does institutional transparency restore public trust?",
        "Do community clinics accept ACCS allocation decisions?",
        "Does mutual aid complement or replace formal authority?",
        "Do courts constrain emergency command powers?",
        "Does rumor correction prevent mobilization?",
    ]
    assert normalized["endpoint_ledger"] == [
        {
            "endpoint_key": "institutional_repair",
            "label": "Institutional repair",
            "description": "Formal institutions restore legitimacy through auditable allocation.",
            "status": "active",
            "probability": None,
            "realization_criteria": [
                "ACCS decisions are publicly auditable.",
                "Clinics and mutual-aid actors accept the allocation process.",
            ],
            "authority_refs": ["Atlas Regional Council", "Emergency Court Panel"],
            "evidence_refs": ["scenario:ACCS", "scenario:trust"],
            "negative_evidence_refs": [],
            "blockers": ["data-smoothing scandal"],
            "status_basis": "initializer_endpoint_ledger",
            "contradiction_notes": "Track later evidence that supports, weakens, eliminates, or realizes this endpoint.",
            "rationale": "This endpoint answers the primary trust-repair question.",
            "last_observed_tick_index": None,
            "meta": {
                "source": "initializer_endpoint_ledger",
                "important_question": "Does institutional transparency restore public trust?",
            },
        }
    ]


def test_initializer_output_preserves_scenario_branch_hypotheses_first():
    normalized = initializer_agent.normalize_initializer_output(
        {
            "branch_hypotheses": [
                {
                    "label": "generic market surprise",
                    "trigger": "market chatter changes",
                    "candidate_endpoint_id": "maybe",
                }
            ],
        },
        {
            "premise": "Forecast-card scenario",
            "branch_hypotheses": [
                {
                    "label": "YES candidate wins",
                    "trigger": "official settlement evidence supports the yes endpoint",
                    "candidate_endpoint_id": "yes",
                    "expected_divergence": "The primary binary endpoint resolves yes.",
                },
                {
                    "label": "NO candidate wins",
                    "trigger": "official settlement evidence supports the no endpoint",
                    "candidate_endpoint_id": "no",
                    "expected_divergence": "The primary binary endpoint resolves no.",
                },
            ],
        },
    )

    candidate_ids = [item.get("candidate_endpoint_id") for item in normalized["branch_hypotheses"][:2]]
    assert candidate_ids == ["yes", "no"]
    assert normalized["branch_hypotheses"][2]["candidate_endpoint_id"] == "maybe"
