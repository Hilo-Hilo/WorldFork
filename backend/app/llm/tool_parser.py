"""LLM-output parser.

Parses raw LLM responses into the typed Pydantic decision models defined in
``backend.app.schemas.llm``.  Validates the *outer* JSON shape against the
matching prompt-contract JSONSchema in the SoT bundle.  Per-tool ``args``
validation lives in :mod:`backend.app.llm.tool_validators`.
"""
from __future__ import annotations

import json
import logging
import re
from copy import deepcopy
from typing import Any

import json_repair
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JSONSchemaValidationError

from backend.app.schemas.llm import (
    CohortDecisionOutput,
    GodReviewOutput,
    HeroDecisionOutput,
)
from backend.app.storage.sot_loader import SoTBundle

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ToolParseError(Exception):
    """Raised when LLM output cannot be parsed or validated."""

    def __init__(
        self,
        message: str,
        *,
        validator_message: str | None = None,
        payload_excerpt: str | None = None,
    ) -> None:
        super().__init__(message)
        self.validator_message = validator_message
        self.payload_excerpt = payload_excerpt


# ---------------------------------------------------------------------------
# Loose JSON parsing (final-mile safety net per the provider fallback policy /)
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def parse_json_loosely(text: str) -> dict:
    """Attempt to recover a JSON object from a noisy LLM response.

    Strategy (in order):
      1. Strip ```json ...``` fences if present.
      2. Trim whitespace.
      3. ``json.loads`` directly.
      4. If that fails, slice from the first ``{`` to the last ``}`` and retry.
      5. Balance braces by truncating trailing garbage and retry one more time.

    Raises:
        ToolParseError: if no parse strategy succeeds.
    """
    if not isinstance(text, str):
        raise ToolParseError(
            "parse_json_loosely received non-string input",
            payload_excerpt=str(text)[:200],
        )

    candidate = text.strip()

    # 1. Strip the first fenced block (we don't try to handle multiple fences).
    m = _FENCE_RE.search(candidate)
    if m:
        candidate = m.group(1).strip()

    # 2. Direct parse.
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # 3. Slice from first { to last } and retry.
    first = candidate.find("{")
    last = candidate.rfind("}")
    if first != -1 and last != -1 and last > first:
        sliced = candidate[first : last + 1]
        try:
            parsed = json.loads(sliced)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            # 4. Balance-trim: drop trailing chars one at a time looking for
            #    a valid prefix.  Bounded to 200 attempts to keep this O(n).
            for trim_back in range(1, min(200, len(sliced))):
                attempt = sliced[: -trim_back]
                # Make sure we still close at least one brace.
                if attempt.count("}") < 1:
                    break
                # Re-balance braces by appending matching closers if open
                # count exceeds close count.
                opens = attempt.count("{")
                closes = attempt.count("}")
                if opens > closes:
                    attempt = attempt + ("}" * (opens - closes))
                try:
                    parsed = json.loads(attempt)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    continue

    repaired = _repair_json_object(candidate)
    if repaired is not None:
        return repaired

    raise ToolParseError(
        "Unable to recover JSON object from response",
        payload_excerpt=candidate[:300],
    )


def _repair_json_object(candidate: str) -> dict | None:
    stripped = candidate.lstrip()
    if not stripped.startswith("{") and not stripped.startswith("```"):
        return None
    try:
        repaired = json_repair.repair_json(
            candidate,
            return_objects=True,
            skip_json_loads=True,
            ensure_ascii=False,
        )
    except Exception:
        return None
    return repaired if isinstance(repaired, dict) else None


# ---------------------------------------------------------------------------
# ToolParser
# ---------------------------------------------------------------------------


