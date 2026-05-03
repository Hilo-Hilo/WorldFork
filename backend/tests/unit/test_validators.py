"""Unit tests for backend.app.llm.tool_validators."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.app.domains.actor import agent_engine
from backend.app.domains.actor.agent_engine import (
    _normalize_event_actions,
    _normalize_self_ratings,
    _normalize_social_actions,
    run_actor_decision,
)
from backend.app.llm.tool_validators import ValidationContext
from backend.app.storage.sot_loader import load_sot


@pytest.fixture(scope="module")
def sot():
    return load_sot()


@pytest.fixture
def ctx(sot):
    return ValidationContext(
        sot,
        cohort_or_hero=None,
        allowed_tool_ids={
            "create_social_post",
            "comment_on_post",
            "stay_silent",
            "queue_event",
            "self_rate_emotions",
            "self_rate_issue_stance",
            "support_event",
        },
    )


@pytest.fixture
def known_event_type(sot):
    event_types = sot.event_types.get("event_types") or []
    assert event_types, "expected source_of_truth event_types to be populated"
    return event_types[0]


# ---------------------------------------------------------------------------


class TestValidateToolCall:
    def test_rejects_unknown_tool_id(self, ctx):
        ok, reason = ctx.validate_tool_call(
            {"tool_id": "magical_thinking_tool", "args": {}}
        )
        assert not ok
        assert "unknown tool_id" in reason

    def test_rejects_disallowed_tool(self, sot, known_event_type):
        ctx = ValidationContext(sot, None, allowed_tool_ids={"stay_silent"})
        ok, reason = ctx.validate_tool_call(
            {"tool_id": "queue_event", "args": {"event_type": known_event_type, "title": "x", "scheduled_tick": 5}}
        )
        assert not ok
        assert "not in actor's allowed_tools" in reason

    def test_rejects_unknown_event_type(self, ctx):
        ok, reason = ctx.validate_tool_call(
            {"tool_id": "queue_event", "args": {
                "event_type": "intergalactic_war", "title": "x", "scheduled_tick": 5
            }}
        )
        assert not ok
        assert "unknown event_type" in reason

    def test_accepts_known_event_type(self, ctx, known_event_type):
        ok, _ = ctx.validate_tool_call(
            {"tool_id": "queue_event", "args": {
                "event_type": known_event_type, "title": "March", "scheduled_tick": 5
            }}
        )
        assert ok

    def test_rejects_queue_event_beyond_configured_horizon(self, sot, known_event_type):
        ctx = ValidationContext(
            sot,
            None,
            allowed_tool_ids={"queue_event"},
            current_tick=10,
            max_schedule_horizon_ticks=3,
        )
        ok, reason = ctx.validate_tool_call(
            {"tool_id": "queue_event", "args": {
                "event_type": known_event_type, "title": "Too late", "scheduled_tick": 14
            }}
        )
        assert not ok
        assert "max_schedule_horizon_ticks" in reason

    def test_rejects_unknown_emotion_key(self, ctx):
        ok, reason = ctx.validate_tool_call(
            {"tool_id": "self_rate_emotions",
             "args": {"emotions": {"anger": 5.0, "schadenfreude": 8.0}}}
        )
        assert not ok
        assert "schadenfreude" in reason

    def test_rejects_unknown_emotion_signal_key(self, ctx):
        ok, reason = ctx.validate_tool_call(
            {"tool_id": "create_social_post",
             "args": {"platform": "twitter_like", "content": "ok", "emotion_signal": {"schadenfreude": 1.0}}}
        )
        assert not ok
        assert "emotion_signal" in reason

    def test_rejects_overlong_post_content(self, ctx):
        ok, reason = ctx.validate_tool_call(
            {"tool_id": "create_social_post",
             "args": {"platform": "twitter_like", "content": "x" * 2500}}
        )
        assert not ok
        # JSONSchema maxLength catches it first.
        assert "create_social_post" in reason or "2000" in reason


# ---------------------------------------------------------------------------


class TestSanitizeDecision:
    def test_drops_invalid_keeps_valid(self, ctx, known_event_type):
        decision = {
            "social_actions": [
                {"tool_id": "stay_silent", "args": {"reason": "fatigue"}},
                {"tool_id": "made_up_tool", "args": {}},
                {"tool_id": "comment_on_post", "args": {"post_id": "p1", "content": "ok"}},
            ],
            "event_actions": [
                {"tool_id": "queue_event", "args": {
                    "event_type": known_event_type, "title": "March", "scheduled_tick": 5
                }},
                {"tool_id": "queue_event", "args": {
                    "event_type": "fictitious_type", "title": "x", "scheduled_tick": 5
                }},
            ],
            "public_actions": [],
        }
        sanitized = ctx.sanitize_decision(decision)
        social_ids = [c["tool_id"] for c in sanitized["social_actions"]]
        event_ids = [c["tool_id"] for c in sanitized["event_actions"]]
        assert "made_up_tool" not in social_ids
        assert "stay_silent" in social_ids
        assert "comment_on_post" in social_ids
        assert len(event_ids) == 1
        assert event_ids[0] == "queue_event"


class TestAgentDecisionDialectNormalization:
    def test_accepts_schema_valid_event_actions_and_self_ratings(self, known_event_type):
        parsed = {
            "event_actions": [
                {"tool_id": "queue_event", "args": {
                    "event_type": known_event_type,
                    "title": "March",
                    "scheduled_tick": 5,
                }}
            ],
            "self_ratings": {"emotions": {"anger": 6, "hope": 2}},
        }

        assert _normalize_event_actions(parsed) == [{
            "event_type": known_event_type,
            "title": "March",
            "scheduled_tick": 5,
        }]
        assert _normalize_self_ratings(parsed) == [
            {"emotion": "anger", "value": 6.0},
            {"emotion": "hope", "value": 2.0},
        ]

    def test_create_social_post_preserves_content_from_args(self):
        parsed = {
            "social_actions": [
                {"tool_id": "create_social_post", "args": {
                    "platform": "oasis",
                    "content": "Specific schema-valid post body.",
                }}
            ]
        }

        actions = _normalize_social_actions(parsed)
        assert actions[0]["body"] == "Specific schema-valid post body."
        assert actions[0]["content"] == "Specific schema-valid post body."
        assert actions[0]["channel"] == "oasis"

    def test_authoritative_actor_id_overrides_llm_actor_id(self, monkeypatch):
        actor = SimpleNamespace(id="actor-real", actor_type="cohort", name="Actor", archetype="test")
        response = SimpleNamespace(parsed={
            "social_actions": [{"action_type": "post", "body": "hello", "actor_id": "actor-fake"}],
            "emotion_self_ratings": [{"emotion": "anger", "value": 4, "actor_id": "actor-fake"}],
        })
        call = SimpleNamespace(id="call-1")

        monkeypatch.setattr(agent_engine, "complete_with_audit", lambda *a, **kw: (response, call))
        result = run_actor_decision(
            SimpleNamespace(),
            big_bang=SimpleNamespace(id="bb"),
            multiverse=SimpleNamespace(id="mv"),
            actor=actor,
            tick_index=1,
            prompt_context={},
        )

        assert result["parsed_actions"][0]["actor_id"] == "actor-real"
        assert result["emotion_self_ratings"][0]["actor_id"] == "actor-real"
