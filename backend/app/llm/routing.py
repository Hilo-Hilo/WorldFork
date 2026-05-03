from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import FAST_MODEL_DEFAULT, SMART_MODEL_DEFAULT, get_settings


class AuditedLLMRoute(StrEnum):
    """Stable route names for direct audited simulation LLM calls."""

    INITIALIZER_CHUNK_EXTRACTOR = "initializer_chunk_extractor"
    INITIALIZER_AGENT = "initializer_agent"
    GOD_AGENT = "god_agent"
    COHORT_AGENT = "cohort_agent"
    HERO_AGENT = "hero_agent"
    EVENT_SUMMARY = "event_summary"
    REPORT_AGENT = "report_agent"
    ENDPOINT_LEDGER = "endpoint_ledger"


@dataclass(frozen=True)
class AuditedLLMRouteInfo:
    route: str
    label: str
    description: str
    fallback_model_setting: str


AUDITED_LLM_ROUTES: tuple[AuditedLLMRouteInfo, ...] = (
    AuditedLLMRouteInfo(
        route=AuditedLLMRoute.INITIALIZER_CHUNK_EXTRACTOR,
        label="Initializer chunk extractor",
        description="Extracts structured facts from long scenario text chunks before initialization.",
        fallback_model_setting="initializer_agent_model",
    ),
    AuditedLLMRouteInfo(
        route=AuditedLLMRoute.INITIALIZER_AGENT,
        label="Initializer agent",
        description="Builds the initial actors, cohorts, heroes, graph state, and baseline events.",
        fallback_model_setting="initializer_agent_model",
    ),
    AuditedLLMRouteInfo(
        route=AuditedLLMRoute.GOD_AGENT,
        label="God review agent",
        description="Reviews provisional ticks and decides whether to continue, branch, merge, or terminate.",
        fallback_model_setting="god_agent_model",
    ),
    AuditedLLMRouteInfo(
        route=AuditedLLMRoute.COHORT_AGENT,
        label="Cohort agent",
        description="Generates cohort social actions, proposed events, and self-ratings for each tick.",
        fallback_model_setting="cohort_agent_model",
    ),
    AuditedLLMRouteInfo(
        route=AuditedLLMRoute.HERO_AGENT,
        label="Hero agent",
        description="Generates hero social actions, proposed events, and self-ratings for each tick.",
        fallback_model_setting="hero_agent_model",
    ),
    AuditedLLMRouteInfo(
        route=AuditedLLMRoute.EVENT_SUMMARY,
        label="Event summary agent",
        description="Summarizes executed simulation events into structured event summary records.",
        fallback_model_setting="event_summary_model",
    ),
    AuditedLLMRouteInfo(
        route=AuditedLLMRoute.REPORT_AGENT,
        label="Report agent",
        description="Writes structured multiverse and final Big Bang report summaries.",
        fallback_model_setting="report_agent_model",
    ),
    AuditedLLMRouteInfo(
        route=AuditedLLMRoute.ENDPOINT_LEDGER,
        label="Endpoint ledger evaluator",
        description="Evaluates endpoint ledger probabilities from simulation evidence.",
        fallback_model_setting="god_agent_model",
    ),
)

_ROUTE_INFO_BY_NAME = {str(item.route): item for item in AUDITED_LLM_ROUTES}
_OPENROUTER_PROVIDER = "openrouter"
_FAST_MODEL = FAST_MODEL_DEFAULT
_SMART_MODEL = SMART_MODEL_DEFAULT
_OPENROUTER_MODEL = _FAST_MODEL
_LEGACY_GEMINI_SEED_MODEL = "google/gemini-3.1-flash-lite-preview"
_SMART_MODEL_ROUTES = frozenset(
    {
        str(AuditedLLMRoute.INITIALIZER_CHUNK_EXTRACTOR),
        str(AuditedLLMRoute.INITIALIZER_AGENT),
        str(AuditedLLMRoute.GOD_AGENT),
        str(AuditedLLMRoute.REPORT_AGENT),
        str(AuditedLLMRoute.ENDPOINT_LEDGER),
        "initialize_big_bang",
        "god_agent_review",
        "aggregate_run_results",
        "evaluate_endpoint_ledger",
    }
)


@dataclass(frozen=True)
class LLMRouteCandidate:
    provider: str
    model: str
    source: str
    metadata_defaults: dict[str, Any]


@dataclass(frozen=True)
class ResolvedLLMRoute:
    requested_route: str | None
    matched_route: str | None
    primary: LLMRouteCandidate
    fallback: LLMRouteCandidate | None = None

    def candidates(self) -> tuple[LLMRouteCandidate, ...]:
        if self.fallback is None:
            return (self.primary,)
        return (self.primary, self.fallback)

    def metadata_for(self, candidate: LLMRouteCandidate, metadata: dict[str, Any]) -> dict[str, Any]:
        return {**candidate.metadata_defaults, **metadata}

    def audit_meta(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "requested_route": self.requested_route,
            "matched_route": self.matched_route,
            "primary_provider": self.primary.provider,
            "primary_model": self.primary.model,
            "source": self.primary.source,
        }
        if self.fallback is not None:
            payload.update(
                {
                    "fallback_provider": self.fallback.provider,
                    "fallback_model": self.fallback.model,
                }
            )
        return payload


