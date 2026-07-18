# 多轮会话实施计划

> **面向 Agent 执行者：** 必须按任务使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 子技能执行。步骤使用复选框（`- [ ]`）跟踪。

**目标：** 新增同时支持 Responses API 与 Chat Completions API 的进程内多轮 `chat` REPL，同时保持现有单轮 `ask` 行为。

**架构：** `conversation.py` 保存与 SDK 无关的标准消息历史；`openai_client.py` 将完整历史转换成两种 API 各自的请求格式；`cli.py` 只负责 REPL 输入输出、退出命令和用户错误提示。每轮发送完整上下文，退出进程后丢弃会话。

**技术栈：** Python 3.10+、Typer 0.12+、OpenAI Python SDK 1.99+、pytest 8+、uv、Hatchling

## 全局约束

- 应用代码放在 `src/cdy_agent/`，测试放在 `tests/`，采用四空格缩进、UTF-8 和公共函数类型提示。
- 同时支持 `responses` 和 `chat_completions`，两种模式具有一致的多轮语义。
- 保留 `cdy-agent ask` 的用户行为，新增 `cdy-agent chat [--model ...]`。
- 会话仅保存在当前进程，不加入持久化、上下文裁剪、系统提示、流式输出、重试、工具、Skills 或记忆。
- 测试不得依赖真实 API Key、网络或贡献者的文件系统；模拟 SDK 与终端边界。
- 不新增通用提供商抽象，不修改 `uv.lock`，不提交缓存、IDE 设置、密钥或模型响应。
- 后续设计、实施计划和项目说明文档均使用中文。

---

## 文件结构

- 新建 `src/cdy_agent/conversation.py`：定义不可变标准消息和内存会话容器。
- 新建 `tests/test_conversation.py`：覆盖会话顺序、校验和历史隔离。
- 修改 `src/cdy_agent/openai_client.py`：增加完整历史请求，并让单轮函数复用它。
- 修改 `tests/test_openai_client.py`：覆盖双 API 多轮载荷并更新单轮 Responses 载荷断言。
- 修改 `src/cdy_agent/cli.py`：新增 `chat` 命令，并集中复用错误呈现。
- 修改 `tests/test_cli.py`：覆盖 REPL 成功、退出、空输入、配置和错误行为。
- 修改 `README.md`：用中文说明当前阶段与 `ask`、`chat` 用法。

### Task 1：标准消息与内存会话

**文件：**
- 新建：`src/cdy_agent/conversation.py`
- 新建：`tests/test_conversation.py`

**接口：**
- 输入：角色 `Literal["user", "assistant"]` 和非空字符串内容。
- 输出：`Message(role: MessageRole, content: str)`；`Conversation.append(role, content) -> Message`；`Conversation.history -> tuple[Message, ...]`。

- [ ] **步骤 1：编写会话顺序与隔离的失败测试**

新建 `tests/test_conversation.py`：

```python
import pytest

from cdy_agent.conversation import Conversation, Message


def test_conversation_appends_normalized_messages_in_order() -> None:
    conversation = Conversation()

    user_message = conversation.append("user", "  Hello  ")
    assistant_message = conversation.append("assistant", " Hi there. ")

    assert user_message == Message(role="user", content="Hello")
    assert assistant_message == Message(
        role="assistant",
        content="Hi there.",
    )
    assert conversation.history == (user_message, assistant_message)


def test_history_is_an_immutable_snapshot() -> None:
    conversation = Conversation()
    conversation.append("user", "Hello")

    history = conversation.history
    conversation.append("assistant", "Hi")

    assert history == (Message(role="user", content="Hello"),)
    assert conversation.history != history
```

- [ ] **步骤 2：运行测试并确认因模块缺失而失败**

运行：

```powershell
uv run pytest tests/test_conversation.py -v
```

预期：测试收集失败，包含 `ModuleNotFoundError: No module named 'cdy_agent.conversation'`。

- [ ] **步骤 3：实现最小会话模型**

新建 `src/cdy_agent/conversation.py`：

```python
"""In-memory conversation state for CDY Agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


MessageRole = Literal["user", "assistant"]
SUPPORTED_MESSAGE_ROLES = ("user", "assistant")


@dataclass(frozen=True)
class Message:
    """One normalized conversation message."""

    role: MessageRole
    content: str


@dataclass
class Conversation:
    """Store one ordered conversation in memory."""

    _messages: list[Message] = field(default_factory=list, init=False)

    @property
    def history(self) -> tuple[Message, ...]:
        """Return an immutable snapshot of the ordered messages."""
        return tuple(self._messages)

    def append(self, role: MessageRole, content: str) -> Message:
        """Normalize and append one supported, non-empty message."""
        normalized_content = content.strip()
        message = Message(role=role, content=normalized_content)
        self._messages.append(message)
        return message
```

