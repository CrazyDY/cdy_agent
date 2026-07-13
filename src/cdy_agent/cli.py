"""Command line interface for CDY Agent."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from .agent import Agent, AgentConfig

app = typer.Typer(help="Run the CDY personal AI agent.")


@app.command()
def run(
    task: Annotated[str, typer.Argument(help="The task to ask the agent to handle.")],
    model: Annotated[str, typer.Option(help="OpenAI model name.")] = "gpt-5.6",
    skills_dir: Annotated[Path, typer.Option(help="Directory containing skill folders.")] = Path("skills"),
) -> None:
    """Run one agent task."""
    agent = Agent(config=AgentConfig(model=model), skill_roots=[skills_dir])
    typer.echo(agent.run(task))


if __name__ == "__main__":
    app()
