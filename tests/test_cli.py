import builtins
from collections.abc import Sequence
from datetime import datetime, timezone
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
from cdy_agent.memory import (
    ConversationNotFoundError,
    ConversationSummary,
    MemoryDraft,
    MemoryStore,
    MemoryStoreError,
    PreparedCreate,
    PreparedDelete,
    PreparedUpdate,
    StoredConversation,
    StoredMemory,
)
from cdy_agent.openai_client import FinalResponse
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


FIRST_ID = "52c809c6-6e55-4ff1-9220-e4f90a4f6774"
SECOND_ID = "f8605a17-cf86-46ce-87ad-7db57533e5dc"
MEMORY_RECORD = StoredMemory(
    FIRST_ID,
    "Use uv\nfor Python projects.",
    ("python", "tooling"),
    "2026-07-19T08:30:00.000000Z",
    "2026-07-19T09:45:00.000000Z",
)
SECOND_MEMORY_RECORD = StoredMemory(
    SECOND_ID,
    "Keep tests offline.",
    ("python", "testing"),
    "2026-07-18T08:30:00.000000Z",
    "2026-07-18T09:45:00.000000Z",
)


class FakeMemoryStore:
    def __init__(
        self,
        records: Sequence[StoredMemory] = (),
        error: MemoryStoreError | None = None,
    ) -> None:
        self.records = tuple(records)
        self.error = error
        self.created: list[tuple[str, tuple[str, ...]]] = []
        self.listed: list[tuple[str, ...]] = []
        self.searched: list[tuple[str | None, tuple[str, ...]]] = []
        self.updated: list[tuple[str, str, tuple[str, ...]]] = []
        self.deleted: list[str] = []
        self.prepared: list[tuple[str, tuple[str, ...]]] = []
        self.duplicates: list[tuple[MemoryDraft, str | None]] = []
        self.loaded: list[str] = []

    def _raise_error(self) -> None:
        if self.error is not None:
            raise self.error

    def prepare(self, content: str, tags: Sequence[str]) -> MemoryDraft:
        self._raise_error()
        normalized = (content.strip(), tuple(sorted({tag.strip().casefold() for tag in tags})))
        self.prepared.append(normalized)
        return MemoryDraft(normalized[0], normalized[1], "identity")

    def find_duplicate(
        self, draft: MemoryDraft, *, exclude_id: str | None = None
    ) -> StoredMemory | None:
        self._raise_error()
        self.duplicates.append((draft, exclude_id))
        return None

    def prepare_create(
        self, content: str, tags: Sequence[str]
    ) -> PreparedCreate:
        return PreparedCreate(FIRST_ID, self.prepare(content, tags))

    def commit_create(self, prepared: PreparedCreate) -> StoredMemory:
        self._raise_error()
        self.created.append((prepared.draft.content, prepared.draft.tags))
        return StoredMemory(
            prepared.memory_id,
            prepared.draft.content,
            prepared.draft.tags,
            MEMORY_RECORD.created_at,
            MEMORY_RECORD.updated_at,
        )

    def create(self, content: str, tags: Sequence[str]) -> StoredMemory:
        self._raise_error()
        self.created.append((content, tuple(tags)))
        return MEMORY_RECORD

    def get(self, memory_id: str) -> StoredMemory:
        self._raise_error()
        self.loaded.append(memory_id)
        if memory_id != FIRST_ID:
            raise MemoryStoreError("Memory ID must be a complete UUID.")
        return MEMORY_RECORD

    def list_memories(self, tags: Sequence[str] = ()) -> tuple[StoredMemory, ...]:
        self._raise_error()
        self.listed.append(tuple(tags))
        return self.records

    def search(
        self, query: str | None = None, tags: Sequence[str] = ()
    ) -> tuple[StoredMemory, ...]:
        self._raise_error()
        self.searched.append((query, tuple(tags)))
        return self.records

    def update(
        self, memory_id: str, content: str, tags: Sequence[str]
    ) -> StoredMemory:
        self._raise_error()
        self.updated.append((memory_id, content, tuple(tags)))
        return StoredMemory(
            memory_id,
            content,
            tuple(tags),
            MEMORY_RECORD.created_at,
            MEMORY_RECORD.updated_at,
        )

    def prepare_update(
        self, memory_id: str, content: str, tags: Sequence[str]
    ) -> PreparedUpdate:
        return PreparedUpdate(self.get(memory_id), self.prepare(content, tags))

    def commit_update(self, prepared: PreparedUpdate) -> StoredMemory:
        self._raise_error()
        self.updated.append(
            (
                prepared.before.id,
                prepared.replacement.content,
                prepared.replacement.tags,
            )
        )
        return StoredMemory(
            prepared.before.id,
            prepared.replacement.content,
            prepared.replacement.tags,
            prepared.before.created_at,
            prepared.before.updated_at,
        )

    def delete(self, memory_id: str) -> None:
        self._raise_error()
        self.deleted.append(memory_id)

    def prepare_delete(self, memory_id: str) -> PreparedDelete:
        return PreparedDelete(self.get(memory_id))

    def commit_delete(self, prepared: PreparedDelete) -> None:
        self._raise_error()
        self.deleted.append(prepared.before.id)


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