- [ ] **步骤 4：运行测试并确认顺序测试通过**

运行：`uv run pytest tests/test_conversation.py -v`

预期：`2 passed`。

- [ ] **步骤 5：补充角色与空内容校验的失败测试**

在 `tests/test_conversation.py` 末尾追加：

```python
@pytest.mark.parametrize("content", ["", "   "])
def test_conversation_rejects_blank_content(content: str) -> None:
    conversation = Conversation()

    with pytest.raises(ValueError, match="Message must not be empty"):
        conversation.append("user", content)

    assert conversation.history == ()


def test_conversation_rejects_unsupported_role() -> None:
    conversation = Conversation()

    with pytest.raises(ValueError, match="Unsupported message role"):
        conversation.append("system", "Instructions")  # type: ignore[arg-type]

    assert conversation.history == ()
```

- [ ] **步骤 6：运行校验测试并确认失败**

运行：

```powershell
uv run pytest tests/test_conversation.py::test_conversation_rejects_blank_content tests/test_conversation.py::test_conversation_rejects_unsupported_role -v
```

预期：3 个用例失败，因为 `append()` 尚未抛出 `ValueError`。

- [ ] **步骤 7：在追加前实现角色与内容校验**

将 `Conversation.append()` 替换为：

```python
    def append(self, role: MessageRole, content: str) -> Message:
        """Normalize and append one supported, non-empty message."""
        if role not in SUPPORTED_MESSAGE_ROLES:
            raise ValueError(f"Unsupported message role: {role!r}.")

        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("Message must not be empty.")

        message = Message(role=role, content=normalized_content)
        self._messages.append(message)
        return message
```

- [ ] **步骤 8：运行会话测试和完整回归测试**

运行：

```powershell
uv run pytest tests/test_conversation.py -v
uv run pytest
```

预期：会话文件报告 `5 passed`；完整测试全部通过。

- [ ] **步骤 9：提交会话模型**

```powershell
git add -- src/cdy_agent/conversation.py tests/test_conversation.py
git commit -m "Add in-memory conversation state"
```

### Task 2：双 API 多轮请求边界

**文件：**
- 修改：`src/cdy_agent/openai_client.py`
- 修改：`tests/test_openai_client.py`

**接口：**
- 输入：任务 1 的 `Sequence[Message]`、`model: str`、`api_mode: str` 和可选 `client: OpenAI | None`。
- 输出：`generate_reply_for_messages(messages, *, model, api_mode, client=None) -> str`；保留 `generate_reply(prompt, *, model, api_mode, client=None) -> str`。

- [ ] **步骤 1：更新单轮断言并编写 Responses 多轮失败测试**

在 `tests/test_openai_client.py` 的导入区加入：

```python
from cdy_agent.conversation import Message
```

将 `test_generate_reply_sends_normalized_prompt_and_model` 的调用断言改为：

```python
    assert client.responses.calls == [
        {
            "model": "gpt-5.6-terra",
            "input": [{"role": "user", "content": "Hello"}],
        }
    ]
```

并在文件末尾追加：

```python
def test_generate_reply_for_messages_sends_responses_history() -> None:
    client = FakeClient(responses_output="Second reply")
    messages = (
        Message(role="user", content="First question"),
        Message(role="assistant", content="First reply"),
        Message(role="user", content="Follow-up"),
    )

    result = openai_client.generate_reply_for_messages(
        messages,
        model="gpt-5.6-terra",
        api_mode="responses",
        client=client,
    )

    assert result == "Second reply"
    assert client.responses.calls == [
        {
            "model": "gpt-5.6-terra",
            "input": [
                {"role": "user", "content": "First question"},
                {"role": "assistant", "content": "First reply"},
                {"role": "user", "content": "Follow-up"},
            ],
        }
    ]
```

- [ ] **步骤 2：运行测试并确认新接口缺失**

运行：

```powershell
uv run pytest tests/test_openai_client.py::test_generate_reply_for_messages_sends_responses_history -v
```

预期：失败，包含 `AttributeError`，指出没有 `generate_reply_for_messages`。

