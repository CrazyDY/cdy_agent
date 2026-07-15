"""Command-line interface for CDY Agent."""

from __future__ import annotations

import typer


app = typer.Typer(help="Run the CDY local personal AI assistant.")


@app.callback()
def main() -> None:
    """Run the CDY local personal AI assistant."""
