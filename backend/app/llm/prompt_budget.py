from __future__ import annotations

import json
from typing import Any


DEFAULT_EVENT_QUEUE_MAX_CHARS = 16_000
DEFAULT_GOD_BUNDLE_MAX_CHARS = 48_000
MAX_OMITTED_TITLES = 6

EVENT_QUEUE_SECTION_LIMITS = {
    "due_events": 16,
    "own_queued_events": 12,
    "upcoming_events": 12,
    "past_events": 8,
    "visible_events": 12,
}

EVENT_QUEUE_SECTION_PRIORITY = [
    "due_events",
    "own_queued_events",
    "upcoming_events",
    "past_events",
    "visible_events",
]

GOD_BUNDLE_SECTION_LIMITS = {
    "executed_events": 16,
    "event_summaries": 16,
    "queued_events": 16,
    "social_observations": 20,
    "split_candidates": 8,
    "merge_candidates": 8,
    "emergence_candidates": 8,
    "agent_outputs": 12,
}

GOD_BUNDLE_SECTION_PRIORITY = [
    "executed_events",
    "event_summaries",
    "queued_events",
    "split_candidates",
    "merge_candidates",
    "emergence_candidates",
    "social_observations",
    "agent_outputs",
]


def budget_event_queue_context(
    event_queue: dict[str, Any],
    *,
    max_chars: int = DEFAULT_EVENT_QUEUE_MAX_CHARS,
) -> dict[str, Any]:
    """Return a deterministic, observable event-queue prompt window.

    This intentionally does not silently dump an unbounded queue into prompts. It
    ranks sections by decision relevance, compacts event rows, and records what
    was omitted so the receiving agent knows the prompt is a window, not the full
    database.
    """

    if not isinstance(event_queue, dict):
        return {}
    existing_budget = event_queue.get("prompt_budget")
    if isinstance(existing_budget, dict) and existing_budget.get("kind") == "event_queue":
        return event_queue

    budgeted: dict[str, Any] = {
        key: value
        for key, value in event_queue.items()
        if key not in EVENT_QUEUE_SECTION_LIMITS and key != "prompt_budget"
    }
    budget_meta = {
        "kind": "event_queue",
        "max_chars": max_chars,
        "ordering_policy": (
            "Always prefer due/current events, actor-owned queued events, and nearest future events. "
            "Omitted rows remain in the database and are summarized here."
        ),
        "sections": {},
    }

    for section in EVENT_QUEUE_SECTION_PRIORITY:
        rows = event_queue.get(section) or []
        if not isinstance(rows, list):
            rows = []
        limit = EVENT_QUEUE_SECTION_LIMITS[section]
        included = [_compact_event_row(row) for row in rows[:limit] if isinstance(row, dict)]
        omitted = rows[limit:]
        budgeted[section] = included
        budget_meta["sections"][section] = _omission_summary(
            rows=rows,
            included_count=len(included),
            omitted=omitted,
        )

    _fit_sections_to_budget(
        budgeted,
        budget_meta,
        section_priority=EVENT_QUEUE_SECTION_PRIORITY,
        max_chars=max_chars,
    )
    budget_meta["omitted_total"] = sum(
        int(section.get("omitted_count") or 0)
        for section in budget_meta["sections"].values()
        if isinstance(section, dict)
    )
    budget_meta["estimated_chars"] = _json_chars(budgeted)
    budgeted["prompt_budget"] = budget_meta
    return budgeted


def budget_god_provisional_bundle(
    provisional_bundle: dict[str, Any],
    *,
    max_chars: int = DEFAULT_GOD_BUNDLE_MAX_CHARS,
) -> dict[str, Any]:
    """Return the God review prompt packet with bounded high-volume sections."""

    if not isinstance(provisional_bundle, dict):
        return {}

    budgeted = {
        key: _compact_value(value)
        for key, value in provisional_bundle.items()
        if key not in GOD_BUNDLE_SECTION_LIMITS and key != "prompt_budget"
    }
    budget_meta = {
        "kind": "god_provisional_bundle",
        "max_chars": max_chars,
        "ordering_policy": (
            "God review receives bounded tick deltas. High-volume raw event, social, and actor sections "
            "are ranked and omitted rows are summarized instead of silently dropped."
        ),
        "sections": {},
    }

    for section in GOD_BUNDLE_SECTION_PRIORITY:
        rows = provisional_bundle.get(section) or []
        if not isinstance(rows, list):
            rows = []
        limit = GOD_BUNDLE_SECTION_LIMITS[section]
        included = [_compact_value(row) for row in rows[:limit]]
        omitted = rows[limit:]
        budgeted[section] = included
        budget_meta["sections"][section] = _omission_summary(
            rows=rows,
            included_count=len(included),
            omitted=omitted,
        )

    _fit_sections_to_budget(
        budgeted,
        budget_meta,
        section_priority=GOD_BUNDLE_SECTION_PRIORITY,
        max_chars=max_chars,
    )
    budget_meta["omitted_total"] = sum(
        int(section.get("omitted_count") or 0)
        for section in budget_meta["sections"].values()
        if isinstance(section, dict)
    )
    budget_meta["estimated_chars"] = _json_chars(budgeted)
    budgeted["prompt_budget"] = budget_meta
    return budgeted