- [ ] **步骤 3：实现共享多消息请求并让单轮函数委托给它**

在 `src/cdy_agent/openai_client.py` 中加入 `Sequence` 和 `Message` 导入，并将请求逻辑整理为：

```python
from collections.abc import Sequence

from .conversation import Message


def generate_reply(
    prompt: str,
    *,
    model: str,
    api_mode: str,
    client: OpenAI | None = None,
) -> str:
    """Generate one non-empty text reply for a user prompt."""
    normalized_prompt = prompt.strip()
    if not normalized_prompt:
        raise ValueError("Prompt must not be empty.")

    return generate_reply_for_messages(
        (Message(role="user", content=normalized_prompt),),
        model=model,
        api_mode=api_mode,
        client=client,
    )


def generate_reply_for_messages(
    messages: Sequence[Message],
    *,
    model: str,
    api_mode: str,
    client: OpenAI | None = None,
) -> str:
    """Generate one reply from a complete, ordered message history."""
    if not messages:
        raise ValueError("Conversation history must not be empty.")
    if api_mode not in {"responses", "chat_completions"}:
        raise ValueError(f"Unsupported API mode: {api_mode!r}.")

    if client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key or not api_key.strip():
            raise MissingAPIKeyError("OPENAI_API_KEY is required.")
        active_client = OpenAI()
    else:
        active_client = client

    request_messages = [
        {"role": message.role, "content": message.content}
        for message in messages
    ]
    if api_mode == "responses":
        response = active_client.responses.create(
            model=model,
            input=request_messages,
        )
        output_text = response.output_text
    else:
        response = active_client.chat.completions.create(
            model=model,
            messages=request_messages,
        )
        try:
            output_text = response.choices[0].message.content
        except (AttributeError, IndexError):
            output_text = None

    if not isinstance(output_text, str) or not output_text.strip():
        raise RuntimeError("OpenAI returned an empty response.")

    return output_text
```

删除原 `generate_reply()` 中已迁移到新函数的重复 SDK 调用逻辑。

- [ ] **步骤 4：运行 Responses 测试并确认通过**

运行：

```powershell
uv run pytest tests/test_openai_client.py::test_generate_reply_sends_normalized_prompt_and_model tests/test_openai_client.py::test_generate_reply_for_messages_sends_responses_history -v
```

预期：`2 passed`。

- [ ] **步骤 5：编写 Chat Completions 多轮与空历史失败测试**

在 `tests/test_openai_client.py` 末尾追加：

```python
def test_generate_reply_for_messages_sends_chat_history() -> None:
    client = FakeClient(chat_output="Second reply")
    messages = (
        Message(role="user", content="First question"),
        Message(role="assistant", content="First reply"),
        Message(role="user", content="Follow-up"),
    )

    result = openai_client.generate_reply_for_messages(
        messages,
        model="deepseek-v4-flash",
        api_mode="chat_completions",
        client=client,
    )

    assert result == "Second reply"
    assert client.chat.completions.calls == [
        {
            "model": "deepseek-v4-flash",
            "messages": [
                {"role": "user", "content": "First question"},
                {"role": "assistant", "content": "First reply"},
                {"role": "user", "content": "Follow-up"},
            ],
        }
    ]
    assert client.responses.calls == []


def test_generate_reply_for_messages_rejects_empty_history() -> None:
    client = FakeClient()

    with pytest.raises(ValueError, match="history must not be empty"):
        openai_client.generate_reply_for_messages(
            (),
            model="test-model",
            api_mode="responses",
            client=client,
        )

    assert client.responses.calls == []
    assert client.chat.completions.calls == []
```

- [ ] **步骤 6：运行双模式客户端测试和完整回归测试**

运行：

```powershell
uv run pytest tests/test_openai_client.py -v
uv run pytest
```

预期：客户端测试和完整测试全部通过；所有 SDK 调用均由假客户端记录，未访问网络。

- [ ] **步骤 7：提交双 API 多轮边界**

```powershell
git add -- src/cdy_agent/openai_client.py tests/test_openai_client.py
git commit -m "Support multi-turn API requests"
```

### Task 3：交互式 chat 命令

**文件：**
- 修改：`src/cdy_agent/cli.py`
- 修改：`tests/test_cli.py`

**接口：**
- 输入：任务 1 的 `Conversation`，任务 2 的 `generate_reply_for_messages()`，现有 `resolve_model()` 与 `resolve_api_mode()`。
- 输出：Typer 命令 `chat(model: str | None = None) -> None`；提示符 `You: `；回复前缀 `Assistant: `。

