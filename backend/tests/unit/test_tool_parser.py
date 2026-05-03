"""Unit tests for backend.app.llm.tool_parser."""
from __future__ import annotations

import pytest

from backend.app.llm.tool_parser import (
    ToolParseError,
    ToolParser,
    parse_json_loosely,
)
from backend.app.storage.sot_loader import load_sot


@pytest.fixture(scope="module")
def parser():
    return ToolParser(load_sot())


def _minimal_cohort_payload() -> dict:
    return {
        "public_actions": [],
        "event_actions": [],
        "social_actions": [
            {"tool_id": "stay_silent", "args": {"reason": "spiral_of_silence"}}
        ],
        "self_ratings": {
            "emotions": {"anger": 5.5},
            "issue_stance": {"primary_issue": -0.2},
            "perceived_majority": {"primary_issue": 0.3},
            "willingness_to_speak": 0.4,
        },
        "split_merge_proposals": [],
        "decision_rationale": {
            "main_factors": ["high fear of isolation"],
            "uncertainty": "medium",
        },
    }


def _minimal_god_payload(decision: str = "continue", branch_delta=None) -> dict:
    payload = {
        "decision": decision,
        "marked_key_events": [],
        "tick_summary": "The universe continued without significant divergence this tick.",
        "rationale": {
            "main_factors": ["calm metrics"],
            "confidence": "high",
        },
    }
    if branch_delta is not None:
        payload["branch_delta"] = branch_delta
    return payload


# ---------------------------------------------------------------------------
# Cohort
# ---------------------------------------------------------------------------


class TestParseCohortOutput:
    def test_parses_minimal_valid_payload(self, parser):
        decision = parser.parse_cohort_output(_minimal_cohort_payload())
        assert decision.decision_rationale.uncertainty == "medium"
        assert len(decision.social_actions) == 1

    def test_rejects_missing_required_field(self, parser):
        bad = _minimal_cohort_payload()
        del bad["decision_rationale"]
        with pytest.raises(ToolParseError):
            parser.parse_cohort_output(bad)

    def test_rejects_bad_uncertainty_value(self, parser):
        bad = _minimal_cohort_payload()
        bad["decision_rationale"]["uncertainty"] = "extreme"
        with pytest.raises(ToolParseError):
            parser.parse_cohort_output(bad)


# ---------------------------------------------------------------------------
# Tool-call extraction
# ---------------------------------------------------------------------------


class TestExtractToolCalls:
    def test_flattens_three_groups(self, parser):
        decision = {
            "public_actions": [{"tool_id": "support_event", "args": {"event_id": "e1"}}],
            "event_actions": [{"tool_id": "queue_event", "args": {"event_type": "protest", "title": "x", "scheduled_tick": 5}}],
            "social_actions": [{"tool_id": "stay_silent", "args": {}}],
        }
        calls = parser.extract_tool_calls(decision)
        sources = {c["source"] for c in calls}
        ids = {c["tool_id"] for c in calls}
        assert sources == {"public", "event", "social"}
        assert ids == {"support_event", "queue_event", "stay_silent"}
        assert len(calls) == 3

    def test_skips_malformed_entries(self, parser):
        decision = {
            "social_actions": [
                {"tool_id": "stay_silent", "args": {}},
                {"tool_id": "no_args"},  # missing args
                "not_a_dict",
                {"args": {}},  # missing tool_id
            ],
        }
        calls = parser.extract_tool_calls(decision)
        assert len(calls) == 1
        assert calls[0]["tool_id"] == "stay_silent"


# ---------------------------------------------------------------------------
# parse_json_loosely
# ---------------------------------------------------------------------------


class TestParseJsonLoosely:
    def test_strips_fenced_blocks(self):
        text = """```json
{"hello": 1, "world": [1,2,3]}
```"""
        parsed = parse_json_loosely(text)
        assert parsed == {"hello": 1, "world": [1, 2, 3]}

    def test_handles_uppercase_fence(self):
        text = """```JSON
{"a": 2}
```"""
        parsed = parse_json_loosely(text)
        assert parsed == {"a": 2}

    def test_strips_leading_chatter(self):
        text = (
            "Sure! Here's the JSON:\n"
            '{"decision": "continue", "tick_summary": "ok"}'
        )
        parsed = parse_json_loosely(text)
        assert parsed["decision"] == "continue"

    def test_raises_on_unrecoverable(self):
        with pytest.raises(ToolParseError):
            parse_json_loosely("totally not json {{{")


# ---------------------------------------------------------------------------
# God output discriminator
# ---------------------------------------------------------------------------


class TestParseGodOutput:
    def test_continue_with_no_branch_delta(self, parser):
        out = parser.parse_god_output(_minimal_god_payload(decision="continue"))
        assert out.decision == "continue"
        assert out.branch_delta is None

    def test_spawn_active_requires_branch_delta(self, parser):
        with pytest.raises(ToolParseError):
            parser.parse_god_output(_minimal_god_payload(decision="spawn_active"))

    def test_spawn_active_with_delta_parses(self, parser):
        delta = {
            "type": "counterfactual_event_rewrite",
            "target_event_id": "evt_1",
            "parent_version": "Defensive statement.",
            "child_version": "Apology + audit.",
        }
        payload = _minimal_god_payload(decision="spawn_active", branch_delta=delta)
        out = parser.parse_god_output(payload)
        assert out.decision == "spawn_active"
        assert out.branch_delta is not None
        assert out.branch_delta.type == "counterfactual_event_rewrite"

    def test_continue_with_branch_delta_is_coerced_to_none(self, parser):
        # Engine is lenient: coerces stray branch_delta to None instead of erroring.
        payload = _minimal_god_payload(
            decision="continue",
            branch_delta={
                "type": "counterfactual_event_rewrite",
                "target_event_id": "x",
                "parent_version": "a",
                "child_version": "b",
            },
        )
        out = parser.parse_god_output(payload)
        assert out.branch_delta is None
