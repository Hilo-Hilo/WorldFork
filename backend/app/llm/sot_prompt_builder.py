"""Prompt-packet builder.

Builds :class:`PromptPacket` instances for cohort/hero/god/initializer LLM
calls.  The builder is the single funnel through which the rest of the engine
constructs prompts so that the §10.2 packet structure, §10.3 clock format,
§11.3 temperature table, and §11.2 active-agent rules are applied uniformly.

The builder is **deterministic** modulo the `_select_temperature` jitter which
uses ``random.Random(seed)`` keyed on ``actor_id+tick`` for reproducibility.
"""
from __future__ import annotations

import json
import logging
import random
from time import perf_counter
from typing import Any, Literal

import jinja2

from backend.app.schemas.actors import (
    CohortState,
    HeroArchetype,
    HeroState,
    PopulationArchetype,
)
from backend.app.schemas.common import Clock
from backend.app.schemas.events import Event
from backend.app.schemas.llm import PromptPacket
from backend.app.schemas.posts import SocialPost
from backend.app.storage.sot_loader import SoTBundle

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# §11.3 temperature table — (lower, upper) inclusive range per representation.
# ---------------------------------------------------------------------------

_TEMP_BANDS: dict[str, tuple[float, float]] = {
    "hero": (0.7, 1.0),
    "micro": (0.8, 1.0),
    "small": (0.6, 0.8),
    "population": (0.35, 0.6),
    "mass": (0.2, 0.45),
    "god": (0.4, 0.7),
    "initializer": (0.5, 0.8),
}


def _temp_band_for_population(pop: int) -> tuple[float, float]:
    """§11.3 lookup based on represented_population."""
    if pop <= 1:
        return _TEMP_BANDS["hero"]
    if pop <= 25:
        return _TEMP_BANDS["micro"]
    if pop <= 250:
        return _TEMP_BANDS["small"]
    if pop <= 5_000:
        return _TEMP_BANDS["population"]
    return _TEMP_BANDS["mass"]


# ---------------------------------------------------------------------------
# Prompt template name → output_schema_id mapping
# ---------------------------------------------------------------------------

_TEMPLATE_TO_SCHEMA: dict[str, str] = {
    "cohort_tick": "cohort_decision_schema",
    "hero_tick": "hero_decision_schema",
    "god_review": "god_review_schema",
    "initializer": "initializer_schema",
}