def test_sessions_list_shows_empty_store_without_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = FakeConversationStore()
    store.list_summaries = lambda: ()  # type: ignore[attr-defined]
    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: store)

    result = runner.invoke(
        app, ["sessions", "list", "--workspace", str(tmp_path)]
    )

    assert result.exit_code == 0
    assert result.stdout == "No saved conversations.\n"


def test_sessions_list_renders_ordered_summary_fields(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = FakeConversationStore()
    store.list_summaries = lambda: (  # type: ignore[attr-defined]
        ConversationSummary(
            "52c809c6-6e55-4ff1-9220-e4f90a4f6774",
            "2026-07-18T08:30:00.000000Z",
            4,
            "First question",
        ),
    )
    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: store)

    result = runner.invoke(
        app, ["sessions", "list", "--workspace", str(tmp_path)]
    )

    assert result.exit_code == 0
    assert "52c809c6-6e55-4ff1-9220-e4f90a4f6774" in result.stdout
    assert "2026-07-18T08:30:00.000000Z" in result.stdout
    assert "4 messages" in result.stdout
    assert "First question" in result.stdout


def test_sessions_delete_defaults_to_no(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    deleted: list[str] = []
    store = FakeConversationStore()
    store.delete = deleted.append  # type: ignore[attr-defined]
    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: store)
    session_id = "52c809c6-6e55-4ff1-9220-e4f90a4f6774"

    result = runner.invoke(
        app,
        ["sessions", "delete", session_id, "--workspace", str(tmp_path)],
        input="\n",
    )

    assert result.exit_code == 0
    assert session_id in result.stdout
    assert "Aborted" in result.stdout
    assert deleted == []


def test_sessions_delete_confirmed_calls_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    deleted: list[str] = []
    store = FakeConversationStore()
    store.delete = deleted.append  # type: ignore[attr-defined]
    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: store)
    session_id = "52c809c6-6e55-4ff1-9220-e4f90a4f6774"

    result = runner.invoke(
        app,
        ["sessions", "delete", session_id, "--workspace", str(tmp_path)],
        input="y\n",
    )

    assert result.exit_code == 0
    assert deleted == [session_id]
    assert result.stdout.endswith(f"Deleted conversation {session_id}.\n")


