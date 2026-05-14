from __future__ import annotations

from app.llm.prompt_templates import (
    ACTOR_SYSTEM_PROMPT,
    ENDPOINT_CALIBRATION_GUIDANCE,
    GOD_AGENT_SYSTEM_PROMPT,
    INITIALIZER_SYSTEM_PROMPT,
    REPORT_AGENT_SYSTEM_PROMPT,
)


def test_endpoint_calibration_guidance_names_outcome_priors() -> None:
    for phrase in (
        "decision authority",
        "switching costs",
        "economic elasticity",
        "platform ownership leverage",
        "coalition durability",
        "terminal endpoint options",
    ):
        assert phrase in ENDPOINT_CALIBRATION_GUIDANCE


def test_initializer_prompt_preserves_endpoint_options_at_t0() -> None:
    assert "Initializer endpoint requirements" in INITIALIZER_SYSTEM_PROMPT
    assert "branch_hypotheses" in INITIALIZER_SYSTEM_PROMPT
    assert "dependency/influence graph edges" in INITIALIZER_SYSTEM_PROMPT
    assert "capitulation, substitution, exit, regulation" in INITIALIZER_SYSTEM_PROMPT


def test_god_prompt_does_not_treat_process_moves_as_terminal_endpoints() -> None:
    assert "Calibrate endpoint pressure explicitly" in GOD_AGENT_SYSTEM_PROMPT
    assert "Do not stop at process moves" in GOD_AGENT_SYSTEM_PROMPT
    assert "Audits, committees, pauses, negotiations, pilots" in GOD_AGENT_SYSTEM_PROMPT
    assert "authority, exit, substitution, durability, or economic endpoint" in GOD_AGENT_SYSTEM_PROMPT


def test_god_prompt_requires_branch_probability() -> None:
    assert "branch_probability" in GOD_AGENT_SYSTEM_PROMPT
    assert "P(child branch occurs | this parent timeline at the fork tick)" in GOD_AGENT_SYSTEM_PROMPT
    assert "This is not your confidence score" in GOD_AGENT_SYSTEM_PROMPT


def test_god_prompt_requires_endpoint_specific_branch_reasons() -> None:
    assert "A create_branch reason must name the alternate path" in GOD_AGENT_SYSTEM_PROMPT
    assert "explicit yes/no endpoint direction" in GOD_AGENT_SYSTEM_PROMPT


def test_god_prompt_forces_deadline_binary_settlement_for_forecast_cards() -> None:
    assert "force a binary yes/no settlement" in GOD_AGENT_SYSTEM_PROMPT
    assert "realize no" in GOD_AGENT_SYSTEM_PROMPT
    assert "not primary yes/no candidates in deadline-aware cards" in GOD_AGENT_SYSTEM_PROMPT
    assert "mark both binary candidates insufficient_ticks" not in GOD_AGENT_SYSTEM_PROMPT


def test_actor_prompt_honors_child_branch_context_without_forcing_settlement() -> None:
    assert "current_state.branch_context" in ACTOR_SYSTEM_PROMPT
    assert "local premise for this child timeline" in ACTOR_SYSTEM_PROMPT
    assert "do not force terminal endpoint settlement" in ACTOR_SYSTEM_PROMPT


def test_report_agent_prompt_distinguishes_terminal_endpoints_from_process_states() -> None:
    assert "Report endpoint requirements" in REPORT_AGENT_SYSTEM_PROMPT
    assert "terminal endpoint or only a process state" in REPORT_AGENT_SYSTEM_PROMPT
    assert "pressure mechanics but not a resolved endpoint" in REPORT_AGENT_SYSTEM_PROMPT
    assert "unresolved endpoint choices" in REPORT_AGENT_SYSTEM_PROMPT
