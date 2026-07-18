# CDY Agent

CDY Agent 是一个本地个人 AI 助理项目，通过渐进式开发学习实用的 Agent 工程。

## 当前阶段

项目支持通过 Responses API 或 Chat Completions API 进行单轮问答和进程内多轮会话，两种 API 模式均可通过同一个 Agent Tool Loop 使用受限的本地文件、Shell、笔记和 Todo 工具。笔记与 Todo 按 workspace 持久化；Skills、持久化会话和长期记忆将在后续阶段加入。

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
uv run cdy-agent ask "读取 README.md 并总结"
uv run cdy-agent ask "检查仓库状态" --workspace .
```

启动进程内多轮会话：

```powershell
uv run cdy-agent chat
uv run cdy-agent chat --model gpt-5.6-luna
uv run cdy-agent chat --workspace .
```

在会话中输入 `/exit`、`/quit`，或发送 EOF 即可退出。会话历史只保留在当前进程中。

### 本地工具与安全边界

`ask` 和 `chat` 都向模型提供以下工具：

- `read_file`：读取工作区内的 UTF-8 常规文件；单次最多返回 1 MiB，超出时明确标记截断。
- `write_file`：在工作区内创建或写入 UTF-8 文件；不会创建缺失的父目录，覆盖已有文件时必须显式传入 `overwrite=true`。
- `shell`：以参数数组在工作区内运行受限命令，不通过 Shell 解释命令字符串。
- `create_note`、`list_notes`、`get_note`、`delete_note`：创建、列出、查看和删除 workspace 笔记。
- `create_todo`、`list_todos`、`complete_todo`、`delete_todo`：创建、列出、完成和删除 workspace Todo。

工作区默认为命令启动时解析后的当前目录，也可通过 `--workspace` 指定。文件工具会解析真实路径（包括符号链接）并拒绝访问工作区之外的路径。

每次 `write_file` 和 `shell` 调用都会显示操作说明并请求确认，默认答案为 No；`read_file` 不需要确认。Shell 超时可设为 1–30 秒（默认 10 秒），标准输出和标准错误分别最多返回 64 KiB。Shell 只允许 `pwd`、`ls`、`find`、`rg`、`grep`、`sed`、`head`、`tail`、`wc`、`sort`、`uniq`，以及 `git status` 和 `git diff`。

### 笔记与 Todo 数据

笔记保存在 `<workspace>/.cdy-agent/notes.json`，Todo 保存在 `<workspace>/.cdy-agent/todos.json`。创建、完成和删除操作每次都需要默认 No 的用户确认；列表和查看不会请求确认，也不会为了空列表创建数据目录。

数据文件使用严格校验的版本化 JSON 和原子替换写入。格式损坏、版本未知或路径越过 workspace 时，工具会拒绝操作，不会用空数据覆盖原文件。同一 workspace 首版只允许一个 `cdy-agent` 进程执行修改。

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
