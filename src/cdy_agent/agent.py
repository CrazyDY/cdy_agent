"""OpenAI Responses API powered agent with local skill tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .skills import SkillRegistry

if TYPE_CHECKING:
    from openai import OpenAI


@dataclass(frozen=True)
class AgentConfig:
    """Runtime configuration for the personal agent."""

    model: str = "gpt-5.6"
    max_tool_rounds: int = 6
    instructions: str = (
        "You are CDY Agent, a practical personal assistant. "
        "Plan briefly, use local skills when they are relevant, and return concise actionable results."
    )


class Agent:
    """A small agent loop around the OpenAI Python SDK Responses API."""

    def __init__(
        self,
        config: AgentConfig | None = None,
        skill_roots: list[Path] | None = None,
        client: OpenAI | None = None,
    ) -> None:
        self.config = config or AgentConfig()
        self.skills = SkillRegistry(skill_roots)
        if client is not None:
            self.client = client
        else:
            from openai import OpenAI

            self.client = OpenAI()

    def run(self, user_input: str) -> str:
        """Run the agent until it produces a final answer."""
        tools = self.skills.tool_schemas()
        system_prompt = f"{self.config.instructions}\n\n{self.skills.skill_prompt()}"
        response = self.client.responses.create(
            model=self.config.model,
            instructions=system_prompt,
            input=user_input,
            tools=tools,
        )

        for _ in range(self.config.max_tool_rounds):
            tool_outputs = self._collect_tool_outputs(response)
            if not tool_outputs:
                return self._response_text(response)
            response = self.client.responses.create(
                model=self.config.model,
                instructions=system_prompt,
                input=tool_outputs,
                previous_response_id=response.id,
                tools=tools,
            )
        return self._response_text(response)

    def _collect_tool_outputs(self, response: Any) -> list[dict[str, Any]]:
        outputs: list[dict[str, Any]] = []
        for item in getattr(response, "output", []) or []:
            if getattr(item, "type", None) != "function_call":
                continue
            output = self.skills.execute_tool(item.name, item.arguments)
            outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": item.call_id,
                    "output": output,
                }
            )
        return outputs

    @staticmethod
    def _response_text(response: Any) -> str:
        text = getattr(response, "output_text", None)
        if text:
            return text
        parts: list[str] = []
        for item in getattr(response, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                if getattr(content, "type", None) in {"output_text", "text"}:
                    parts.append(getattr(content, "text", ""))
        return "\n".join(part for part in parts if part).strip()