- [ ] **步骤 1：编写两轮 Responses REPL 失败测试**

在 `tests/test_cli.py` 的导入区加入：

```python
from collections.abc import Sequence

from cdy_agent.conversation import Message
```

并在文件末尾追加：

```python
def test_chat_sends_complete_history_across_two_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[Message, ...], str, str]] = []
    replies = iter(["First reply", "Second reply"])
    monkeypatch.setenv("CDY_AGENT_MODEL", "env-model")
    monkeypatch.delenv("CDY_AGENT_API_MODE", raising=False)

    def fake_generate_reply_for_messages(
        messages: Sequence[Message],
        *,
        model: str,
        api_mode: str,
    ) -> str:
        calls.append((tuple(messages), model, api_mode))
        return next(replies)

    monkeypatch.setattr(
        cli,
        "generate_reply_for_messages",
        fake_generate_reply_for_messages,
    )

    result = runner.invoke(
        app,
        ["chat"],
        input="First question\nFollow-up\n/exit\n",
    )

    assert result.exit_code == 0
    assert "Assistant: First reply" in result.stdout
    assert "Assistant: Second reply" in result.stdout
    assert calls == [
        (
            (Message(role="user", content="First question"),),
            "env-model",
            "responses",
        ),
        (
            (
                Message(role="user", content="First question"),
                Message(role="assistant", content="First reply"),
                Message(role="user", content="Follow-up"),
            ),
            "env-model",
            "responses",
        ),
    ]
```

- [ ] **步骤 2：运行测试并确认 chat 命令缺失**

运行：

```powershell
uv run pytest tests/test_cli.py::test_chat_sends_complete_history_across_two_turns -v
```

预期：失败，输出包含 `No such command 'chat'`。

- [ ] **步骤 3：新增 chat 命令的成功路径**

在 `src/cdy_agent/cli.py` 中加入：

```python
from .conversation import Conversation
from .openai_client import generate_reply_for_messages
```

并在 `ask` 命令后新增：

```python
@app.command()
def chat(
    model: Annotated[
        str | None,
        typer.Option(help="Model override for this conversation."),
    ] = None,
) -> None:
    """Start an in-memory multi-turn conversation."""
    active_model = resolve_model(model)
    api_mode = resolve_api_mode()
    conversation = Conversation()

    while True:
        try:
            prompt = input("You: ")
        except (EOFError, KeyboardInterrupt):
            return

        normalized_prompt = prompt.strip()
        if not normalized_prompt:
            continue
        if normalized_prompt.lower() in {"/exit", "/quit"}:
            return

        conversation.append("user", normalized_prompt)
        reply = generate_reply_for_messages(
            conversation.history,
            model=active_model,
            api_mode=api_mode,
        )
        conversation.append("assistant", reply)
        typer.echo(f"Assistant: {reply}")
```

- [ ] **步骤 4：运行两轮测试并确认通过**

运行：`uv run pytest tests/test_cli.py::test_chat_sends_complete_history_across_two_turns -v`

预期：`1 passed`。

- [ ] **步骤 5：编写退出、空输入、EOF 与模型覆盖测试**

在 `tests/test_cli.py` 末尾追加：

```python
@pytest.mark.parametrize("command", ["/exit", "  /QUIT  "])
def test_chat_exit_commands_do_not_call_model(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
) -> None:
    calls: list[bool] = []
    monkeypatch.delenv("CDY_AGENT_API_MODE", raising=False)
    monkeypatch.setattr(
        cli,
        "generate_reply_for_messages",
        lambda *args, **kwargs: calls.append(True),
    )

    result = runner.invoke(app, ["chat"], input=f"{command}\n")

    assert result.exit_code == 0
    assert calls == []


def test_chat_ignores_blank_input_and_honors_model_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setenv("CDY_AGENT_MODEL", "env-model")
    monkeypatch.setenv("CDY_AGENT_API_MODE", "chat_completions")

    def fake_generate_reply_for_messages(
        messages: Sequence[Message],
        *,
        model: str,
        api_mode: str,
    ) -> str:
        calls.append((model, api_mode))
        return "Reply"

    monkeypatch.setattr(
        cli,
        "generate_reply_for_messages",
        fake_generate_reply_for_messages,
    )

    result = runner.invoke(
        app,
        ["chat", "--model", "cli-model"],
        input="   \nHello\n/quit\n",
    )

    assert result.exit_code == 0
    assert calls == [("cli-model", "chat_completions")]


def test_chat_eof_exits_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CDY_AGENT_API_MODE", raising=False)

    result = runner.invoke(app, ["chat"], input="")

    assert result.exit_code == 0
```