class ToolParser:
    """Validate + parse LLM outputs against SoT prompt contracts."""

    def __init__(self, sot: SoTBundle) -> None:
        self.sot = sot
        self._validators: dict[str, Draft202012Validator] = {}
        for name, schema in sot.prompt_contracts.items():
            try:
                self._validators[name] = Draft202012Validator(schema)
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "Could not build Draft202012Validator for %r: %s", name, exc
                )

    # ---------------- per-actor parsers ----------------

    def parse_cohort_output(self, payload: dict) -> CohortDecisionOutput:
        payload = self._normalize_schema_bounded_numbers("cohort_decision_schema", payload)
        self._validate("cohort_decision_schema", payload)
        try:
            return CohortDecisionOutput.model_validate(payload)
        except Exception as exc:  # pydantic.ValidationError
            raise ToolParseError(
                "CohortDecisionOutput Pydantic validation failed",
                validator_message=str(exc),
                payload_excerpt=str(payload)[:300],
            ) from exc

    def parse_hero_output(self, payload: dict) -> HeroDecisionOutput:
        payload = self._normalize_schema_bounded_numbers("hero_decision_schema", payload)
        self._validate("hero_decision_schema", payload)
        try:
            return HeroDecisionOutput.model_validate(payload)
        except Exception as exc:
            raise ToolParseError(
                "HeroDecisionOutput Pydantic validation failed",
                validator_message=str(exc),
                payload_excerpt=str(payload)[:300],
            ) from exc

    def parse_god_output(self, payload: dict) -> GodReviewOutput:
        payload = self._normalize_schema_bounded_numbers("god_review_schema", payload)
        self._validate("god_review_schema", payload)
        # Discriminator check: branch_delta required iff decision is spawn_*.
        decision = payload.get("decision")
        bd = payload.get("branch_delta")
        if decision in ("spawn_candidate", "spawn_active") and not bd:
            raise ToolParseError(
                f"branch_delta is required when decision={decision!r}",
                validator_message="branch_delta missing for spawn_* decision",
                payload_excerpt=str(payload)[:300],
            )
        if decision in ("continue", "freeze", "kill", "complete_universe") and bd:
            # Coerce to None — engine treats this leniently.
            _log.warning(
                "branch_delta supplied for decision=%r; ignoring", decision
            )
            payload = {**payload, "branch_delta": None}
        try:
            return GodReviewOutput.model_validate(payload)
        except Exception as exc:
            raise ToolParseError(
                "GodReviewOutput Pydantic validation failed",
                validator_message=str(exc),
                payload_excerpt=str(payload)[:300],
            ) from exc

    def parse_initializer_output(self, payload: dict) -> dict:
        """Initializer output is open-ended — return the validated dict."""
        payload = self._normalize_schema_bounded_numbers("initializer_schema", payload)
        self._validate("initializer_schema", payload)
        return dict(payload)

    # ---------------- tool-call extraction ----------------

    @staticmethod
    def extract_tool_calls(decision: dict) -> list[dict]:
        """Flatten ``public_actions + event_actions + social_actions`` calls.

        Each output entry is ``{tool_id, args, source}`` where ``source`` is
        one of ``"public" | "event" | "social"``.  Items lacking a tool_id or
        args field are skipped with a debug log; callers are responsible for
        sanitization with the ``Validators`` layer.
        """
        results: list[dict] = []
        for source, key in (
            ("public", "public_actions"),
            ("event", "event_actions"),
            ("social", "social_actions"),
        ):
            arr = decision.get(key) or []
            if not isinstance(arr, list):
                continue
            for item in arr:
                if not isinstance(item, dict):
                    continue
                tool_id = item.get("tool_id")
                args = item.get("args")
                if not tool_id or args is None:
                    _log.debug(
                        "skipping malformed tool call in %s: %r", key, item
                    )
                    continue
                results.append(
                    {"tool_id": tool_id, "args": args, "source": source}
                )
        return results

    # ---------------- internals ----------------

    def _validate(self, schema_name: str, payload: Any) -> None:
        if not isinstance(payload, dict):
            raise ToolParseError(
                f"Expected dict payload for {schema_name}, got {type(payload).__name__}",
                payload_excerpt=str(payload)[:300],
            )
        validator = self._validators.get(schema_name)
        if validator is None:
            raise ToolParseError(
                f"No JSONSchema validator available for {schema_name!r}; "
                "ensure SoT prompt_contracts loaded.",
            )
        try:
            validator.validate(payload)
        except JSONSchemaValidationError as exc:
            raise ToolParseError(
                f"JSONSchema validation failed for {schema_name}: {exc.message}",
                validator_message=exc.message,
                payload_excerpt=str(payload)[:300],
            ) from exc

    def _normalize_schema_bounded_numbers(self, schema_name: str, payload: dict) -> dict:
        """Clamp numeric LLM slips to the JSONSchema bounds before validation.

        Live models occasionally emit signed values for fields whose schema
        represents magnitude/proportion (for example material stake or
        population counts). Raw output is still written to the ledger before
        parsing; this normalization only affects the validated runtime payload.
        """
        schema = self.sot.prompt_contracts.get(schema_name)
        if not isinstance(schema, dict):
            return dict(payload)
        normalized = deepcopy(payload)
        self._normalize_value(normalized, schema, root_schema=schema, key=None)
        return normalized

    def _resolve_schema(self, schema: dict, root_schema: dict) -> dict:
        ref = schema.get("$ref")
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return schema
        cur: Any = root_schema
        for part in ref[2:].split("/"):
            if not isinstance(cur, dict):
                return schema
            cur = cur.get(part)
        return cur if isinstance(cur, dict) else schema

    def _normalize_value(
        self,
        value: Any,
        schema: dict,
        *,
        root_schema: dict,
        key: str | None,
    ) -> Any:
        schema = self._resolve_schema(schema, root_schema)
        if isinstance(value, dict):
            props_value = schema.get("properties")
            props = props_value if isinstance(props_value, dict) else {}
            additional = schema.get("additionalProperties")
            for child_key, child_value in list(value.items()):
                child_schema = props.get(child_key)
                if child_schema is None and isinstance(additional, dict):
                    child_schema = additional
                if isinstance(child_schema, dict):
                    value[child_key] = self._normalize_value(
                        child_value,
                        child_schema,
                        root_schema=root_schema,
                        key=child_key,
                    )
            return value
        if isinstance(value, list):
            item_schema = schema.get("items")
            if isinstance(item_schema, dict):
                for idx, item in enumerate(list(value)):
                    value[idx] = self._normalize_value(
                        item,
                        item_schema,
                        root_schema=root_schema,
                        key=key,
                    )
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            minimum = schema.get("minimum")
            maximum = schema.get("maximum")
            if isinstance(minimum, (int, float)) and value < minimum:
                if key in {"population_total", "max_child_cohorts"}:
                    value = abs(value)
                value = max(value, minimum)
            if isinstance(maximum, (int, float)) and value > maximum:
                value = maximum
            return value
        return value
