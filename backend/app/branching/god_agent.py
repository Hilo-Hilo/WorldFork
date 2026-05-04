"""God-agent reviewer for the branching runtime.

Wraps the per-tick God review into a single ``await god_review(...)`` call:

1. Build a :class:`PromptPacket` via :class:`PromptBuilder.build_god_packet`.
2. Dispatch through :func:`call_with_policy` with ``job_type="god_agent_review"``.
3. Parse the JSON output via :meth:`ToolParser.parse_god_output`.
4. Apply the invariants (spawn_active without delta -> spawn_candidate;
   kill without rationale -> safe explanatory rationale; marked_key_events filtered to
   known IDs).
5. Persist the decision to the ledger via
   :func:`backend.app.storage.artifacts.write_god_decision`.

The branch-policy gate (:func:`backend.app.branching.branch_policy.
evaluate_branch_policy`) is a separate downstream step — this module returns
the parsed decision unmodified by capacity considerations.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from backend.app.providers import call_with_policy
from backend.app.providers.rate_limits import ProviderRateLimiter
from backend.app.providers.routing import RoutingTable
from backend.app.schemas.events import Event
from backend.app.schemas.llm import GodReviewOutput
from backend.app.schemas.posts import SocialPost
from backend.app.llm.sot_prompt_builder import PromptBuilder
from backend.app.llm.tool_parser import ToolParseError, ToolParser
from backend.app.storage import artifacts
from backend.app.storage.sot_loader import SoTBundle

if TYPE_CHECKING:
    from backend.app.storage.ledger import Ledger

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Input dataclass
# ---------------------------------------------------------------------------


@dataclass
class GodReviewInput:
    """All state the God-agent needs to decide a tick."""

    universe_id: str
    run_id: str
    current_tick: int
    universe_state_summary: dict[str, Any]
    recent_ticks: list[dict[str, Any]] = field(default_factory=list)
    event_proposals: list[dict[str, Any]] = field(default_factory=list)
    social_posts: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    branch_candidates: list[dict[str, Any]] = field(default_factory=list)
    rate_limit_state: dict[str, Any] = field(default_factory=dict)
    budget_state: dict[str, Any] = field(default_factory=dict)
    prior_branch_history: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers — coerce raw dicts into the typed objects PromptBuilder expects.
# ---------------------------------------------------------------------------


def _coerce_events(items: list[dict[str, Any]]) -> list[Event]:
    """Best-effort coerce dicts into :class:`Event` instances."""
    out: list[Event] = []
    for item in items:
        if isinstance(item, Event):
            out.append(item)
            continue
        try:
            out.append(Event.model_validate(item))
        except Exception as exc:  # noqa: BLE001
            _log.debug("god_review: skipping malformed event proposal: %s", exc)
    return out


def _coerce_posts(items: list[dict[str, Any]]) -> list[SocialPost]:
    out: list[SocialPost] = []
    for item in items:
        if isinstance(item, SocialPost):
            out.append(item)
            continue
        try:
            out.append(SocialPost.model_validate(item))
        except Exception as exc:  # noqa: BLE001
            _log.debug("god_review: skipping malformed social post: %s", exc)
    return out


def _known_event_ids(
    event_proposals: Sequence[dict[str, Any] | Event],
    social_posts: Sequence[dict[str, Any] | SocialPost],
) -> set[str]:
    """Return the union of event_ids in event_proposals + post IDs / refs."""
    ids: set[str] = set()
    for item in event_proposals:
        if isinstance(item, Event):
            ids.add(item.event_id)
            continue
        if isinstance(item, dict):
            for key in ("event_id", "id"):
                v = item.get(key)
                if isinstance(v, str):
                    ids.add(v)
    for post_item in social_posts:
        if isinstance(post_item, SocialPost):
            ids.add(post_item.post_id)
            continue
        if isinstance(post_item, dict):
            for key in ("post_id", "id", "event_id"):
                v = post_item.get(key)
                if isinstance(v, str):
                    ids.add(v)
    return ids


def _is_safe_noop_decision(decision: GodReviewOutput) -> bool:
    """Detect a safe-noop fallback from provider error handling."""
    if decision.decision != "continue":
        return False
    if decision.branch_delta is not None:
        return False
    if decision.marked_key_events:
        return False
    rationale = decision.rationale or {}
    reason = rationale.get("reason") if isinstance(rationale, dict) else None
    return reason == "invalid_json_safe_noop"


# ---------------------------------------------------------------------------
# Invariant enforcement
# ---------------------------------------------------------------------------


def _coerce_spawn_active_without_delta(payload: dict[str, Any]) -> dict[str, Any]:
    """If decision==spawn_active and branch_delta missing, coerce to spawn_candidate.

    The contract requires a non-null ``branch_delta`` for ``spawn_active``;
    without one we can't construct a child universe.  Downgrading to
    ``spawn_candidate`` preserves the God-agent's intent for the branch-policy
    gate to revisit later.
    """
    decision = payload.get("decision")
    if decision == "spawn_active" and not payload.get("branch_delta"):
        _log.warning(
            "god_review: spawn_active without branch_delta — coercing to spawn_candidate"
        )
        new_payload = dict(payload)
        new_payload["decision"] = "spawn_candidate"
        return new_payload
    return payload


def _ensure_kill_rationale(payload: dict[str, Any]) -> dict[str, Any]:
    """``kill`` decisions must carry at least one ``rationale.main_factors``."""
    if payload.get("decision") != "kill":
        return payload
    rationale = payload.get("rationale") or {}
    if not isinstance(rationale, dict):
        rationale = {}
    main_factors = rationale.get("main_factors") or []
    if not isinstance(main_factors, list):
        main_factors = []
    if not main_factors:
        _log.warning(
            "god_review: kill decision missing rationale.main_factors — appending fallback rationale"
        )
        new_rationale = dict(rationale)
        new_factors = list(main_factors)
        new_factors.append("low_value_auto")
        new_rationale["main_factors"] = new_factors
        new_payload = dict(payload)
        new_payload["rationale"] = new_rationale
        return new_payload
    return payload


def _filter_marked_key_events(
    payload: dict[str, Any],
    known_ids: set[str],
) -> dict[str, Any]:
    """Drop any ``marked_key_events`` entry that isn't a known event/post id."""
    raw = payload.get("marked_key_events") or []
    if not isinstance(raw, list):
        return payload
    kept: list[str] = []
    dropped: list[Any] = []
    for entry in raw:
        if isinstance(entry, str) and entry in known_ids:
            kept.append(entry)
        else:
            dropped.append(entry)
    if dropped:
        _log.warning(
            "god_review: dropping %d unknown marked_key_events: %r",
            len(dropped),
            dropped[:5],
        )
    if kept == raw:
        return payload
    new_payload = dict(payload)
    new_payload["marked_key_events"] = kept
    return new_payload


