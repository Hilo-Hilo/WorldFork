from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.llm.audit import complete_with_audit
from app.llm.prompt_templates import INITIALIZER_SYSTEM_PROMPT
from app.llm.routing import AuditedLLMRoute


def run_initializer_agent(
    db: Session,
    *,
    big_bang_id,
    scenario_input: dict[str, Any],
    plain_text_corpus: dict[str, Any] | None = None,
    initializer_prompt: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    prompt = initializer_prompt or (
        "Build the complete WorldFork initialization state from the user's full plain-text corpus."
    )
    corpus = plain_text_corpus or {}
    prompt_corpus = initializer_prompt_corpus(corpus)
    prompt_scenario_context = initializer_prompt_scenario_context(
        scenario_input,
        has_prompt_corpus=_has_prompt_source_material(prompt_corpus),
    )
    response, call = complete_with_audit(
        db,
        big_bang_id=big_bang_id,
        purpose=f"initializer_agent_{big_bang_id}",
        model=settings.initializer_agent_model,
        route=AuditedLLMRoute.INITIALIZER_AGENT,
        messages=[
            {
                "role": "system",
                "content": INITIALIZER_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": (
                    f"{prompt}\nScenario metadata/config context: {_prompt_json(prompt_scenario_context)}\n"
                    f"UNTRUSTED plain text corpus and derived summaries: {_prompt_json(prompt_corpus)}"
                ),
            },
        ],
        metadata={"max_tokens": 3400, "temperature": 0.25, "agent_type": "initializer_agent"},
    )
    parsed = response.parsed or {}
    normalized = normalize_initializer_output(parsed, scenario_input)
    normalized["plain_text_corpus"] = corpus
    normalized["llm_call_id"] = str(call.id)
    normalized["model"] = call.model
    return normalized


def initializer_prompt_scenario_context(
    scenario_input: dict[str, Any],
    *,
    has_prompt_corpus: bool,
) -> dict[str, Any]:
    if not isinstance(scenario_input, dict):
        return {}
    context: dict[str, Any] = {}
    raw_scenario = _raw_scenario_text(scenario_input)
    for key, value in scenario_input.items():
        if has_prompt_corpus and key in {"scenario_text", "prompt", "premise"}:
            if not raw_scenario or str(value) == raw_scenario:
                continue
        context[key] = value
    return context


