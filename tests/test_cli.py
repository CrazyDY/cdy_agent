import builtins
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest
import typer
from openai import (
    APIConnectionError,
    APIError,
    AuthenticationError,
    OpenAIError,
    RateLimitError,
)
from typer.testing import CliRunner

from cdy_agent import cli, openai_client
from cdy_agent.agent import AgentLoopLimitError
from cdy_agent.cli import app
from cdy_agent.conversation import Message
from cdy_agent.memory import ConversationNotFoundError, StoredConversation
from cdy_agent.tools.base import ConfirmationRequest, ToolResult


runner = CliRunner()
confirm_test_app = typer.Typer()
confirmation_request = ConfirmationRequest(
    "run_shell", {"argv": ["pwd"]}, "Run ['pwd']"
)


@confirm_test_app.callback(invoke_without_command=True)
def confirm_test() -> None:
    typer.echo("APPROVED" if cli._confirm_tool(confirmation_request) else "DENIED")


class FakeAgent:
    def __init__(
        self,
        replies: str | Sequence[str] = "Model reply",
        error: Exception | None = None,
    ) -> None:
        self.replies = iter([replies] if isinstance(replies, str) else replies)
        self.error = error
        self.calls: list[tuple[Message, ...]] = []

    def run(self, messages: Sequence[Message]) -> str:
        self.calls.append(tuple(messages))
        if self.error is not None:
            raise self.error
        return next(self.replies)


class FakeConversationStore:
    def __init__(self, stored: StoredConversation | None = None) -> None:
        self.stored = stored
        self.loads: list[str] = []
        self.appended: list[tuple[str, Message, Message]] = []

    def load(self, session_id: str) -> StoredConversation:
        self.loads.append(session_id)
        assert self.stored is not None
        return self.stored

    def append_turn(
        self, session_id: str, user: Message, assistant: Message
    ) -> None:
        self.appended.append((session_id, user, assistant))


@pytest.fixture(autouse=True)
def default_model_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDY_AGENT_MODEL", "env-model")
    monkeypatch.delenv("CDY_AGENT_API_MODE", raising=False)


def test_cli_help_describes_local_personal_assistant() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "CDY local personal AI assistant" in result.stdout


