"""Per-tool-call validators and decision sanitizers.

The :mod:`tool_parser` module verifies the *outer* shape of an LLM decision
against a prompt-contract JSONSchema.  This module verifies that each tool
call inside a decision is well-formed against its tool's ``json_schema`` from
``social_action_tools.json`` and that referenced taxonomy keys (emotion,
issue_stance, event_type) all live in the SoT.
"""
from __future__ import annotations

import logging
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JSONSchemaValidationError

from backend.app.schemas.actors import CohortState, HeroState
from backend.app.schemas.llm import CohortDecisionOutput, HeroDecisionOutput
from backend.app.storage.sot_loader import SoTBundle

_log = logging.getLogger(__name__)


_MAX_POST_CONTENT_LENGTH = 2000


class ValidationContext:
    """Validate tool calls against SoT and the calling actor's allowed_tools."""

    def __init__(
        self,
        sot: SoTBundle,
        cohort_or_hero: CohortState | HeroState | None,
        allowed_tool_ids: set[str],
        *,
        current_tick: int | None = None,
        max_schedule_horizon_ticks: int | None = None,
    ) -> None:
        self.sot = sot
        self.actor = cohort_or_hero
        self.allowed_tool_ids = set(allowed_tool_ids)
        self.current_tick = current_tick
        self.max_schedule_horizon_ticks = max_schedule_horizon_ticks

        # Index tool registry by id so we can validate args quickly.
        self._tools_by_id: dict[str, dict] = {}
        self._tool_validators: dict[str, Draft202012Validator] = {}
        for tool in sot.social_action_tools.get("tools", []):
            tid = tool.get("tool_id")
            if not tid:
                continue
            self._tools_by_id[tid] = tool
            schema = tool.get("json_schema", {})
            try:
                self._tool_validators[tid] = Draft202012Validator(schema)
            except Exception as exc:  # noqa: BLE001
                _log.warning("Bad tool schema for %s: %s", tid, exc)

        # Pre-build emotion / stance / event-type key sets for cheap lookups.
        self._known_emotions = self._collect_keys(sot.emotions, "items", "emotions")
        self._known_event_types = self._collect_keys(
            sot.event_types,
            "items",
            "event_types",
        )
        self._known_stance_axes = self._collect_stance_keys(sot.issue_stance_axes)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_tool_call(self, call: dict) -> tuple[bool, str | None]:
        """Validate one ``{tool_id, args, source?}`` entry.

        Returns ``(ok, reason)`` — ``reason`` is ``None`` on success.
        """
        tool_id = call.get("tool_id")
        args = call.get("args")

        if not isinstance(tool_id, str) or not tool_id:
            return False, "missing tool_id"
        if not isinstance(args, dict):
            return False, "args must be an object"

        # 1. allowed for actor + known in SoT registry
        if tool_id not in self._tools_by_id:
            return False, f"unknown tool_id: {tool_id}"
        if self.allowed_tool_ids and tool_id not in self.allowed_tool_ids:
            return False, f"tool_id {tool_id} not in actor's allowed_tools"

        # 2. args validate against the tool's JSONSchema
        validator = self._tool_validators.get(tool_id)
        if validator is not None:
            try:
                validator.validate(args)
            except JSONSchemaValidationError as exc:
                return False, f"args invalid for {tool_id}: {exc.message}"

        # 3. content length sanity check for post-creation tools
        if tool_id == "create_social_post":
            content = args.get("content", "")
            if isinstance(content, str) and len(content) > _MAX_POST_CONTENT_LENGTH:
                return False, (
                    f"create_social_post.content exceeds {_MAX_POST_CONTENT_LENGTH} chars"
                )

        # 4. event_type must be in SoT for queue_event
        if tool_id == "queue_event":
            etype = args.get("event_type")
            if etype not in self._known_event_types:
                return False, f"unknown event_type: {etype!r}"
            horizon_error = self._validate_schedule_horizon(args.get("scheduled_tick"))
            if horizon_error:
                return False, horizon_error

        emotion_signal = args.get("emotion_signal")
        if isinstance(emotion_signal, dict):
            for key in emotion_signal:
                if key not in self._known_emotions:
                    return False, f"unknown emotion key in emotion_signal: {key!r}"

        # 5. self-rate emotions/stance keys must be in SoT
        if tool_id == "self_rate_emotions":
            emotions = args.get("emotions") or {}
            for key in emotions:
                if key not in self._known_emotions:
                    return False, f"unknown emotion key: {key!r}"
        if tool_id == "self_rate_issue_stance":
            stance = args.get("issue_stance") or {}
            for key in stance:
                if key not in self._known_stance_axes:
                    return False, f"unknown issue_stance axis: {key!r}"
        if tool_id == "estimate_perceived_majority":
            pm = args.get("perceived_majority") or {}
            for key in pm:
                if key not in self._known_stance_axes:
                    return False, f"unknown stance axis in perceived_majority: {key!r}"

        return True, None

    def _validate_schedule_horizon(self, scheduled_tick: Any) -> str | None:
        if self.max_schedule_horizon_ticks is None or self.current_tick is None:
            return None
        if not isinstance(scheduled_tick, int):
            return None
        max_tick = self.current_tick + self.max_schedule_horizon_ticks
        if scheduled_tick > max_tick:
            return (
                f"scheduled_tick {scheduled_tick} exceeds max_schedule_horizon_ticks "
                f"({self.max_schedule_horizon_ticks}; max_tick={max_tick})"
            )
        return None

    def validate_decision(
        self, decision: CohortDecisionOutput | HeroDecisionOutput
    ) -> list[str]:
        """Validate every tool call inside a decision; return error strings."""
        errors: list[str] = []
        groups = (
            ("public_actions", decision.public_actions),
            ("event_actions", decision.event_actions),
            ("social_actions", decision.social_actions),
        )
        # CohortDecisionOutput has split_merge_proposals; HeroDecisionOutput
        # does not.  Use getattr to stay compatible.
        smp = getattr(decision, "split_merge_proposals", None)
        if smp:
            groups = groups + (("split_merge_proposals", smp),)

        for label, items in groups:
            for idx, call in enumerate(items or []):
                # Pydantic models normalize to dict via model_dump if needed.
                call_dict = call if isinstance(call, dict) else dict(call)
                ok, reason = self.validate_tool_call(call_dict)
                if not ok:
                    errors.append(f"{label}[{idx}]: {reason}")
        return errors

    def sanitize_decision(self, decision: dict) -> dict:
        """Return a copy of *decision* with invalid tool calls dropped.

        Logs a WARNING for every dropped action so the engine has an audit
        trail without rejecting the whole tick.
        """
        if not isinstance(decision, dict):
            return decision

        sanitized = dict(decision)
        for key in ("public_actions", "event_actions", "social_actions",
                    "split_merge_proposals"):
            arr = sanitized.get(key)
            if not isinstance(arr, list):
                continue
            kept: list[dict] = []
            for idx, call in enumerate(arr):
                if not isinstance(call, dict):
                    _log.warning(
                        "sanitize_decision dropped non-dict %s[%d]: %r",
                        key, idx, call,
                    )
                    continue
                ok, reason = self.validate_tool_call(call)
                if ok:
                    kept.append(call)
                else:
                    _log.warning(
                        "sanitize_decision dropped invalid %s[%d]: %s — %r",
                        key, idx, reason, call,
                    )
            sanitized[key] = kept
        return sanitized

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _collect_keys(obj: Any, *candidates: str) -> set[str]:
        """Collect taxonomy keys from list, wrapped-list, or mapping-shaped SoT blobs."""
        if isinstance(obj, list):
            items = obj
        elif isinstance(obj, dict):
            items = None
            for c in candidates:
                v = obj.get(c)
                if isinstance(v, list):
                    items = v
                    break
                if isinstance(v, dict):
                    items = list(v.values())
                    break
            if items is None:
                items = [
                    value
                    for key, value in obj.items()
                    if key not in {"version", "scale", "note", "notes", "metadata"}
                ]
        else:
            items = []

        keys: set[str] = set()
        for item in items:
            if isinstance(item, str):
                keys.add(item)
            elif isinstance(item, dict) and "key" in item and isinstance(item["key"], str):
                keys.add(item["key"])
        return keys

    @staticmethod
    def _collect_stance_keys(obj: Any) -> set[str]:
        """Stance axes live under ``default_axes`` plus optional extensions."""
        keys: set[str] = set()
        if not isinstance(obj, dict):
            return keys
        for arr_key in ("default_axes", "items", "axes", "scenario_axes_extension"):
            arr = obj.get(arr_key)
            if isinstance(arr, list):
                for it in arr:
                    if isinstance(it, str):
                        keys.add(it)
                    elif isinstance(it, dict) and "key" in it and isinstance(it["key"], str):
                        keys.add(it["key"])
        return keys
