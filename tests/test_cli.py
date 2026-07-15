from typer.testing import CliRunner

from cdy_agent.cli import app


runner = CliRunner()


def test_cli_help_describes_local_personal_assistant() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "CDY local personal AI assistant" in result.stdout
