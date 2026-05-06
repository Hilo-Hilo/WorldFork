from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.llm.audit import complete_with_audit
from app.llm.prompt_templates import INITIALIZER_SYSTEM_PROMPT
from app.llm.routing import AuditedLLMRoute

INITIALIZER_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "simulation_brief": {"type": ["object", "string"]},
        "actors": {"type": "array", "items": {"type": "object"}},
        "population_archetypes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "archetype_id": {"type": "string"},
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                    "population_total": {"type": "integer", "minimum": 1},
                    "definition": {"type": "object"},
                },
                "required": ["population_total"],
                "additionalProperties": True,
            },
        },
        "cohort_states": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "actor_name": {"type": "string"},
                    "archetype_id": {"type": "string"},
                    "represented_population": {"type": "integer", "minimum": 0},
                    "population_share_of_archetype": {"type": "number", "minimum": 0, "maximum": 1},
                    "representation_mode": {"type": "string"},
                    "state": {"type": "object"},
                },
                "required": ["represented_population", "population_share_of_archetype", "representation_mode"],
                "additionalProperties": True,
            },
        },
        "hero_archetypes": {"type": "array", "items": {"type": "object"}},
        "hero_states": {"type": "array", "items": {"type": "object"}},
        "trait_vectors": {"type": "array", "items": {"type": "object"}},
        "graph_edges": {"type": "array", "items": {"type": "object"}},
        "emotion_observations": {"type": "array", "items": {"type": "object"}},
        "sociology_baseline": {"type": "array", "items": {"type": "object"}},
        "sociology_prompt_influences": {"type": "array", "items": {"type": "object"}},
        "channels": {"type": "array", "items": {"type": "object"}},
        "initial_events": {"type": "array", "items": {"type": "object"}},
        "branch_hypotheses": {"type": "array", "items": {"type": "object"}},
        "merge_hypotheses": {"type": "array", "items": {"type": "object"}},
        "important_questions": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": {"type": "string"},
        },
        "endpoint_ledger": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "endpoint_key": {"type": "string"},
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                    "status": {"type": "string"},
                    "probability": {"type": ["number", "null"]},
                    "realization_criteria": {"type": "array", "items": {"type": "string"}},
                    "authority_refs": {"type": "array"},
                    "evidence_refs": {"type": "array"},
                    "negative_evidence_refs": {"type": "array"},
                    "blockers": {"type": "array"},
                    "status_basis": {"type": "string"},
                    "contradiction_notes": {"type": "string"},
                    "rationale": {"type": "string"},
                    "last_observed_tick_index": {"type": ["integer", "null"]},
                    "meta": {"type": "object"},
                },
                "required": ["endpoint_key", "label", "status", "realization_criteria"],
                "additionalProperties": True,
            },
        },
        "risk_flags": {"type": "array", "items": {"type": "object"}},
    },
    "required": [
        "simulation_brief",
        "actors",
        "population_archetypes",
        "cohort_states",
        "hero_archetypes",
        "hero_states",
        "trait_vectors",
        "graph_edges",
        "emotion_observations",
        "sociology_baseline",
        "sociology_prompt_influences",
        "channels",
        "initial_events",
        "branch_hypotheses",
        "merge_hypotheses",
        "important_questions",
        "endpoint_ledger",
        "risk_flags",
    ],
    "additionalProperties": True,
}


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
        json_schema=INITIALIZER_JSON_SCHEMA,
        json_response_transform=lambda parsed: normalize_initializer_output(parsed, scenario_input),
        metadata={"temperature": 0.25, "agent_type": "initializer_agent"},
    )
    normalized = response.parsed or {}
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
            {
                "name": "affected_public",
                "population_total": 1000,
                "definition": {"premise": premise, "population_total": 1000},
            },
        ],
        "cohorts": [
            {
                "name": "affected public",
                "actor_name": "Public Cohort",
                "represented_population": 1000,
                "population_share_of_archetype": 1.0,
                "representation_mode": "population",
                "state": {
                    "represented_population": 1000,
                    "population_share_of_archetype": 1.0,
                    "representation_mode": "population",
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
        "important_questions": [
            "Which endpoint best explains how this scenario resolves?",
        ],
        "endpoint_ledger": [
            {
                "endpoint_key": "scenario_resolution",
                "label": "Scenario resolution",
                "description": "The scenario reaches a stable, evidence-backed terminal interpretation.",
                "status": "active",
                "probability": None,
                "realization_criteria": [
                    "A later authority decision or executed terminal event names the resolved endpoint.",
                ],
                "authority_refs": ["Institutional Actor"],
                "evidence_refs": ["scenario:premise"],
                "negative_evidence_refs": [],
                "blockers": ["No terminal evidence yet"],
                "status_basis": "initializer_endpoint_ledger",
                "contradiction_notes": "Track later evidence that supports, weakens, eliminates, or realizes this endpoint.",
                "rationale": "Fallback initializer endpoint for a scenario without explicit endpoint options.",
                "last_observed_tick_index": None,
                "meta": {
                    "source": "initializer_endpoint_ledger",
                    "important_question": "Which endpoint best explains how this scenario resolves?",
                },
            }
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
    normalized["important_questions"] = _normalize_important_questions(
        output.get("important_questions")
        or output.get("importantQuestions")
        or output.get("endpoint_questions")
        or output.get("endpointQuestions"),
        fallback["important_questions"],
    )
    normalized["branch_hypotheses"] = _merge_scenario_branch_hypotheses(
        normalized["branch_hypotheses"],
        scenario_input,
    )
    normalized["endpoint_ledger"] = _normalize_initializer_endpoint_ledger(
        output.get("endpoint_ledger") or output.get("endpointLedger") or output.get("endpoints"),
        important_questions=normalized["important_questions"],
        branch_hypotheses=normalized["branch_hypotheses"],
        default=fallback["endpoint_ledger"],
    )
    normalized["endpoint_ledger"] = _overlay_scenario_candidate_endpoints(
        normalized["endpoint_ledger"],
        scenario_input,
    )
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


def _normalize_important_questions(value: Any, default: list[str]) -> list[str]:
    questions: list[str] = []
    for item in _list_value(value):
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = str(item.get("question") or item.get("prompt") or item.get("text") or "")
        else:
            text = ""
        text = " ".join(text.strip().split())
        if text and text not in questions:
            questions.append(text)
        if len(questions) == 5:
            break
    if questions:
        return questions
    return [str(item).strip() for item in default[:5] if str(item).strip()]


def _merge_scenario_branch_hypotheses(
    generated: list[dict],
    scenario_input: dict[str, Any],
    *,
    limit: int = 5,
) -> list[dict]:
    scenario_items = []
    if isinstance(scenario_input, dict):
        scenario_items = [dict(item) for item in _list_value(scenario_input.get("branch_hypotheses")) if isinstance(item, dict)]
    if not scenario_items:
        return generated
    merged: list[dict] = []
    seen: set[str] = set()
    for source in (scenario_items, generated):
        for item in source:
            key = _branch_hypothesis_key(item)
            if key in seen:
                continue
            merged.append(dict(item))
            seen.add(key)
            if len(merged) >= limit:
                return merged
    return merged


def _branch_hypothesis_key(item: dict) -> str:
    candidate_id = str(item.get("candidate_endpoint_id") or item.get("candidate_id") or "").strip().lower()
    if candidate_id:
        return f"candidate:{candidate_id}"
    text = str(
        item.get("label")
        or item.get("alternate_path")
        or item.get("plausible_alternate_path")
        or item.get("trigger")
        or item
    )
    return "text:" + " ".join(text.lower().split())


def _normalize_initializer_endpoint_ledger(
    value: Any,
    *,
    important_questions: list[str],
    branch_hypotheses: list[dict],
    default: list[dict],
) -> list[dict[str, Any]]:
    raw_entries = [item for item in _list_value(value) if isinstance(item, dict)]
    if not raw_entries:
        raw_entries = _endpoint_entries_from_branch_hypotheses(branch_hypotheses)
    if not raw_entries:
        raw_entries = default

    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_entries):
        question = _entry_question(item, important_questions, index)
        label = str(item.get("label") or item.get("name") or item.get("endpoint_key") or question or "Endpoint").strip()
        key = _endpoint_key(item.get("endpoint_key") or item.get("key") or label)
        if not key or key in seen:
            continue
        seen.add(key)
        status = str(item.get("status") or "active").lower()
        if status not in {"active", "weakened", "eliminated", "realized", "unresolved", "process_only"}:
            status = "active"
        probability = _optional_probability(item.get("probability"))
        meta = dict(item.get("meta")) if isinstance(item.get("meta"), dict) else {}
        meta.setdefault("source", "initializer_endpoint_ledger")
        if question:
            meta.setdefault("important_question", question)
        normalized.append(
            {
                "endpoint_key": key,
                "label": label,
                "description": str(item.get("description") or item.get("rationale") or label).strip() or None,
                "status": status,
                "probability": probability,
                "realization_criteria": _string_list(item.get("realization_criteria"))
                or _string_list(item.get("criteria"))
                or [f"Observable evidence answers: {question or label}"],
                "authority_refs": _list_value(item.get("authority_refs") or item.get("authority") or item.get("decision_authority")),
                "evidence_refs": _list_value(item.get("evidence_refs")) or [{"source": "initializer", "kind": "endpoint_seed"}],
                "negative_evidence_refs": _list_value(item.get("negative_evidence_refs")),
                "blockers": _string_list(item.get("blockers")),
                "status_basis": str(item.get("status_basis") or "initializer_endpoint_ledger"),
                "contradiction_notes": str(
                    item.get("contradiction_notes")
                    or "Track later evidence that supports, weakens, eliminates, or realizes this endpoint."
                ),
                "rationale": str(item.get("rationale") or "Seeded by the initializer as an endpoint option."),
                "last_observed_tick_index": _optional_int(item.get("last_observed_tick_index")),
                "meta": meta,
            }
        )
        if len(normalized) == 5:
            break
    return normalized or list(default)


def _overlay_scenario_candidate_endpoints(
    entries: list[dict[str, Any]],
    scenario_input: dict[str, Any],
) -> list[dict[str, Any]]:
    raw_candidates = scenario_input.get("candidate_endpoints")
    if not isinstance(raw_candidates, list):
        return entries
    forecast_metadata = scenario_input.get("forecast_metadata") if isinstance(scenario_input.get("forecast_metadata"), dict) else {}
    merged = {str(entry.get("endpoint_key") or ""): dict(entry) for entry in entries if entry.get("endpoint_key")}
    order = [str(entry.get("endpoint_key")) for entry in entries if entry.get("endpoint_key")]
    for candidate in raw_candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_id = str(candidate.get("id") or candidate.get("endpoint_key") or candidate.get("label") or "").strip()
        if not candidate_id:
            continue
        key = _endpoint_key(candidate_id)
        label = str(candidate.get("label") or candidate.get("description") or candidate_id)
        current = merged.get(key, {})
        meta = dict(current.get("meta")) if isinstance(current.get("meta"), dict) else {}
        current.update(
            {
                "endpoint_key": key,
                "label": current.get("label") or label,
                "description": candidate.get("description") or current.get("description") or label,
                "status": str(current.get("status") or candidate.get("status") or "active").lower(),
                "probability": None,
                "realization_criteria": _string_list(candidate.get("realization_criteria"))
                or current.get("realization_criteria")
                or [
                    f"Resolve candidate endpoint {candidate_id} using the forecast question, deadline, and official settlement evidence.",
                ],
                "authority_refs": _list_value(candidate.get("authority_refs")) or current.get("authority_refs") or ["forecast_card"],
                "evidence_refs": [
                    {"source": "scenario_candidate_endpoint", "candidate_endpoint_id": candidate_id},
                    *_list_value(candidate.get("evidence_refs")),
                ],
                "negative_evidence_refs": _list_value(candidate.get("negative_evidence_refs")),
                "blockers": _string_list(candidate.get("blockers")) or current.get("blockers") or [],
                "status_basis": current.get("status_basis") or "scenario_candidate_endpoint",
                "contradiction_notes": current.get("contradiction_notes")
                or "Auxiliary mechanism endpoints must not override this primary yes/no candidate.",
                "rationale": current.get("rationale") or "Preserved from scenario candidate endpoints.",
                "last_observed_tick_index": _optional_int(current.get("last_observed_tick_index")),
                "meta": {
                    **meta,
                    "source": "scenario_candidate_endpoint",
                    "endpoint_role": "primary_candidate",
                    "candidate_endpoint_id": candidate_id.lower(),
                    "candidate_endpoint_role": candidate_id.lower(),
                    "forecast_deadline_date": forecast_metadata.get("forecast_deadline_date"),
                    "as_of_date": forecast_metadata.get("as_of_date"),
                },
            }
        )
        if key not in merged:
            order.append(key)
        merged[key] = current
    return [merged[key] for key in order if key in merged]


def _endpoint_entries_from_branch_hypotheses(items: list[dict]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for item in items[:5]:
        text = item.get("alternate_path") or item.get("plausible_alternate_path") or item.get("trigger") or item.get("label")
        if not text:
            continue
        entries.append(
            {
                "endpoint_key": _endpoint_key(text),
                "label": str(text),
                "description": item.get("observable_divergence_signal") or item.get("expected_divergence") or str(text),
                "status": "active",
                "realization_criteria": _string_list(item.get("realization_criteria"))
                or [f"Observable evidence confirms {text}."],
                "authority_refs": _list_value(item.get("authority") or item.get("decision_authority") or item.get("actor")),
                "evidence_refs": [{"source": "initializer", "kind": "branch_hypothesis"}],
                "blockers": [],
                "rationale": "Converted from initializer branch hypothesis because no explicit endpoint ledger was returned.",
            }
        )
    return entries


def _entry_question(item: dict[str, Any], questions: list[str], index: int) -> str | None:
    raw = item.get("important_question") or item.get("question") or item.get("evaluation_question")
    if raw:
        return str(raw).strip()
    if questions:
        return questions[min(index, len(questions) - 1)]
    return None


def _endpoint_key(value: Any) -> str:
    text = str(value or "endpoint").strip().lower()
    chars = [char if char.isalnum() else "_" for char in text]
    key = "_".join("".join(chars).split("_"))
    return key[:120] or "endpoint"


def _optional_probability(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _string_list(value: Any) -> list[str]:
    items = _list_value(value)
    return [" ".join(str(item).strip().split()) for item in items if str(item).strip()]


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