- [ ] **步骤 6：补充 Ctrl-C 正常退出测试**

在 `tests/test_cli.py` 中加入 `builtins` 导入：

```python
import builtins
```

并在文件末尾追加：

```python
def test_chat_keyboard_interrupt_exits_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CDY_AGENT_API_MODE", raising=False)

    def raise_keyboard_interrupt(*args: object, **kwargs: object) -> str:
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "input", raise_keyboard_interrupt)

    result = runner.invoke(app, ["chat"])

    assert result.exit_code == 0
```

运行：

```powershell
uv run pytest tests/test_cli.py::test_chat_keyboard_interrupt_exits_cleanly -v
```

预期：`1 passed`；测试通过 monkeypatch `builtins.input` 精确隔离终端输入边界。

- [ ] **步骤 7：运行 REPL 交互测试**

运行：

```powershell
uv run pytest tests/test_cli.py -k "chat and not reports" -v
```

预期：两轮会话、退出命令、空输入、EOF、Ctrl-C、模型覆盖与 Chat Completions 模式测试全部通过。

- [ ] **步骤 8：先编写 chat 错误呈现失败测试**

在 `tests/test_cli.py` 末尾追加：

```python
def test_chat_reports_request_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CDY_AGENT_API_MODE", raising=False)

    def fake_generate_reply_for_messages(
        messages: Sequence[Message],
        *,
        model: str,
        api_mode: str,
    ) -> str:
        raise APIConnectionError(request=REQUEST)

    monkeypatch.setattr(
        cli,
        "generate_reply_for_messages",
        fake_generate_reply_for_messages,
    )

    result = runner.invoke(app, ["chat"], input="Hello\n")

    assert result.exit_code == 1
    assert "Check OPENAI_BASE_URL and your network connection" in result.stderr
    assert "Traceback" not in result.stderr
```

运行：`uv run pytest tests/test_cli.py::test_chat_reports_request_error -v`

预期：失败，因为 `chat` 尚未将连接异常转换为用户友好错误。

- [ ] **步骤 9：集中错误映射并让 ask、chat 共用**

在 `src/cdy_agent/cli.py` 中新增：

```python
REQUEST_ERRORS = (
    MissingAPIKeyError,
    AuthenticationError,
    APIConnectionError,
    RateLimitError,
    APIError,
    OpenAIError,
    ValueError,
    RuntimeError,
)


def _fail_for_exception(exc: Exception) -> NoReturn:
    """Render one supported request failure without exposing a traceback."""
    if isinstance(exc, (MissingAPIKeyError, AuthenticationError)):
        _fail("OpenAI authentication failed. Check OPENAI_API_KEY.")
    if isinstance(exc, APIConnectionError):
        _fail(
            "Unable to connect to OpenAI. "
            "Check OPENAI_BASE_URL and your network connection."
        )
    if isinstance(exc, RateLimitError):
        _fail("OpenAI rate limit reached. Try again later or check your quota.")
    if isinstance(exc, APIError):
        _fail(f"OpenAI request failed: {exc}")
    if isinstance(exc, OpenAIError):
        _fail(f"OpenAI client error: {exc}")
    _fail(str(exc))
```

将 `ask` 中现有多个 `except` 分支替换为：

```python
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)
```

将 `chat` 中模型调用与助手消息追加改为：

```python
        conversation.append("user", normalized_prompt)
        try:
            reply = generate_reply_for_messages(
                conversation.history,
                model=active_model,
                api_mode=api_mode,
            )
        except REQUEST_ERRORS as exc:
            _fail_for_exception(exc)
        conversation.append("assistant", reply)
        typer.echo(f"Assistant: {reply}")
```

并把 `active_model = resolve_model(model)` 与 `api_mode = resolve_api_mode()` 放入同一错误捕获中，确保非法 API 模式也使用既有提示：

```python
    try:
        active_model = resolve_model(model)
        api_mode = resolve_api_mode()
    except REQUEST_ERRORS as exc:
        _fail_for_exception(exc)
```

- [ ] **步骤 10：运行 CLI 与完整回归测试**

运行：