def test_ask_passes_normalized_user_message_to_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent = FakeAgent()
    calls: list[tuple[str, str, Path]] = []

    def fake_create_agent(model: str, api_mode: str, workspace: Path) -> FakeAgent:
        calls.append((model, api_mode, workspace))
        return agent

    monkeypatch.setattr(cli, "_create_agent", fake_create_agent)

    result = runner.invoke(app, ["ask", "  Hello  ", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert result.stdout == "Model reply\n"
    assert calls == [("env-model", "responses", tmp_path.resolve())]
    assert agent.calls == [(Message(role="user", content="Hello"),)]


def test_ask_model_and_api_mode_are_resolved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[tuple[str, str, Path]] = []
    monkeypatch.setenv("CDY_AGENT_API_MODE", "chat_completions")
    monkeypatch.setattr(
        cli,
        "_create_agent",
        lambda model, api_mode, workspace: seen.append(
            (model, api_mode, workspace)
        ) or FakeAgent(),
    )

    result = runner.invoke(
        app, ["ask", "Hello", "--model", "  cli-model  ", "--workspace", str(tmp_path)]
    )

    assert result.exit_code == 0
    assert seen == [("cli-model", "chat_completions", tmp_path.resolve())]


def test_ask_defaults_workspace_to_invocation_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    seen: list[Path] = []
    monkeypatch.setattr(
        cli,
        "_create_agent",
        lambda model, api_mode, workspace: seen.append(workspace) or FakeAgent("ok"),
    )

    result = runner.invoke(app, ["ask", "hello"])

    assert result.exit_code == 0
    assert seen == [tmp_path.resolve()]


def test_ask_rejects_invalid_workspace_before_creating_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(cli, "_create_agent", lambda *args: calls.append(True))

    result = runner.invoke(
        app, ["ask", "hello", "--workspace", str(tmp_path / "missing")]
    )

    assert result.exit_code == 1
    assert "workspace" in result.stderr.lower()
    assert calls == []


def test_ask_rejects_blank_prompt_before_creating_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(cli, "_create_agent", lambda *args: calls.append(True))

    result = runner.invoke(
        app, ["ask", "   ", "--workspace", str(tmp_path)]
    )

    assert result.exit_code == 1
    assert "Prompt must not be empty" in result.stderr
    assert calls == []


def test_chat_passes_accumulated_history_and_appends_final_replies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent = FakeAgent(["First reply", "Second reply"])
    monkeypatch.setattr(cli, "_create_agent", lambda *args: agent)

    result = runner.invoke(
        app,
        ["chat", "--workspace", str(tmp_path)],
        input=" First question \nFollow-up\n/exit\n",
    )

    assert result.exit_code == 0
    assert "Assistant: First reply" in result.stdout
    assert "Assistant: Second reply" in result.stdout
    assert agent.calls == [
        (Message(role="user", content="First question"),),
        (
            Message(role="user", content="First question"),
            Message(role="assistant", content="First reply"),
            Message(role="user", content="Follow-up"),
        ),
    ]


def test_chat_persists_each_complete_turn_before_display(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent = FakeAgent(["First reply", "Second reply"])
    store = FakeConversationStore()
    monkeypatch.setattr(cli, "_create_agent", lambda *args: agent)
    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: store)

    result = runner.invoke(
        app, ["chat", "--workspace", str(tmp_path)], input="First\nSecond\n/exit\n"
    )

    assert result.exit_code == 0
    assert len(store.appended) == 2
    session_id = store.appended[0][0]
    assert store.appended == [
        (session_id, Message("user", "First"), Message("assistant", "First reply")),
        (session_id, Message("user", "Second"), Message("assistant", "Second reply")),
    ]
    assert store.loads == []
    assert result.stdout.index("Assistant: First reply") >= 0


def test_chat_resume_loads_history_before_first_agent_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    session_id = "52c809c6-6e55-4ff1-9220-e4f90a4f6774"
    stored = StoredConversation(
        session_id,
        "2026-07-18T08:30:00.000000Z",
        "2026-07-18T08:30:00.000000Z",
        (Message("user", "Old"), Message("assistant", "History")),
    )
    store = FakeConversationStore(stored)
    agent = FakeAgent("New reply")
    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: store)
    monkeypatch.setattr(cli, "_create_agent", lambda *args: agent)

    result = runner.invoke(
        app,
        ["chat", "--resume", session_id, "--workspace", str(tmp_path)],
        input="Continue\n/exit\n",
    )

    assert result.exit_code == 0
    assert store.loads == [session_id]
    assert agent.calls == [
        (
            Message("user", "Old"),
            Message("assistant", "History"),
            Message("user", "Continue"),
        )
    ]
    assert store.appended == [
        (session_id, Message("user", "Continue"), Message("assistant", "New reply"))
    ]


def test_chat_model_failure_and_immediate_exit_do_not_save(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = FakeConversationStore()
    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: store)
    monkeypatch.setattr(
        cli,
        "_create_agent",
        lambda *args: FakeAgent(error=AgentLoopLimitError("model loop exhausted")),
    )

    failed = runner.invoke(app, ["chat", "--workspace", str(tmp_path)], input="Hello\n")
    exited = runner.invoke(app, ["chat", "--workspace", str(tmp_path)], input="/exit\n")

    assert failed.exit_code == 1
    assert exited.exit_code == 0
    assert store.appended == []


def test_chat_store_failure_does_not_display_reply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FailingStore(FakeConversationStore):
        def append_turn(
            self, session_id: str, user: Message, assistant: Message
        ) -> None:
            raise cli.ConversationStoreError("Could not write conversation data.")

    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: FailingStore())
    monkeypatch.setattr(cli, "_create_agent", lambda *args: FakeAgent("Unsaved reply"))

    result = runner.invoke(
        app, ["chat", "--workspace", str(tmp_path)], input="Hello\n"
    )

    assert result.exit_code == 1
    assert "Could not write conversation data" in result.stderr
    assert "Assistant: Unsaved reply" not in result.stdout


