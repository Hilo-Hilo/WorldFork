from __future__ import annotations

from app.llm.prompt_templates import (
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


def test_report_agent_prompt_distinguishes_terminal_endpoints_from_process_states() -> None:
    assert "Report endpoint requirements" in REPORT_AGENT_SYSTEM_PROMPT
    assert "terminal endpoint or only a process state" in REPORT_AGENT_SYSTEM_PROMPT
    assert "pressure mechanics but not a resolved endpoint" in REPORT_AGENT_SYSTEM_PROMPT
    assert "unresolved endpoint choices" in REPORT_AGENT_SYSTEM_PROMPT