```powershell
uv run pytest tests/test_cli.py -v
uv run pytest
```

预期：所有既有 `ask` 测试与新增 `chat` 测试通过；完整测试全部通过。

- [ ] **步骤 11：提交 chat 命令**

```powershell
git add -- src/cdy_agent/cli.py tests/test_cli.py
git commit -m "Add interactive multi-turn chat"
```

### Task 4：中文使用文档与阶段验收

**文件：**
- 修改：`README.md`

**接口：**
- 输入：已实现的 `cdy-agent ask` 与 `cdy-agent chat` 命令。
- 输出：中文当前阶段、配置、使用与开发验证说明。

- [ ] **步骤 1：将 README 更新为中文并记录多轮用法**

用以下内容替换 `README.md`：

````markdown
# CDY Agent

CDY Agent 是一个本地个人 AI 助理项目，通过渐进式开发学习实用的 Agent 工程。

## 当前阶段

项目支持通过 Responses API 或 Chat Completions API 进行单轮问答和进程内多轮会话。工具、Skills、持久化会话和记忆将在后续阶段加入。

## 配置

在当前 PowerShell 会话中选择 API 模式并配置相应的提供商：

```powershell
# OpenAI Responses API
$env:OPENAI_API_KEY = "your-openai-key"
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
$env:CDY_AGENT_MODEL = "gpt-5.6-terra"
$env:CDY_AGENT_API_MODE = "responses"

# 或 DeepSeek Chat Completions API
$env:OPENAI_API_KEY = "your-deepseek-key"
$env:OPENAI_BASE_URL = "https://api.deepseek.com"
$env:CDY_AGENT_MODEL = "deepseek-v4-flash"
$env:CDY_AGENT_API_MODE = "chat_completions"
```

`CDY_AGENT_API_MODE` 只接受 `responses` 或 `chat_completions`，默认值为 `responses`。`OPENAI_BASE_URL` 可以指向 OpenAI-compatible 提供商或网关。`--model` 优先于 `CDY_AGENT_MODEL`；两者都未设置时使用默认模型 `gpt-5.6-terra`。

## 使用

发送单轮问题：

```powershell
uv run cdy-agent ask "用一句话介绍你自己"
uv run cdy-agent ask "解释 Agent Loop" --model gpt-5.6-luna
```

启动进程内多轮会话：

```powershell
uv run cdy-agent chat
uv run cdy-agent chat --model gpt-5.6-luna
```

在会话中输入 `/exit`、`/quit`，或发送 EOF 即可退出。会话历史只保留在当前进程中。

## 开发

需要 Python 3.10+ 和 [uv](https://docs.astral.sh/uv/)。

```powershell
uv sync --extra dev
uv run pytest
uv run cdy-agent --help
uv run cdy-agent ask --help
uv run cdy-agent chat --help
uv build
```
````

- [ ] **步骤 2：运行全部自动化测试**

运行：`uv run pytest`

预期：所有测试通过，且没有网络请求。

- [ ] **步骤 3：验证所有 CLI 帮助入口**

分别运行：

```powershell
uv run cdy-agent --help
uv run cdy-agent ask --help
uv run cdy-agent chat --help
```

预期：三个命令均以状态码 0 退出；顶层帮助列出 `ask` 和 `chat`；子命令帮助分别显示提示参数和 `--model` 选项。

- [ ] **步骤 4：构建发行包**

运行：`uv build`

预期：命令以状态码 0 退出，并生成源码包和 wheel；生成物由 `.gitignore` 排除。

- [ ] **步骤 5：检查差异与工作区范围**

运行：

```powershell
git diff --check
git status --short
```

预期：无空白错误；只显示本任务的 `README.md` 修改，不包含 `.idea/`、密钥、缓存、模型响应或无关文件。

- [ ] **步骤 6：提交中文文档**

```powershell
git add -- README.md
git commit -m "Document multi-turn chat usage"
```

## 最终验证

完成所有任务后，再分别运行：

```powershell
uv run pytest
uv run cdy-agent --help
uv run cdy-agent ask --help
uv run cdy-agent chat --help
uv build
git status --short
```

预期结果：

- 全部 pytest 测试通过。
- 顶层帮助同时列出 `ask` 和 `chat`。
- `ask` 和 `chat` 帮助均可正常显示。
- Hatchling 成功构建源码包和 wheel。
- 工作区不包含本阶段遗漏的修改、生成缓存、凭据或模型响应。