def test_chat_later_model_failure_keeps_only_prior_complete_turn(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FailSecondAgent(FakeAgent):
        def run(self, messages: Sequence[Message]) -> str:
            self.calls.append(tuple(messages))
            if len(self.calls) == 2:
                raise AgentLoopLimitError("second turn failed")
            return "First reply"

    store = FakeConversationStore()
    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: store)
    monkeypatch.setattr(cli, "_create_agent", lambda *args: FailSecondAgent())

    result = runner.invoke(
        app, ["chat", "--workspace", str(tmp_path)], input="First\nSecond\n"
    )

    assert result.exit_code == 1
    assert len(store.appended) == 1
    assert store.appended[0][1:] == (
        Message("user", "First"),
        Message("assistant", "First reply"),
    )


def test_resume_failure_happens_before_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class MissingStore(FakeConversationStore):
        def load(self, session_id: str) -> StoredConversation:
            raise ConversationNotFoundError("Conversation not found.")

    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: MissingStore())
    monkeypatch.setattr(cli, "_create_agent", lambda *args: FakeAgent())

    result = runner.invoke(
        app,
        [
            "chat",
            "--resume",
            "52c809c6-6e55-4ff1-9220-e4f90a4f6774",
            "--workspace",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert "Conversation not found" in result.stderr
    assert "You:" not in result.stdout


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
def test_resumed_history_is_api_mode_neutral(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, api_mode: str
) -> None:
    session_id = "52c809c6-6e55-4ff1-9220-e4f90a4f6774"
    stored = StoredConversation(
        session_id,
        "2026-07-18T08:30:00.000000Z",
        "2026-07-18T08:30:00.000000Z",
        (Message("user", "Old"), Message("assistant", "History")),
    )
    store = FakeConversationStore(stored)
    seen_modes: list[str] = []
    agent = FakeAgent("Reply")
    monkeypatch.setenv("CDY_AGENT_API_MODE", api_mode)
    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: store)
    monkeypatch.setattr(
        cli,
        "_create_agent",
        lambda model, mode, workspace: seen_modes.append(mode) or agent,
    )

    result = runner.invoke(
        app,
        ["chat", "--resume", session_id, "--workspace", str(tmp_path)],
        input="New\n/exit\n",
    )

    assert result.exit_code == 0
    assert seen_modes == [api_mode]
    assert agent.calls[0][:2] == stored.messages


def test_ask_does_not_construct_conversation_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_create_agent", lambda *args: FakeAgent("Reply"))
    monkeypatch.setattr(
        cli,
        "ConversationStore",
        lambda workspace: pytest.fail("ask must remain stateless"),
    )

    result = runner.invoke(app, ["ask", "Hello", "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert result.stdout == "Reply\n"


def test_chat_does_not_append_assistant_message_after_failed_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent = FakeAgent(error=AgentLoopLimitError("model loop exhausted"))
    monkeypatch.setattr(cli, "_create_agent", lambda *args: agent)

    result = runner.invoke(
        app, ["chat", "--workspace", str(tmp_path)], input="Hello\n"
    )

    assert result.exit_code == 1
    assert "model loop exhausted" in result.stderr
    assert "Assistant:" not in result.stdout
    assert agent.calls == [(Message(role="user", content="Hello"),)]


@pytest.mark.parametrize("command", ["/exit", "  /QUIT  "])
def test_chat_exit_commands_do_not_call_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, command: str
) -> None:
    agent = FakeAgent()
    monkeypatch.setattr(cli, "_create_agent", lambda *args: agent)

    result = runner.invoke(
        app, ["chat", "--workspace", str(tmp_path)], input=f"{command}\n"
    )

    assert result.exit_code == 0
    assert agent.calls == []


def test_chat_ignores_blank_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent = FakeAgent("Reply")
    monkeypatch.setattr(cli, "_create_agent", lambda *args: agent)

    result = runner.invoke(
        app, ["chat", "--workspace", str(tmp_path)], input="   \nHello\n/quit\n"
    )

    assert result.exit_code == 0
    assert agent.calls == [(Message(role="user", content="Hello"),)]


def test_chat_defaults_workspace_to_invocation_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    seen: list[Path] = []
    monkeypatch.setattr(
        cli,
        "_create_agent",
        lambda model, api_mode, workspace: seen.append(workspace) or FakeAgent(),
    )

    result = runner.invoke(app, ["chat"], input="/exit\n")

    assert result.exit_code == 0
    assert seen == [tmp_path.resolve()]


def test_chat_rejects_invalid_workspace_before_prompting(tmp_path: Path) -> None:
    result = runner.invoke(
        app, ["chat", "--workspace", str(tmp_path / "missing")], input=""
    )

    assert result.exit_code == 1
    assert "workspace" in result.stderr.lower()
    assert "You:" not in result.stdout


def test_chat_eof_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_create_agent", lambda *args: FakeAgent())
    result = runner.invoke(app, ["chat", "--workspace", str(tmp_path)], input="")
    assert result.exit_code == 0


def test_chat_keyboard_interrupt_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_create_agent", lambda *args: FakeAgent())

    def raise_keyboard_interrupt(*args: object, **kwargs: object) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "input", raise_keyboard_interrupt)
    result = runner.invoke(app, ["chat", "--workspace", str(tmp_path)])
    assert result.exit_code == 0


def test_create_agent_wires_gateway_registry_and_confirmation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gateway = object()
    manager = object()
    skill_tools = object()
    manager_calls: list[tuple[Path, object, object]] = []
    registered: list[object] = []

    class FakeRegistry:
        def register_many(self, tools: object) -> ToolResult:
            registered.append(tools)
            return ToolResult.success({})

    registry = FakeRegistry()
    seen: list[tuple[object, object, object]] = []
    monkeypatch.setattr(cli, "ModelGateway", lambda **kwargs: gateway)
    monkeypatch.setattr(cli, "create_builtin_registry", lambda workspace: registry)
    monkeypatch.setattr(
        cli,
        "SkillManager",
        lambda workspace, built_registry, confirm: manager_calls.append(
            (workspace, built_registry, confirm)
        ) or manager,
    )
    monkeypatch.setattr(cli, "create_skill_tools", lambda built_manager: skill_tools)
    monkeypatch.setattr(
        cli,
        "Agent",
        lambda built_gateway, built_registry, confirm: seen.append(
            (built_gateway, built_registry, confirm)
        ) or "agent",
    )

    result = cli._create_agent("model", "responses", tmp_path)

    assert result == "agent"
    assert manager_calls == [(tmp_path, registry, cli._confirm_tool)]
    assert registered == [skill_tools]
    assert seen == [(gateway, registry, cli._confirm_tool)]


@pytest.mark.parametrize(
    ("answer", "expected"),
    [("y\n", "APPROVED\n"), ("YES\n", "APPROVED\n"), ("\n", "DENIED\n"),
     ("n\n", "DENIED\n"), ("invalid\n", "DENIED\n")],
)
def test_confirmation_answers(answer: str, expected: str) -> None:
    result = runner.invoke(confirm_test_app, [], input=answer)

    assert result.exit_code == 0
    assert "Run ['pwd']" in result.stdout
    assert result.stdout.count("Run ['pwd']") == 1
    assert result.stdout.endswith(expected)


def test_confirmation_eof_denies() -> None:
    result = runner.invoke(confirm_test_app, [], input="")
    assert result.exit_code == 0
    assert result.stdout.endswith("DENIED\n")


def test_personal_tool_confirmation_description_is_shown_once() -> None:
    request = ConfirmationRequest(
        "create_todo",
        {"text": "Write tests"},
        "Create Todo: Write tests.",
    )
    monkey_app = typer.Typer()

    @monkey_app.callback(invoke_without_command=True)
    def invoke() -> None:
        typer.echo("APPROVED" if cli._confirm_tool(request) else "DENIED")

    result = runner.invoke(monkey_app, [], input="y\n")
    assert result.exit_code == 0
    assert result.stdout.count("Create Todo: Write tests.") == 1
    assert result.stdout.endswith("APPROVED\n")


@pytest.mark.parametrize("error", [KeyboardInterrupt(), typer.Abort()])
def test_confirmation_interrupt_denies(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    def raise_error(*args: object, **kwargs: object) -> bool:
        raise error

    monkeypatch.setattr(builtins, "input", raise_error)
    result = runner.invoke(confirm_test_app)
    assert result.exit_code == 0
    assert result.stdout.endswith("DENIED\n")


def test_ask_reports_invalid_api_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDY_AGENT_API_MODE", "legacy")
    result = runner.invoke(app, ["ask", "Hello"])
    assert result.exit_code == 1
    assert "CDY_AGENT_API_MODE" in result.stderr
    assert "responses" in result.stderr
    assert "chat_completions" in result.stderr


REQUEST = httpx.Request("POST", "https://api.openai.com/v1/responses")


@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        (
            AuthenticationError(
                "invalid key",
                response=httpx.Response(401, request=REQUEST),
                body=None,
            ),
            "Check OPENAI_API_KEY",
        ),
        (
            OpenAIError("Missing credentials"),
            "OpenAI client error: Missing credentials",
        ),
        (
            APIConnectionError(request=REQUEST),
            "Check OPENAI_BASE_URL and your network connection",
        ),
        (
            RateLimitError(
                "rate limited",
                response=httpx.Response(429, request=REQUEST),
                body=None,
            ),
            "rate limit",
        ),
        (
            APIError("server error", REQUEST, body=None),
            "OpenAI request failed: server error",
        ),
        (ValueError("Prompt must not be empty."), "Prompt must not be empty"),
        (
            RuntimeError("OpenAI returned an empty response."),
            "OpenAI returned an empty response",
        ),
        (AgentLoopLimitError("Agent loop exhausted."), "Agent loop exhausted"),
    ],
)
def test_ask_reports_expected_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    error: Exception,
    expected_message: str,
) -> None:
    monkeypatch.setattr(cli, "_create_agent", lambda *args: FakeAgent(error=error))
    result = runner.invoke(app, ["ask", "Hello", "--workspace", str(tmp_path)])
    assert result.exit_code == 1
    assert expected_message in result.stderr
    assert "Traceback" not in result.stderr