def _fit_sections_to_budget(
    payload: dict[str, Any],
    budget_meta: dict[str, Any],
    *,
    section_priority: list[str],
    max_chars: int,
) -> None:
    if _json_chars(payload) <= max_chars:
        return
    for section in reversed(section_priority):
        rows = payload.get(section)
        if not isinstance(rows, list):
            continue
        while rows and _json_chars(payload) > max_chars:
            removed = rows.pop()
            section_meta = budget_meta["sections"].setdefault(section, {})
            section_meta["included_count"] = len(rows)
            section_meta["omitted_count"] = int(section_meta.get("omitted_count") or 0) + 1
            titles = section_meta.setdefault("omitted_titles", [])
            title = _title_for_row(removed)
            if title and len(titles) < MAX_OMITTED_TITLES:
                titles.append(title)
            section_meta["budget_trimmed"] = True
        if _json_chars(payload) <= max_chars:
            return


def _compact_event_row(row: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "event_id",
        "event_type",
        "title",
        "description",
        "created_tick",
        "scheduled_tick",
        "status",
        "creator_actor_id",
        "expected_impact",
        "actual_impact",
        "source",
        "inherited_from_event_id",
    )
    return {key: _compact_value(row.get(key), string_limit=600) for key in fields if row.get(key) not in (None, {}, [])}


def _compact_value(value: Any, *, depth: int = 0, string_limit: int = 900) -> Any:
    if depth > 4:
        return _excerpt(str(value), 240)
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 12:
                compact["_truncated_keys"] = len(value) - 12
                break
            compact[str(key)] = _compact_value(item, depth=depth + 1, string_limit=string_limit)
        return compact
    if isinstance(value, list):
        items = [_compact_value(item, depth=depth + 1, string_limit=string_limit) for item in value[:12]]
        if len(value) > 12:
            items.append({"_truncated_items": len(value) - 12})
        return items
    if isinstance(value, str):
        return _excerpt(value, string_limit)
    return value


def _omission_summary(*, rows: list[Any], included_count: int, omitted: list[Any]) -> dict[str, Any]:
    summary = {
        "total_count": len(rows),
        "included_count": included_count,
        "omitted_count": max(0, len(rows) - included_count),
    }
    omitted_titles = [_title_for_row(row) for row in omitted if _title_for_row(row)]
    if omitted_titles:
        summary["omitted_titles"] = omitted_titles[:MAX_OMITTED_TITLES]
    omitted_preview = [_omitted_preview_row(row) for row in omitted[:3] if isinstance(row, dict)]
    if omitted_preview:
        summary["omitted_preview"] = omitted_preview
    event_type_counts = _count_field(omitted, "event_type")
    if event_type_counts:
        summary["omitted_event_type_counts"] = event_type_counts
    status_counts = _count_field(omitted, "status")
    if status_counts:
        summary["omitted_status_counts"] = status_counts
    tick_values = [_tick_for_row(row) for row in omitted]
    tick_values = [tick for tick in tick_values if tick is not None]
    if tick_values:
        summary["omitted_tick_range"] = [min(tick_values), max(tick_values)]
    return summary


def _omitted_preview_row(row: dict[str, Any]) -> dict[str, Any]:
    preview = {
        "title": _title_for_row(row),
        "event_type": row.get("event_type"),
        "status": row.get("status"),
        "scheduled_tick": row.get("scheduled_tick"),
        "source": row.get("source"),
    }
    if row.get("description"):
        preview["description_excerpt"] = _excerpt(str(row["description"]), 180)
    return {key: value for key, value in preview.items() if value not in (None, "", {}, [])}


def _count_field(rows: list[Any], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = row.get(field)
        if value in (None, ""):
            continue
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _title_for_row(row: Any) -> str | None:
    if not isinstance(row, dict):
        return None
    value = row.get("title") or row.get("summary") or row.get("body") or row.get("action_type")
    if value in (None, ""):
        return None
    return _excerpt(str(value), 120)


def _tick_for_row(row: Any) -> int | None:
    if not isinstance(row, dict):
        return None
    for key in ("scheduled_tick", "tick_index", "created_tick"):
        value = row.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _excerpt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def _json_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=True, default=str))