def _apply_invariants(
    payload: dict[str, Any],
    known_ids: set[str],
) -> dict[str, Any]:
    """Apply all three invariants in order."""
    payload = _coerce_spawn_active_without_delta(payload)
    payload = _ensure_kill_rationale(payload)
    payload = _filter_marked_key_events(payload, known_ids)
    return payload


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------


async def god_review(
    input: GodReviewInput,
    *,
    sot: SoTBundle,
    routing: RoutingTable,
    limiter: ProviderRateLimiter,
    ledger: Ledger | None = None,
) -> GodReviewOutput:
    """Run a single God-agent review for one universe at one tick.

    This function:

    * builds the PromptPacket
    * calls the LLM via :func:`call_with_policy` (which handles backoff and
      fallback through the provider error-handling policy)
    * parses + repairs the JSON output via :class:`ToolParser`
    * persists the decision artifact to the ledger
    * returns the typed :class:`GodReviewOutput`

    The branch-policy capacity gate is intentionally *not* invoked here — see
    :func:`backend.app.branching.branch_policy.evaluate_branch_policy`.
    """
    # 1. Build the prompt.
    builder = PromptBuilder(sot)
    universe_state = dict(input.universe_state_summary)
    universe_state.setdefault("universe_id", input.universe_id)
    universe_state.setdefault("current_tick", input.current_tick)

    packet = builder.build_god_packet(
        universe_state=universe_state,
        recent_ticks=list(input.recent_ticks),
        event_proposals=_coerce_events(input.event_proposals),
        social_posts=_coerce_posts(input.social_posts),
        metrics=dict(input.metrics),
        branch_candidates=list(input.branch_candidates),
        rate_limit_state=dict(input.rate_limit_state),
        budget_state=dict(input.budget_state),
        prior_branch_history=list(input.prior_branch_history),
    )

    # 2. Dispatch through the policy orchestrator.
    result = await call_with_policy(
        job_type="god_agent_review",
        prompt=packet,
        routing=routing,
        limiter=limiter,
        ledger=ledger,
        run_id=input.run_id,
        universe_id=input.universe_id,
        tick=input.current_tick,
    )

    parsed_payload = result.parsed_json or {}

    # 3. Apply invariants *before* schema validation so the schema sees the
    #    repaired payload.
    known_ids = _known_event_ids(input.event_proposals, input.social_posts)
    repaired = _apply_invariants(parsed_payload, known_ids)

    # 4. Parse via the SoT-bound tool parser.  If the raw LLM JSON failed the
    #    JSONSchema (e.g. provider returned the safe-noop), fall back to a
    #    direct Pydantic validation so we never refuse to advance.
    parser = ToolParser(sot)
    try:
        decision = parser.parse_god_output(repaired)
    except ToolParseError as exc:
        _log.warning(
            "god_review: parser rejected output (%s); falling back to "
            "direct Pydantic validation",
            exc,
        )
        decision = GodReviewOutput.model_validate(repaired)

    if _is_safe_noop_decision(decision):
        _log.warning(
            "god_review: accepting safe-noop fallback for universe %s tick %d",
            input.universe_id,
            input.current_tick,
        )

    # 5. Persist the decision artifact.  Ledger is optional (test mode).
    if ledger is not None:
        try:
            artifacts.write_god_decision(
                ledger,
                input.universe_id,
                input.current_tick,
                decision.model_dump(mode="json"),
            )
        except Exception as exc:  # noqa: BLE001 — paranoid; never block engine
            _log.warning(
                "god_review: failed to persist decision artifact: %s", exc
            )

    return decision


__all__ = ["GodReviewInput", "god_review"]
