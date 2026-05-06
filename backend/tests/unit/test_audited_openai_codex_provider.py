from __future__ import annotations

from backend.app.llm.openai_codex_provider import _input_from_messages


def test_input_from_messages_wraps_assistant_history_as_user_context() -> None:
    payload = _input_from_messages(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "review this"},
            {"role": "assistant", "content": '{"decision":"branch"}'},
            {"role": "user", "content": "repair the tool call"},
        ]
    )

    assert [item["role"] for item in payload] == ["user", "user", "user"]
    assert payload[0]["content"][0] == {"type": "input_text", "text": "review this"}
    assert payload[1]["content"][0]["type"] == "input_text"
    assert payload[1]["content"][0]["text"].startswith("Previous assistant response:")
    assert '{"decision":"branch"}' in payload[1]["content"][0]["text"]
    assert payload[2]["content"][0] == {"type": "input_text", "text": "repair the tool call"}