class PromptBuilder:
    """Build prompt packets from a :class:`SoTBundle` plus a state slice."""

    def __init__(self, sot: SoTBundle) -> None:
        self.sot = sot
        # Load Jinja templates from the SoT bundle (already read into memory by
        # the loader).  StrictUndefined surfaces missing context vars during
        # template development; trim_blocks/lstrip_blocks keep markdown clean.
        self._jinja_env = jinja2.Environment(
            loader=jinja2.DictLoader(dict(sot.prompt_templates)),
            undefined=jinja2.StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            autoescape=False,
        )
        # Add a ``tojson`` filter that emits compact JSON; jinja2's stock
        # filter calls htmlsafe_json_dumps which escapes characters we don't
        # want here.  We delegate to ``json.dumps`` instead.
        self._jinja_env.filters["tojson"] = self._tojson_filter

    # ------------------------------------------------------------------
    # Public packet builders
    # ------------------------------------------------------------------

    def build_cohort_packet(
        self,
        *,
        cohort: CohortState,
        archetype: PopulationArchetype,
        clock: Clock,
        visible_feed: list[SocialPost],
        visible_events: list[Event],
        own_queued_events: list[Event],
        own_recent_actions: list[dict],
        retrieved_memory: dict | None,
    ) -> PromptPacket:
        t0 = perf_counter()

        sot_excerpt = self._excerpt_sot("cohort")
        schema = self.sot.prompt_contracts.get("cohort_decision_schema", {})
        temperature = self._select_temperature(cohort)
        allowed_tools = self._filter_allowed_tools(cohort.allowed_tools)

        ctx = {
            "clock": self._clock_template_ctx(clock),
            "archetype": self._archetype_template_ctx(archetype),
            "cohort_state": cohort.model_dump(mode="json"),
            "represented_population": cohort.represented_population,
            "representation_mode": cohort.representation_mode,
            "sot": sot_excerpt,
            "visible_feed": [p.model_dump(mode="json") for p in visible_feed],
            "visible_events": [e.model_dump(mode="json") for e in visible_events],
            "own_queued_events": [
                e.model_dump(mode="json") for e in own_queued_events
            ],
            "own_recent_actions": list(own_recent_actions),
            "retrieved_memory": retrieved_memory if retrieved_memory else "(no memory retrieved)",
            "allowed_tools": allowed_tools,
            "schema": json.dumps(schema, indent=2, sort_keys=True),
            "temperature_hint": round(temperature, 3),
        }

        rendered = self._render("cohort_tick", ctx)
        system = self._wrap_system(rendered, schema)
        build_ms = int((perf_counter() - t0) * 1000)

        return PromptPacket(
            system=system,
            clock=clock,
            actor_id=cohort.cohort_id,
            actor_kind="cohort",
            archetype=archetype.model_dump(mode="json"),
            state=cohort.model_dump(mode="json"),
            sot_excerpt=sot_excerpt,
            visible_feed=[p.model_dump(mode="json") for p in visible_feed],
            visible_events=[e.model_dump(mode="json") for e in visible_events],
            own_queued_events=[e.model_dump(mode="json") for e in own_queued_events],
            own_recent_actions=list(own_recent_actions),
            retrieved_memory=retrieved_memory,
            allowed_tools=[t["tool_id"] for t in allowed_tools],
            output_schema_id="cohort_decision_schema",
            temperature=temperature,
            metadata=self._metadata(
                template="cohort_tick",
                actor_id=cohort.cohort_id,
                tick=cohort.tick,
                universe_id=cohort.universe_id,
                build_ms=build_ms,
            ),
        )

    def build_hero_packet(
        self,
        *,
        hero: HeroState,
        archetype: HeroArchetype,
        clock: Clock,
        visible_feed: list[SocialPost],
        visible_events: list[Event],
        own_queued_events: list[Event],
        own_recent_actions: list[dict],
        retrieved_memory: dict | None,
    ) -> PromptPacket:
        t0 = perf_counter()

        sot_excerpt = self._excerpt_sot("hero")
        schema = self.sot.prompt_contracts.get("hero_decision_schema", {})
        temperature = self._jittered_temperature("hero", seed_key=f"{hero.hero_id}:{hero.tick}")
        allowed_tools = self._filter_allowed_tools(None)

        # Heroes can see the status of their queued events; we surface it
        # alongside the raw event payload for the template.
        queued_with_status = []
        for e in own_queued_events:
            d = e.model_dump(mode="json")
            d.setdefault("status", e.status)
            queued_with_status.append(d)

        ctx = {
            "clock": self._clock_template_ctx(clock),
            "hero": self._hero_template_ctx(archetype),
            "hero_state": hero.model_dump(mode="json"),
            "sot": sot_excerpt,
            "visible_feed": [p.model_dump(mode="json") for p in visible_feed],
            "visible_events": [e.model_dump(mode="json") for e in visible_events],
            "queued_events_with_status": queued_with_status,
            "own_recent_actions": list(own_recent_actions),
            "retrieved_memory": retrieved_memory if retrieved_memory else "(no memory retrieved)",
            "allowed_tools": allowed_tools,
            "schema": json.dumps(schema, indent=2, sort_keys=True),
            "temperature_hint": round(temperature, 3),
        }

        rendered = self._render("hero_tick", ctx)
        system = self._wrap_system(rendered, schema)
        build_ms = int((perf_counter() - t0) * 1000)

        return PromptPacket(
            system=system,
            clock=clock,
            actor_id=hero.hero_id,
            actor_kind="hero",
            archetype=archetype.model_dump(mode="json"),
            state=hero.model_dump(mode="json"),
            sot_excerpt=sot_excerpt,
            visible_feed=[p.model_dump(mode="json") for p in visible_feed],
            visible_events=[e.model_dump(mode="json") for e in visible_events],
            own_queued_events=[e.model_dump(mode="json") for e in own_queued_events],
            own_recent_actions=list(own_recent_actions),
            retrieved_memory=retrieved_memory,
            allowed_tools=[t["tool_id"] for t in allowed_tools],
            output_schema_id="hero_decision_schema",
            temperature=temperature,
            metadata=self._metadata(
                template="hero_tick",
                actor_id=hero.hero_id,
                tick=hero.tick,
                universe_id=hero.universe_id,
                build_ms=build_ms,
            ),
        )

    def build_god_packet(
        self,
        *,
        universe_state: dict,
        recent_ticks: list[dict],
        event_proposals: list[Event],
        social_posts: list[SocialPost],
        metrics: dict,
        branch_candidates: list[dict],
        rate_limit_state: dict,
        budget_state: dict,
        prior_branch_history: list[dict],
        clock: Clock | None = None,
        branch_policy: dict | None = None,
    ) -> PromptPacket:
        t0 = perf_counter()

        sot_excerpt = self._excerpt_sot("god")
        schema = self.sot.prompt_contracts.get("god_review_schema", {})
        universe_id = str(universe_state.get("universe_id", "unknown"))
        tick_value = int(
            universe_state.get("current_tick")
            or (clock.current_tick if clock else 0)
        )

        if clock is None:
            # Build a minimal clock from universe_state if not supplied.
            clock = Clock(
                current_tick=tick_value,
                tick_duration_minutes=int(universe_state.get("tick_duration_minutes", 60)),
                elapsed_minutes=int(universe_state.get("elapsed_minutes", tick_value * 60)),
                previous_tick_minutes=None,
                max_schedule_horizon_ticks=int(universe_state.get("max_schedule_horizon_ticks", 5)),
            )

        temperature = self._jittered_temperature(
            "god", seed_key=f"god:{universe_id}:{tick_value}"
        )

        ctx = {
            "clock": self._clock_template_ctx(clock),
            "universe": {
                "universe_id": universe_id,
                "branch_depth": universe_state.get("branch_depth", 0),
                "lineage_path": universe_state.get("lineage_path", [universe_id]),
                "status": universe_state.get("status", "active"),
            },
            "recent_ticks": list(recent_ticks),
            "event_proposals": [e.model_dump(mode="json") for e in event_proposals],
            "social_posts": [p.model_dump(mode="json") for p in social_posts],
            "metrics": dict(metrics),
            "branch_candidates": list(branch_candidates),
            "rate_limit_state": dict(rate_limit_state),
            "budget_state": dict(budget_state),
            "current_universe_state": dict(universe_state),
            "prior_branch_history": list(prior_branch_history),
            "branch_policy": branch_policy or {"min_divergence_score": 0.35},
            "schema": json.dumps(schema, indent=2, sort_keys=True),
            "sot": sot_excerpt,
        }

        rendered = self._render("god_review", ctx)
        system = self._wrap_system(rendered, schema)
        build_ms = int((perf_counter() - t0) * 1000)

        return PromptPacket(
            system=system,
            clock=clock,
            actor_id=f"god:{universe_id}",
            actor_kind="god",
            archetype=None,
            state=dict(universe_state),
            sot_excerpt=sot_excerpt,
            visible_feed=[p.model_dump(mode="json") for p in social_posts],
            visible_events=[e.model_dump(mode="json") for e in event_proposals],
            own_queued_events=[],
            own_recent_actions=list(recent_ticks),
            retrieved_memory=None,
            allowed_tools=[],
            output_schema_id="god_review_schema",
            temperature=temperature,
            metadata=self._metadata(
                template="god_review",
                actor_id=f"god:{universe_id}",
                tick=tick_value,
                universe_id=universe_id,
                build_ms=build_ms,
                extras={"branch_candidates": len(branch_candidates)},
            ),
        )

    def build_initializer_packet(
        self,
        *,
        scenario_text: str,
        uploaded_docs: list[dict],
        time_horizon_label: str,
        tick_duration_minutes: int,
        max_ticks: int,
    ) -> PromptPacket:
        t0 = perf_counter()

        sot_excerpt = self._excerpt_sot("god")  # initializer needs the broad SoT
        schema = self.sot.prompt_contracts.get("initializer_schema", {})
        temperature = self._jittered_temperature(
            "initializer",
            seed_key=f"init:{hash(scenario_text)}",
        )

        # Initializer runs before the persisted clock exists, so synthesize the
        # tick-0 clock required by PromptPacket validation.
        clock = Clock(
            current_tick=0,
            tick_duration_minutes=tick_duration_minutes,
            elapsed_minutes=0,
            previous_tick_minutes=None,
            max_schedule_horizon_ticks=min(max_ticks, 10),
        )

        ctx = {
            "scenario_text": scenario_text,
            "uploaded_docs": list(uploaded_docs),
            "config": {
                "time_horizon_label": time_horizon_label,
                "tick_duration_minutes": tick_duration_minutes,
                "max_ticks": max_ticks,
            },
            "sot": sot_excerpt,
            "schema": json.dumps(schema, indent=2, sort_keys=True),
        }

        rendered = self._render("initializer", ctx)
        system = self._wrap_system(rendered, schema)
        build_ms = int((perf_counter() - t0) * 1000)

        return PromptPacket(
            system=system,
            clock=clock,
            actor_id="initializer",
            actor_kind="god",
            archetype=None,
            state={
                "scenario_text": scenario_text,
                "time_horizon_label": time_horizon_label,
                "tick_duration_minutes": tick_duration_minutes,
                "max_ticks": max_ticks,
            },
            sot_excerpt=sot_excerpt,
            visible_feed=[],
            visible_events=[],
            own_queued_events=[],
            own_recent_actions=[],
            retrieved_memory=None,
            allowed_tools=[],
            output_schema_id="initializer_schema",
            temperature=temperature,
            metadata=self._metadata(
                template="initializer",
                actor_id="initializer",
                tick=0,
                universe_id="(pending)",
                build_ms=build_ms,
            ),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _select_temperature(self, cohort: CohortState) -> float:
        """§11.3 lookup with deterministic jitter inside the band."""
        # Heroes (population==1) handled by _temp_band_for_population.
        band_lo, band_hi = _temp_band_for_population(cohort.represented_population)
        rng = random.Random(hash(f"{cohort.cohort_id}:{cohort.tick}"))
        return round(band_lo + rng.random() * (band_hi - band_lo), 4)

    def _jittered_temperature(self, band_key: str, *, seed_key: str) -> float:
        """Deterministic jitter inside ``_TEMP_BANDS[band_key]``."""
        band_lo, band_hi = _TEMP_BANDS[band_key]
        rng = random.Random(hash(seed_key))
        return round(band_lo + rng.random() * (band_hi - band_lo), 4)

    def _render(self, template_name: str, ctx: dict[str, Any]) -> str:
        try:
            template = self._jinja_env.get_template(template_name)
        except jinja2.TemplateNotFound as exc:
            raise KeyError(
                f"Prompt template {template_name!r} not present in SoT bundle"
            ) from exc
        return template.render(**ctx)

    def _wrap_system(self, rendered: str, schema: dict) -> str:
        """Append the §10.5 strict JSON-only instruction to the rendered text.

        The cohort/hero/god templates already include the schema, but we still
        append a final "Return JSON only" sentinel so even a stripped or
        truncated template keeps the contract clear.
        """
        # Keep the system message exactly: rendered text + closing instruction.
        sentinel = (
            "\n\n---\n\n"
            "FINAL INSTRUCTION: Return JSON only matching the schema above. "
            "Do not include any other text. Do not wrap the JSON in fences. "
            "Begin your response with `{` and end with `}`."
        )
        return rendered + sentinel

    def _filter_allowed_tools(
        self, allowed_ids: list[str] | None
    ) -> list[dict]:
        """Filter the SoT tool registry against ``allowed_ids``.

        If ``allowed_ids`` is None or empty, returns the full tool registry
        (heroes default to the full set unless their archetype restricts it).
        """
        all_tools = self.sot.social_action_tools.get("tools", [])
        if not allowed_ids:
            return list(all_tools)
        allowed_set = set(allowed_ids)
        return [t for t in all_tools if t.get("tool_id") in allowed_set]

    def _excerpt_sot(self, actor_kind: Literal["cohort", "hero", "god"]) -> dict:
        """Return a minimal SoT subset for the prompt to save tokens.

        Cohort/hero get emotions + behavior axes + event types + expression
        scale + issue stance axes.  God additionally gets channel types and
        actor types so it can reason about platform / actor-class shifts.
        """
        base = {
            "emotions": self.sot.emotions,
            "behavior_axes": self.sot.behavior_axes,
            "expression_scale": self.sot.expression_scale,
            "event_types": self.sot.event_types,
            "issue_stance_axes": self.sot.issue_stance_axes,
        }
        if actor_kind == "god":
            base["channel_types"] = self.sot.channel_types
            base["actor_types"] = self.sot.actor_types
            base["ideology_axes"] = self.sot.ideology_axes
        return base

    def _clock_template_ctx(self, clock: Clock) -> dict:
        """Materialize template-friendly clock fields (labels and ETA hints)."""
        ctx = clock.model_dump(mode="json")
        # Labels expected by templates — keep them short and human-readable.
        tick_hours = clock.tick_duration_minutes / 60
        ctx["tick_duration_label"] = (
            f"{clock.tick_duration_minutes} min"
            if tick_hours < 1
            else f"{tick_hours:.1f} h"
        )
        elapsed_hours = clock.elapsed_minutes / 60
        ctx["elapsed_label"] = (
            f"{clock.elapsed_minutes} min"
            if elapsed_hours < 1
            else f"{elapsed_hours:.1f} h"
        )
        horizon_min = clock.max_schedule_horizon_ticks * clock.tick_duration_minutes
        horizon_hours = horizon_min / 60
        ctx["max_schedule_horizon_label"] = (
            f"{horizon_min} min" if horizon_hours < 1 else f"{horizon_hours:.1f} h"
        )
        # Defaults for fields the template may reference but which we don't
        # always have — keep StrictUndefined-safe by populating placeholders.
        ctx.setdefault("most_recent_post_age_label", "n/a")
        ctx.setdefault("next_queued_event_tick", "n/a")
        ctx.setdefault("next_queued_event_eta_label", "n/a")
        return ctx

    def _archetype_template_ctx(self, archetype: PopulationArchetype) -> dict:
        """Make sure ``geography`` has ``region_label``/``scope`` keys."""
        ctx = archetype.model_dump(mode="json")
        geo = ctx.get("geography") or {}
        geo.setdefault("region_label", "unspecified")
        geo.setdefault("scope", "unspecified")
        ctx["geography"] = geo
        return ctx

    def _hero_template_ctx(self, hero: HeroArchetype) -> dict:
        return hero.model_dump(mode="json")

    def _metadata(
        self,
        *,
        template: str,
        actor_id: str,
        tick: int,
        universe_id: str,
        build_ms: int,
        extras: dict | None = None,
    ) -> dict:
        meta = {
            "prompt_template": template,
            "sot_version": self.sot.version,
            "actor_id": actor_id,
            "tick": tick,
            "universe_id": universe_id,
            "build_ms": build_ms,
            "schema_id": _TEMPLATE_TO_SCHEMA.get(template),
        }
        if extras:
            meta.update(extras)
        return meta

    @staticmethod
    def _tojson_filter(value: Any, indent: int | None = None, sort_keys: bool = False) -> str:
        """Compact-or-pretty JSON dumper that handles plain Python types."""
        return json.dumps(value, indent=indent, sort_keys=sort_keys, default=str)