def test_ask_reports_missing_api_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        cli,
        "_create_agent",
        lambda *args: FakeAgent(
            error=openai_client.MissingAPIKeyError("OPENAI_API_KEY is required.")
        ),
    )
    result = runner.invoke(app, ["ask", "Hello", "--workspace", str(tmp_path)])
    assert result.exit_code == 1
    assert "Check OPENAI_API_KEY" in result.stderr


def test_create_agent_registers_skill_management_tools(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "ModelGateway", lambda **kwargs: object())

    agent = cli._create_agent("model", "responses", tmp_path)

    names = [definition["name"] for definition in agent._registry.definitions]
    assert names[-2:] == ["list_skills", "activate_skill"]


def test_ask_reports_management_tool_registration_failure_without_model_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class RejectingRegistry:
        def register_many(self, tools: object) -> ToolResult:
            return ToolResult.failure(
                "invalid_tools", "Could not register Skill management tools."
            )

    monkeypatch.setattr(cli, "ModelGateway", lambda **kwargs: object())
    monkeypatch.setattr(
        cli, "create_builtin_registry", lambda workspace: RejectingRegistry()
    )

    def fail_if_agent_is_created(*args: object, **kwargs: object) -> object:
        raise AssertionError("model execution boundary must not be constructed")

    monkeypatch.setattr(cli, "Agent", fail_if_agent_is_created)

    result = runner.invoke(app, ["ask", "Hello", "--workspace", str(tmp_path)])

    assert result.exit_code == 1
    assert result.stderr == "Error: Could not register Skill management tools.\n"
    assert "Traceback" not in result.stderr


def test_skill_code_confirmation_warns_about_current_user_permissions() -> None:
    request = ConfirmationRequest(
        "activate_skill",
        {"name": "research"},
        "Run Skill 'research' Python code from /workspace/tools.py with current user permissions.",
    )
    monkey_app = typer.Typer()

    @monkey_app.callback(invoke_without_command=True)
    def invoke() -> None:
        typer.echo("APPROVED" if cli._confirm_tool(request) else "DENIED")

    result = runner.invoke(monkey_app, [], input="\n")
    assert "current user permissions" in result.stdout
    assert result.stdout.endswith("DENIED\n")
