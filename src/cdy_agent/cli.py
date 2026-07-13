"""Command-line entrypoint for the CDY Agent MVP."""

from __future__ import annotations

import argparse
import asyncio

from cdy_agent.openai_sdk import Runner

from cdy_agent.agent import create_agent


async def run_prompt(prompt: str) -> str:
    """Run a single prompt through the agent and return the final output."""

    result = await Runner.run(create_agent(), prompt)
    return result.final_output


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the CDY skill-based AI agent.")
    parser.add_argument("prompt", nargs="+", help="Natural-language task for the agent")
    args = parser.parse_args()

    prompt = " ".join(args.prompt)
    print(asyncio.run(run_prompt(prompt)))


if __name__ == "__main__":
    main()