@pytest.mark.parametrize("error", [EOFError(), KeyboardInterrupt(), typer.Abort()])
def test_sessions_delete_interruption_aborts_without_deleting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: BaseException
) -> None:
    deleted: list[str] = []
    store = FakeConversationStore()
    store.delete = deleted.append  # type: ignore[attr-defined]
    monkeypatch.setattr(cli, "ConversationStore", lambda workspace: store)

    def interrupt_confirmation(*args: object, **kwargs: object) -> bool:
        raise error

    monkeypatch.setattr(cli.typer, "confirm", interrupt_confirmation)

    result = runner.invoke(
        app,
        [
            "sessions",
            "delete",
            "52c809c6-6e55-4ff1-9220-e4f90a4f6774",
            "--workspace",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert result.stdout == "Aborted.\n"
    assert deleted == []


def test_sessions_commands_report_errors_without_tracebacks(tmp_path: Path) -> None:
    missing_workspace = runner.invoke(
        app, ["sessions", "list", "--workspace", str(tmp_path / "missing")]
    )
    missing_session = runner.invoke(
        app,
        [
            "sessions",
            "delete",
            "52c809c6-6e55-4ff1-9220-e4f90a4f6774",
            "--workspace",
            str(tmp_path),
        ],
        input="y\n",
    )

    assert missing_workspace.exit_code == 1
    assert "workspace" in missing_workspace.stderr.lower()
    assert missing_session.exit_code == 1
    assert "Conversation not found" in missing_session.stderr
    assert "Traceback" not in missing_session.stderr


def _use_fake_memory_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, store: FakeMemoryStore
) -> list[Path]:
    workspaces: list[Path] = []

    def build(workspace: Path) -> FakeMemoryStore:
        workspaces.append(workspace)
        return store

    monkeypatch.setattr(cli, "MemoryStore", build)
    return workspaces


def test_memories_add_defaults_to_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeMemoryStore()
    workspaces = _use_fake_memory_store(monkeypatch, tmp_path, store)

    result = runner.invoke(
        app,
        ["memories", "add", "Use uv", "--tag", "Python", "--workspace", str(tmp_path)],
        input="\n",
    )

    assert result.exit_code == 0
    assert "Tags: python" in result.output
    assert "Use uv" in result.output
    assert "Aborted." in result.output
    assert store.created == []
    assert workspaces == [tmp_path.resolve()]


def test_memories_add_confirmed_creates_normalized_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeMemoryStore()
    workspaces = _use_fake_memory_store(monkeypatch, tmp_path, store)

    result = runner.invoke(
        app,
        ["memories", "add", "Use uv", "--tag", "Python", "--workspace", str(tmp_path)],
        input="y\n",
    )

    assert result.exit_code == 0
    assert store.created == [("Use uv", ("python",))]
    assert FIRST_ID in result.output
    assert workspaces == [tmp_path.resolve()]


def test_memories_add_displays_and_commits_preallocated_uuid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryStore(
        tmp_path,
        clock=lambda: datetime(2026, 7, 19, 8, 30, tzinfo=timezone.utc),
        id_factory=lambda: FIRST_ID,
    )
    monkeypatch.setattr(cli, "MemoryStore", lambda workspace: store)

    result = runner.invoke(
        app,
        ["memories", "add", "Use uv", "--tag", "Python", "--workspace", str(tmp_path)],
        input="y\n",
    )

    assert result.exit_code == 0
    assert result.output.index(FIRST_ID) < result.output.index("Create this memory?")
    assert store.get(FIRST_ID).id == FIRST_ID


def test_memories_list_forwards_tags_and_renders_every_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeMemoryStore((MEMORY_RECORD, SECOND_MEMORY_RECORD))
    workspaces = _use_fake_memory_store(monkeypatch, tmp_path, store)

    result = runner.invoke(
        app,
        [
            "memories", "list", "--tag", "Python", "--tag", "testing",
            "--workspace", str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert store.listed == [("Python", "testing")]
    assert result.output == (
        f"ID: {MEMORY_RECORD.id}\n"
        f"Updated: {MEMORY_RECORD.updated_at}\n"
        "Tags: python, tooling\nContent:\nUse uv\nfor Python projects.\n\n"
        f"ID: {SECOND_MEMORY_RECORD.id}\n"
        f"Updated: {SECOND_MEMORY_RECORD.updated_at}\n"
        "Tags: python, testing\nContent:\nKeep tests offline.\n"
    )
    assert workspaces == [tmp_path.resolve()]


def test_memories_list_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeMemoryStore()
    _use_fake_memory_store(monkeypatch, tmp_path, store)

    result = runner.invoke(
        app, ["memories", "list", "--workspace", str(tmp_path)]
    )

    assert result.exit_code == 0
    assert result.output == "No saved memories.\n"


def test_memories_search_renders_full_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeMemoryStore(records=(MEMORY_RECORD,))
    workspaces = _use_fake_memory_store(monkeypatch, tmp_path, store)

    result = runner.invoke(
        app,
        [
            "memories", "search", "uv", "--tag", "python",
            "--workspace", str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert store.searched == [("uv", ("python",))]
    assert MEMORY_RECORD.id in result.output
    assert MEMORY_RECORD.content in result.output
    assert "python" in result.output
    assert workspaces == [tmp_path.resolve()]


def test_memories_search_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeMemoryStore()
    _use_fake_memory_store(monkeypatch, tmp_path, store)

    result = runner.invoke(
        app, ["memories", "search", "missing", "--workspace", str(tmp_path)]
    )

    assert result.exit_code == 0
    assert result.output == "No matching memories.\n"


def test_memories_update_confirmed_replaces_complete_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeMemoryStore((MEMORY_RECORD,))
    workspaces = _use_fake_memory_store(monkeypatch, tmp_path, store)

    result = runner.invoke(
        app,
        [
            "memories", "update", FIRST_ID, "--content", "  Prefer uv sync  ",
            "--tag", "Python", "--tag", "TOOLING", "--workspace", str(tmp_path),
        ],
        input="y\n",
    )

    assert result.exit_code == 0
    assert "Current:" in result.output
    assert MEMORY_RECORD.content in result.output
    assert "Replacement:" in result.output
    assert "Prefer uv sync" in result.output
    assert "Tags: python, tooling" in result.output
    assert store.updated == [
        (FIRST_ID, "Prefer uv sync", ("python", "tooling"))
    ]
    assert result.output.endswith(f"Updated memory {FIRST_ID}.\n")
    assert workspaces == [tmp_path.resolve()]


def test_memories_delete_defaults_to_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeMemoryStore((MEMORY_RECORD,))
    _use_fake_memory_store(monkeypatch, tmp_path, store)

    result = runner.invoke(
        app,
        ["memories", "delete", FIRST_ID, "--workspace", str(tmp_path)],
        input="\n",
    )

    assert result.exit_code == 0
    assert MEMORY_RECORD.content in result.output
    assert "Aborted." in result.output
    assert store.deleted == []


def test_memories_delete_confirmed_calls_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeMemoryStore((MEMORY_RECORD,))
    workspaces = _use_fake_memory_store(monkeypatch, tmp_path, store)

    result = runner.invoke(
        app,
        ["memories", "delete", FIRST_ID, "--workspace", str(tmp_path)],
        input="y\n",
    )

    assert result.exit_code == 0
    assert store.deleted == [FIRST_ID]
    assert result.output.endswith(f"Deleted memory {FIRST_ID}.\n")
    assert workspaces == [tmp_path.resolve()]


@pytest.mark.parametrize("command", ["update", "delete"])
def test_memories_confirmed_write_reports_concurrent_change(
    command: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store_a = MemoryStore(
        tmp_path,
        clock=lambda: datetime(2026, 7, 19, 8, 30, tzinfo=timezone.utc),
        id_factory=lambda: FIRST_ID,
    )
    store_b = MemoryStore(
        tmp_path,
        clock=lambda: datetime(2026, 7, 19, 10, 30, tzinfo=timezone.utc),
    )
    store_a.create("Original", ["old"])
    monkeypatch.setattr(cli, "MemoryStore", lambda workspace: store_a)

    def mutate_then_approve(*args: object, **kwargs: object) -> bool:
        store_b.update(FIRST_ID, "Concurrent", ["newer"])
        return True

    monkeypatch.setattr(cli.typer, "confirm", mutate_then_approve)
    arguments = ["memories", command, FIRST_ID]
    if command == "update":
        arguments.extend(["--content", "Approved", "--tag", "new"])
    result = runner.invoke(app, [*arguments, "--workspace", str(tmp_path)])

    assert result.exit_code == 1
    assert "changed after confirmation" in result.stderr
    assert "Traceback" not in result.stderr
    assert store_a.get(FIRST_ID).content == "Concurrent"


@pytest.mark.parametrize("command", ["add", "update", "delete"])
@pytest.mark.parametrize("error", [EOFError(), KeyboardInterrupt()])
def test_memories_write_confirmation_interruption_aborts_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    error: BaseException,
) -> None:
    store = FakeMemoryStore((MEMORY_RECORD,))
    _use_fake_memory_store(monkeypatch, tmp_path, store)

    def interrupt_confirmation(*args: object, **kwargs: object) -> bool:
        raise error

    monkeypatch.setattr(cli.typer, "confirm", interrupt_confirmation)
    arguments = {
        "add": ["memories", "add", "Use uv"],
        "update": ["memories", "update", FIRST_ID, "--content", "Use uv sync"],
        "delete": ["memories", "delete", FIRST_ID],
    }[command]
    result = runner.invoke(app, [*arguments, "--workspace", str(tmp_path)])

    assert result.exit_code == 0
    assert result.output.endswith("Aborted.\n")
    assert store.created == []
    assert store.updated == []
    assert store.deleted == []


@pytest.mark.parametrize("command", ["update", "delete"])
def test_memories_reject_invalid_uuid_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    store = FakeMemoryStore((MEMORY_RECORD,))
    _use_fake_memory_store(monkeypatch, tmp_path, store)
    arguments = ["memories", command, "not-a-complete-uuid"]
    if command == "update":
        arguments.extend(["--content", "new content"])

    result = runner.invoke(app, [*arguments, "--workspace", str(tmp_path)])

    assert result.exit_code == 1
    assert "complete UUID" in result.stderr
    assert "Traceback" not in result.stderr
    assert store.updated == []
    assert store.deleted == []


def test_memories_report_safe_store_errors_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = FakeMemoryStore(error=MemoryStoreError("safe message"))
    _use_fake_memory_store(monkeypatch, tmp_path, store)

    result = runner.invoke(
        app, ["memories", "list", "--workspace", str(tmp_path)]
    )

    assert result.exit_code == 1
    assert "safe message" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "arguments",
    [
        ["memories", "--help"],
        ["memories", "add", "--help"],
        ["memories", "list", "--help"],
        ["memories", "search", "--help"],
        ["memories", "update", "--help"],
        ["memories", "delete", "--help"],
    ],
)
def test_memories_help(arguments: list[str]) -> None:
    result = runner.invoke(app, arguments)
    assert result.exit_code == 0


@pytest.mark.parametrize("api_mode", ["responses", "chat_completions"])
@pytest.mark.parametrize("command", ["ask", "chat"])
def test_ask_and_chat_offer_memory_tools_without_automatic_injection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    api_mode: str,
    command: str,
) -> None:
    memory = MemoryStore(tmp_path).create("private remembered preference", ["private"])

    class FakeGateway:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def create(self, **kwargs: object) -> FinalResponse:
            self.calls.append(kwargs)
            return FinalResponse("done")

    gateway = FakeGateway()
    searches: list[tuple[object, ...]] = []
    monkeypatch.setenv("CDY_AGENT_API_MODE", api_mode)
    monkeypatch.setattr(cli, "ModelGateway", lambda **kwargs: gateway)
    monkeypatch.setattr(
        MemoryStore,
        "search",
        lambda self, query=None, tags=(): searches.append((query, tuple(tags))) or (),
    )
    arguments = [command, "hello", "--workspace", str(tmp_path)]
    invocation_input = None
    if command == "chat":
        arguments = ["chat", "--workspace", str(tmp_path)]
        invocation_input = "hello\n/exit\n"

    result = runner.invoke(app, arguments, input=invocation_input)

    assert result.exit_code == 0
    assert {tool["name"] for tool in gateway.calls[0]["tools"]} >= {
        "remember_memory", "search_memories", "update_memory", "forget_memory"
    }
    assert all(
        memory.content not in message.content
        for message in gateway.calls[0]["messages"]
    )
    assert searches == []
