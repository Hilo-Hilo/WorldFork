"""Typed writer helpers that wrap ``Ledger.write_artifact`` / ``Ledger.append_jsonl``.

All path conventions follow runtime artifact storage.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.app.storage.ledger import Ledger


# ---------------------------------------------------------------------------
# Tick-scoped writers
# ---------------------------------------------------------------------------

def _tick_prefix(universe_id: str, tick: int) -> str:
    return f"universes/{universe_id}/ticks/tick_{tick:03d}"


def write_llm_call(
    ledger: Ledger,
    universe_id: str,
    tick: int,
    call_id: str,
    payload: dict,
) -> str:
    """Write an LLM call artifact.  Returns sha256."""
    rel = f"{_tick_prefix(universe_id, tick)}/llm_calls/{call_id}.json"
    return ledger.write_artifact(rel, payload, immutable=True)


def write_tick_state_before(
    ledger: Ledger,
    universe_id: str,
    tick: int,
    state: dict,
) -> str:
    rel = f"{_tick_prefix(universe_id, tick)}/universe_state_before.json"
    return ledger.write_artifact(rel, state, immutable=True)


def write_tick_state_after(
    ledger: Ledger,
    universe_id: str,
    tick: int,
    state: dict,
) -> str:
    rel = f"{_tick_prefix(universe_id, tick)}/universe_state_after.json"
    return ledger.write_artifact(rel, state, immutable=True)


def write_god_decision(
    ledger: Ledger,
    universe_id: str,
    tick: int,
    decision: dict,
) -> str:
    rel = f"{_tick_prefix(universe_id, tick)}/god/decision.json"
    return ledger.write_artifact(rel, decision, immutable=True)


def write_visible_packet(
    ledger: Ledger,
    universe_id: str,
    tick: int,
    actor_id: str,
    packet: dict,
) -> str:
    rel = f"{_tick_prefix(universe_id, tick)}/visible_packets/{actor_id}.json"
    return ledger.write_artifact(rel, packet, immutable=True)


def write_parsed_decisions(
    ledger: Ledger,
    universe_id: str,
    tick: int,
    decisions: list[dict],
) -> str:
    rel = f"{_tick_prefix(universe_id, tick)}/parsed_decisions.json"
    payload: dict = {"decisions": decisions}
    return ledger.write_artifact(rel, payload, immutable=True)


def write_tool_calls(
    ledger: Ledger,
    universe_id: str,
    tick: int,
    calls: list[dict],
) -> str:
    rel = f"{_tick_prefix(universe_id, tick)}/tool_calls.json"
    payload: dict = {"tool_calls": calls}
    return ledger.write_artifact(rel, payload, immutable=True)


# ---------------------------------------------------------------------------
# Universe-scoped log appenders (JSONL, mutable)
# ---------------------------------------------------------------------------

def _logs_prefix(universe_id: str) -> str:
    return f"universes/{universe_id}/logs"


def append_event_log(
    ledger: Ledger,
    universe_id: str,
    event: dict,
) -> None:
    rel = f"{_logs_prefix(universe_id)}/event_log.jsonl"
    ledger.append_jsonl(rel, event)


def append_social_posts(
    ledger: Ledger,
    universe_id: str,
    posts: list[dict],
) -> None:
    rel = f"{_logs_prefix(universe_id)}/social_posts.jsonl"
    for post in posts:
        ledger.append_jsonl(rel, post)


def append_tool_call_log(
    ledger: Ledger,
    universe_id: str,
    calls: list[dict],
) -> None:
    rel = f"{_logs_prefix(universe_id)}/tool_calls.jsonl"
    for call in calls:
        ledger.append_jsonl(rel, call)


def append_metrics(
    ledger: Ledger,
    universe_id: str,
    metrics: dict,
) -> None:
    rel = f"{_logs_prefix(universe_id)}/metrics.jsonl"
    ledger.append_jsonl(rel, metrics)