def route_for_actor_type(actor_type: str | None) -> AuditedLLMRoute:
    normalized = (actor_type or "").strip().lower()
    if normalized == "hero":
        return AuditedLLMRoute.HERO_AGENT
    return AuditedLLMRoute.COHORT_AGENT


def audited_route_catalog() -> list[dict[str, Any]]:
    return [
        {
            "route": str(item.route),
            "route_kind": "audited_llm",
            "label": item.label,
            "description": item.description,
            "fallback_model_setting": item.fallback_model_setting,
            "direct_override": True,
        }
        for item in AUDITED_LLM_ROUTES
    ]


def resolve_audited_llm_route(
    db: Session,
    *,
    route: AuditedLLMRoute | str | None,
    fallback_provider: str | None = None,
    fallback_model: str | None = None,
) -> ResolvedLLMRoute:
    settings = get_settings()
    route_name = _route_name(route)
    row = _route_row(db, route_name)
    if _is_stale_seed_default_row(row):
        row = None
    matched_route = route_name if row is not None else None

    if row is not None:
        primary = LLMRouteCandidate(
            provider=str(row["preferred_provider"]),
            model=str(row["preferred_model"]),
            source="settings_model_routing",
            metadata_defaults=_metadata_defaults(row),
        )
        fallback = None
        if row.get("fallback_provider") and row.get("fallback_model"):
            fallback_provider = str(row["fallback_provider"])
            fallback_model = str(row["fallback_model"])
            if (fallback_provider, fallback_model) != (primary.provider, primary.model):
                fallback = LLMRouteCandidate(
                    provider=fallback_provider,
                    model=fallback_model,
                    source="settings_model_routing",
                    metadata_defaults=_metadata_defaults(row),
                )
        return ResolvedLLMRoute(
            requested_route=route_name,
            matched_route=matched_route,
            primary=primary,
            fallback=fallback,
        )

    default_provider = _settings_provider_for_route(settings, route_name, fallback_provider)
    default_model = fallback_model or _settings_model_for_route(settings, route_name)
    return ResolvedLLMRoute(
        requested_route=route_name,
        matched_route=None,
        primary=LLMRouteCandidate(
            provider=default_provider,
            model=default_model,
            source="settings",
            metadata_defaults={},
        ),
    )


def _route_name(route: AuditedLLMRoute | str | None) -> str | None:
    if route is None:
        return None
    if isinstance(route, AuditedLLMRoute):
        return str(route)
    value = str(route).strip()
    return value or None

def _route_row(db: Session, route_name: str | None) -> dict[str, Any] | None:
    if not route_name:
        return None
    try:
        result = db.execute(
            text(
                "SELECT job_type, preferred_provider, preferred_model, "
                "fallback_provider, fallback_model, temperature, top_p, "
                "max_tokens, timeout_seconds, retry_policy, payload "
                "FROM settings_model_routing WHERE job_type = :job_type"
            ),
            {"job_type": route_name},
        )
        row = result.mappings().first()
    except Exception:
        return None
    return dict(row) if row is not None else None

def _is_seed_default_row(row: dict[str, Any]) -> bool:
    payload = row.get("payload")
    return (
        (isinstance(payload, dict) and payload.get("source") == "seed_default")
        or _is_stale_seed_default_row(row)
    )


def _is_legacy_seed_default_row(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    payload = row.get("payload")
    if not isinstance(payload, dict):
        return False
    return (
        payload.get("source") == "seed_default"
        and row.get("preferred_provider") == _OPENROUTER_PROVIDER
        and row.get("preferred_model") == _LEGACY_GEMINI_SEED_MODEL
        and payload.get("preferred_provider") == _OPENROUTER_PROVIDER
        and payload.get("preferred_model") == _LEGACY_GEMINI_SEED_MODEL
    )


def _is_stale_seed_default_row(row: dict[str, Any] | None) -> bool:
    if not row:
        return False
    if _is_legacy_seed_default_row(row):
        return True
    payload = row.get("payload")
    if not isinstance(payload, dict) or payload.get("source") != "seed_default":
        return False
    expected_model = _seed_default_model_for_row(row)
    return not (
        row.get("preferred_provider") == _OPENROUTER_PROVIDER
        and row.get("preferred_model") == expected_model
        and row.get("fallback_provider") == _OPENROUTER_PROVIDER
        and row.get("fallback_model") == expected_model
    )


def _seed_default_model_for_row(row: dict[str, Any]) -> str:
    job_type = str(row.get("job_type") or "")
    return _SMART_MODEL if job_type in _SMART_MODEL_ROUTES else _FAST_MODEL


def _metadata_defaults(row: dict[str, Any]) -> dict[str, Any]:
    defaults = {
        "temperature": row.get("temperature"),
        "top_p": row.get("top_p"),
        "max_tokens": row.get("max_tokens"),
        "timeout_seconds": row.get("timeout_seconds"),
        "retry_policy": row.get("retry_policy"),
    }
    return {key: value for key, value in defaults.items() if value is not None}


def _settings_model_for_route(settings: Any, route_name: str | None) -> str:
    if route_name:
        info = _ROUTE_INFO_BY_NAME.get(route_name)
        if info is not None:
            value = getattr(settings, info.fallback_model_setting, None)
            if value:
                return str(value)
    return str(getattr(settings, "default_model", ""))


def _settings_provider_for_route(
    settings: Any,
    route_name: str | None,
    fallback_provider: str | None,
) -> str:
    return str(fallback_provider or getattr(settings, "default_llm_provider", "openrouter"))