def initializer_prompt_corpus(corpus: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(corpus, dict) or not corpus:
        return {}
    brief = corpus.get("simulation_brief") if isinstance(corpus.get("simulation_brief"), dict) else {}
    mode = brief.get("mode") or corpus.get("mode")
    compact: dict[str, Any] = {
        "raw_char_count": corpus.get("raw_char_count") or brief.get("raw_char_count"),
    }
    if mode == "direct":
        compact["simulation_brief"] = {
            "mode": "direct",
            "text": brief.get("text") or corpus.get("text") or "",
        }
        return _drop_empty_values(compact)
    if mode == "chunked":
        compact["simulation_brief"] = {
            "mode": "chunked",
            "raw_char_count": brief.get("raw_char_count") or corpus.get("raw_char_count"),
            "chunk_count": brief.get("chunk_count") or corpus.get("chunk_count"),
            "chunk_summaries": _compact_chunk_summaries(
                [
                    *_list_value(corpus.get("chunk_summaries")),
                    *_list_value(brief.get("chunk_summaries")),
                ]
            ),
        }
        return _drop_empty_values(compact)
    stripped = _strip_prompt_bookkeeping(corpus)
    if isinstance(stripped, dict):
        stripped.pop("chunk_artifacts", None)
        return _drop_empty_values(stripped)
    return {}


def fallback_initializer_output(scenario_input: dict[str, Any]) -> dict[str, Any]:
    premise = scenario_input.get("premise") or scenario_input.get("prompt") or "The scenario begins."
    return {
        "actors": [
            {
                "name": "Public Cohort",
                "actor_type": "cohort",
                "description": "A broad public group affected by the scenario.",
            },
            {
                "name": "Institutional Actor",
                "actor_type": "institution",
                "description": "An institution responding to the scenario.",
            },
        ],
        "population_archetypes": [
            {"name": "affected_public", "definition": {"premise": premise}},
        ],
        "cohorts": [
            {
                "name": "affected public",
                "actor_name": "Public Cohort",
                "state": {
                    "represented_population": 1000,
                    "stance_axes": {"support": 0.0, "oppose": 0.0, "uncertain": 1.0},
                    "expression_level": 0.25,
                    "attention_level": 0.5,
                    "fatigue": 0.1,
                    "perceived_majority": "unknown",
                    "fear_of_isolation": 0.4,
                    "mobilization_readiness": 0.25,
                    "trust_summary": {"Institutional Actor": 0.45},
                    "dependency_summary": {"Institutional Actor": 0.6},
                },
            },
        ],
        "heroes": [
            {
                "name": "Local Catalyst",
                "actor_type": "hero",
                "definition": {"role": "high-impact individual", "public_reach": 0.45},
                "state": {"attention": 0.5, "fatigue": 0.1, "current_strategy": "observe_and_signal"},
            },
        ],
        "channels": [
            {"name": "OASIS", "surface_type": "social_media"},
        ],
        "initial_events": [
            {
                "title": "Scenario enters public awareness",
                "event_type": "announcement",
                "description": premise,
                "scheduled_tick": 1,
                "expected_impact": {"attention": "increases"},
            }
        ],
        "trait_vectors": [
            {
                "actor_name": "Public Cohort",
                "behavior_axes": {"openness_to_change": 0.5, "source_credulity": 0.5, "mobilization_capacity": 0.4},
                "ideology_axes": {"institutional_trust": 0.45},
                "secrecy": 0.2,
                "trustworthiness": 0.55,
                "reputation": 0.5,
                "tendency": {"public_expression": 0.25, "coordination": 0.35},
            }
        ],
        "graph_edges": _fallback_graph_edges(),
        "emotion_observations": [
            {"actor_name": "Public Cohort", "emotion": "confusion", "value": 5, "source": "initializer_agent"},
            {"actor_name": "Public Cohort", "emotion": "urgency", "value": 4, "source": "initializer_agent"},
        ],
        "sociology_baseline": [
            {"model": "attention_decay", "signal": {"salience": "initial"}},
            {"model": "public_silence", "signal": {"pressure": "normal"}},
        ],
        "sociology_prompt_influences": [
            {"actor_name": "Public Cohort", "influence": {"attention_salience": "initial", "silence_pressure": "normal"}},
        ],
        "branch_hypotheses": [
            {"trigger": "trust collapse", "expected_divergence": "public compliance versus mobilization"},
        ],
        "merge_hypotheses": [
            {"trigger": "shared dependency", "expected_convergence": "moderate public groups coordinate"},
        ],
        "risk_flags": [],
        "fallback": True,
    }


def normalize_initializer_output(parsed: dict[str, Any], scenario_input: dict[str, Any]) -> dict[str, Any]:
    fallback = fallback_initializer_output(scenario_input)
    output = parsed if isinstance(parsed, dict) else {}
    normalized = {
        "actors": _list_or_default(output.get("actors"), fallback["actors"]),
        "simulation_brief": output.get("simulation_brief") or output.get("simulationBrief") or {"summary": scenario_input},
        "population_archetypes": _list_or_default(
            output.get("population_archetypes") or output.get("populationArchetypes"),
            fallback["population_archetypes"],
        ),
        "cohorts": _list_or_default(output.get("cohorts") or output.get("cohort_states"), fallback["cohorts"]),
        "cohort_states": _list_or_default(output.get("cohort_states") or output.get("cohorts"), fallback["cohorts"]),
        "heroes": _list_or_default(output.get("heroes") or output.get("hero_archetypes"), fallback["heroes"]),
        "hero_archetypes": _list_or_default(output.get("hero_archetypes") or output.get("heroes"), fallback["heroes"]),
        "hero_states": _list_or_default(output.get("hero_states"), fallback["heroes"]),
        "trait_vectors": _list_or_default(output.get("trait_vectors") or output.get("traits"), fallback["trait_vectors"]),
        "graph_edges": _list_or_default(output.get("graph_edges") or output.get("graphEdges"), fallback["graph_edges"]),
        "emotion_observations": _list_or_default(
            output.get("emotion_observations") or output.get("emotionObservations"),
            fallback["emotion_observations"],
        ),
        "sociology_baseline": _list_or_default(output.get("sociology_baseline"), fallback["sociology_baseline"]),
        "sociology_prompt_influences": _list_or_default(
            output.get("sociology_prompt_influences"),
            fallback["sociology_prompt_influences"],
        ),
        "channels": _list_or_default(output.get("channels") or output.get("social_surfaces"), fallback["channels"]),
        "initial_events": _list_or_default(output.get("initial_events") or output.get("initialEvents"), fallback["initial_events"]),
        "branch_hypotheses": _list_or_default(output.get("branch_hypotheses"), fallback["branch_hypotheses"]),
        "merge_hypotheses": _list_or_default(output.get("merge_hypotheses"), fallback["merge_hypotheses"]),
        "risk_flags": _list_or_default(output.get("risk_flags"), fallback["risk_flags"]),
    }
    normalized["fallback"] = bool(output.get("fallback")) or parsed.get("error") is not None if isinstance(parsed, dict) else True
    normalized["graph_edges"] = ensure_required_graph_layers(normalized["graph_edges"])
    return normalized


def merge_initializer_lists(generated: list[dict], manual: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for item in generated:
        if isinstance(item, dict):
            merged[_item_name(item)] = item
    for item in manual:
        if isinstance(item, dict):
            merged[_item_name(item)] = item
    return list(merged.values())


def _list_or_default(value, default: list[dict]) -> list[dict]:
    if isinstance(value, list) and value:
        objects = [item for item in value if isinstance(item, dict)]
        return objects or default
    return default


def _item_name(item: dict) -> str:
    return str(item.get("name") or item.get("title") or item).strip().lower()


def ensure_required_graph_layers(edges: list[dict]) -> list[dict]:
    required = ["exposure", "trust", "dependency", "influence", "coalition", "conflict", "oasis_interaction"]
    present = {str(edge.get("layer") or edge.get("graph_layer")) for edge in edges if isinstance(edge, dict)}
    completed = list(edges)
    for layer in required:
        if layer not in present:
            completed.append(
                {
                    "layer": layer,
                    "source_actor_name": "Public Cohort",
                    "target_actor_name": "Institutional Actor",
                    "weight": 0.2 if layer in {"coalition", "conflict"} else 0.5,
                    "reason": f"Initializer fallback seed for {layer} graph.",
                }
            )
    return completed


def _fallback_graph_edges() -> list[dict]:
    return [
        {
            "layer": "dependency",
            "source_actor_name": "Public Cohort",
            "target_actor_name": "Institutional Actor",
            "weight": 0.6,
            "reason": "The public depends on institutional response capacity.",
        },
        {
            "layer": "trust",
            "source_actor_name": "Public Cohort",
            "target_actor_name": "Institutional Actor",
            "weight": 0.45,
            "reason": "Baseline trust starts uncertain.",
        },
        {
            "layer": "exposure",
            "source_actor_name": "Public Cohort",
            "target_actor_name": "Institutional Actor",
            "weight": 0.5,
            "reason": "The public sees institutional announcements.",
        },
        {
            "layer": "influence",
            "source_actor_name": "Institutional Actor",
            "target_actor_name": "Public Cohort",
            "weight": 0.55,
            "reason": "Institutional policy changes constrain public behavior.",
        },
        {
            "layer": "coalition",
            "source_actor_name": "Public Cohort",
            "target_actor_name": "Institutional Actor",
            "weight": 0.2,
            "reason": "No strong coalition exists at initialization.",
        },
        {
            "layer": "conflict",
            "source_actor_name": "Public Cohort",
            "target_actor_name": "Institutional Actor",
            "weight": 0.25,
            "reason": "Baseline disagreement may emerge around the scenario.",
        },
        {
            "layer": "oasis_interaction",
            "source_actor_name": "Public Cohort",
            "target_actor_name": "Institutional Actor",
            "weight": 0.35,
            "reason": "OASIS is the initial public interaction surface.",
        },
    ]


def _compact_chunk_summaries(items: list[Any]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        summary = _strip_prompt_bookkeeping(item.get("summary", item))
        record = _drop_empty_values(
            {
                "chunk_index": item.get("chunk_index"),
                "summary": summary,
            }
        )
        key = _prompt_json(record)
        if key in seen:
            continue
        seen.add(key)
        summaries.append(record)
    return summaries


def _strip_prompt_bookkeeping(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_prompt_bookkeeping(item) for item in value]
    if not isinstance(value, dict):
        return value
    stripped: dict[str, Any] = {}
    for key, item in value.items():
        if _is_prompt_bookkeeping_key(str(key)):
            continue
        stripped[key] = _strip_prompt_bookkeeping(item)
    return stripped


def _is_prompt_bookkeeping_key(key: str) -> bool:
    return (
        key in {"artifact_id", "llm_call_id"}
        or key.endswith("_artifact_id")
        or key.endswith("_llm_call_id")
    )


def _drop_empty_values(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, [], {})}


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _has_prompt_source_material(corpus: dict[str, Any]) -> bool:
    brief = corpus.get("simulation_brief") if isinstance(corpus.get("simulation_brief"), dict) else {}
    if isinstance(brief, dict):
        return bool(brief.get("text") or brief.get("chunk_summaries"))
    return bool(corpus)


def _raw_scenario_text(scenario_input: dict[str, Any]) -> str:
    for key in ("scenario_text", "prompt", "premise"):
        value = scenario_input.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _prompt_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=str)
