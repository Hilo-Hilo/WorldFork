from __future__ import annotations

from click.testing import CliRunner
from worldfork_cli.main import main


def test_help_lists_agent_commands() -> None:
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "agent" in result.output
    assert "runs" in result.output
    assert "jobs" in result.output


def test_global_verbosity_parses_before_command() -> None:
    result = CliRunner().invoke(main, ["--verbosity", "normal", "agent", "--help"])

    assert result.exit_code == 0
    assert "discover" in result.output
