# 本地工具 Agent Loop 设计

## 背景

CDY Agent 已支持 Responses API 与 Chat Completions API 的单轮问答和进程内多轮会话。当前增量将工具调用能力与首批本地工具一起交付，使模型可以在明确的安全边界内读取文件、写入文件和执行受限命令。

这项工作取代原路线图中“先用计算器或时间工具验证 Agent Tool Loop”的顺序，并提前纳入原第五阶段的部分本地工具能力。首版因此必须同时完成工具循环、用户确认和本地执行限制，不能把安全边界留到后续补充。

## 目标与非目标

### 目标

- 为 Responses API 和 Chat Completions API 提供行为一致的 Agent Tool Loop。
- 提供受 workspace 限制的 UTF-8 文件读取和写入工具。
- 提供采用参数数组、严格白名单和超时限制的 Shell 工具。
- 读取文件自动执行；写文件与 Shell 命令逐次请求用户确认。
- 将工具参数错误、拒绝和执行失败作为结构化结果回传模型。
- 保持 CLI、Agent、OpenAI SDK 适配和工具执行的职责边界清晰。
- 使用完全离线的自动测试覆盖循环、API 适配和安全限制。

### 非目标

- 不提供任意 Bash 字符串、管道、重定向、变量展开或命令拼接。
- 不提供删除、移动、目录创建、文件补丁或二进制文件工具。
- 不自动创建写入目标的父目录。
- 不加入计算器、时间、笔记或 Todo 工具。
- 不实现持久化会话、长期记忆、Skills、MCP 或通用 Provider 抽象。
- 不将工具调用中间事件持久化到普通会话历史。

## 架构与职责

数据流如下：

```text
CLI -> Agent -> OpenAI client adapter
          |
          +-> Tool Registry -> filesystem / shell
          |
          +-> confirmation callback supplied by CLI
```

新增或调整的模块职责如下：

- `agent.py` 是唯一的 Tool Loop。它调用模型、识别工具请求、协调确认、执行工具、回传结果并实施循环上限。
- `tools/base.py` 定义工具描述、统一工具调用、结构化工具结果和工具协议。
- `tools/registry.py` 注册工具，校验工具名称和 JSON 参数，并将合法调用分发给具体工具。
- `tools/filesystem.py` 实现受 workspace 限制的文件读取和写入。
- `tools/shell.py` 实现严格白名单、超时和输出限制下的命令执行。
- `openai_client.py` 继续作为唯一的 OpenAI-compatible SDK 边界。它负责两种 API 的工具 schema、调用解析和结果回传格式，但不执行工具。
- `cli.py` 解析 workspace、展示高风险操作并读取用户确认。确认能力通过回调注入 Agent，工具和 Agent 都不直接依赖终端输入。
- `conversation.py` 继续只保存用户消息和最终助手消息。

该设计只引入满足现有两种 API 所需的薄适配层，不建立可插拔 Provider 层。

## 统一模型结果

`openai_client.py` 向 Agent 返回两类统一结果：

- `FinalResponse(text)` 表示模型已产生非空最终回复。
- `ToolCallResponse(calls, continuation)` 表示模型请求工具。每个调用包含 `call_id`、工具名和 JSON 参数；`continuation` 保存当前 API 后续请求需要的原生上下文。

Responses API 适配器解析 `function_call` 输出项，并用对应 `call_id` 的 `function_call_output` 回传结果。Chat Completions 适配器保留包含 `tool_calls` 的 assistant 消息，再追加带对应 `tool_call_id` 的 `tool` 消息。

两种模式都支持一次响应中的多个工具调用。Registry 按模型返回顺序处理调用，Agent 收集本批次的所有结果后统一发起下一次模型请求。

## 工具接口

### `read_file`

参数：

- `path: str`：相对于 workspace 的文件路径，也可接受最终仍位于 workspace 内的绝对路径。

行为：

- 自动执行，无需用户确认。
- 只读取 UTF-8 普通文件，拒绝目录和无法按 UTF-8 解码的内容。
- 最多读取 1 MiB；超过上限时返回前 1 MiB 内容并添加明确截断标记。
- 路径解析后必须仍位于 workspace 内。

### `write_file`

参数：

- `path: str`：目标文件路径。
- `content: str`：写入的 UTF-8 文本。
- `overwrite: bool = false`：是否明确允许覆盖已有文件。

行为：

- 创建或覆盖前都必须得到用户确认。
- 目标已存在且 `overwrite` 不是 `true` 时，在确认前即返回拒绝覆盖错误。
- 目标父目录必须已经存在；首版不自动创建目录。
- 拒绝目录目标以及解析后位于 workspace 外的路径。
- 确认信息显示绝对目标路径、操作是创建还是覆盖，以及内容的 UTF-8 字节数。

### `run_shell`

参数：

- `argv: list[str]`：参数化命令，例如 `["git", "status", "--short"]`。
- `timeout_seconds: int = 10`：执行超时，只允许 1 到 30 秒。

行为：

- 每次执行前都必须得到用户确认。
- 使用 `subprocess.run`，固定 `shell=False` 和 `cwd=workspace`。
- `argv` 必须非空，每个元素必须是字符串；`argv[0]` 只能是单纯命令名，不能包含路径分隔符，也不能是绝对或相对路径。
- 标准输出与标准错误分别最多保留 64 KiB，超出部分截断并添加明确标记。
- 超时、非零退出和启动失败均返回结构化工具结果。
- 确认信息显示准确参数列表和 workspace。

## Shell 白名单

普通命令只允许：

- `pwd`
- `ls`
- `find`
- `rg`
- `grep`
- `sed`
- `head`
- `tail`
- `wc`
- `sort`
- `uniq`

`git` 只允许 `status` 与 `diff` 子命令。校验器拒绝出现在子命令之前的 Git 全局选项，包括 `-C`、`--git-dir` 和 `--work-tree`，因此命令不能切换仓库或工作目录。

Shell 不接收命令字符串，也不启动 Shell 解释器。参数中的 `|`、`>`、`&&` 等字符只会作为普通参数传给已批准的可执行文件，不具备 Shell 语法含义。

## Workspace 与路径安全

`ask` 和 `chat` 都增加 `--workspace PATH`。未指定时，在命令运行时使用当前工作目录。workspace 在首次模型请求前解析为绝对真实路径；路径不存在或不是目录时立即报错。

读取已有文件时解析目标的真实路径。写入新文件时先解析已经存在的父目录，再拼接文件名。所有文件操作都检查最终目标能相对于真实 workspace 表示，从而拒绝 `..` 穿越、绝对路径越界和符号链接逃逸。

确认提示默认选择 No。用户输入不明确、EOF 或键盘中断都视为拒绝，工具不得产生副作用。

## Agent Loop

一次用户请求按以下步骤执行：

1. Agent 将完整普通会话历史和工具定义发送给所选 API。
2. 最终文本响应立即结束本轮。
3. 工具请求由 Registry 校验名称和 JSON 参数。
4. `read_file` 自动执行；`write_file` 和 `run_shell` 逐个调用确认回调。
5. 每个成功、拒绝或失败结果都与原 `call_id` 关联。
6. Agent 将本批次所有结果回传模型并继续循环。
7. 最终回复或达到模型调用上限时终止。

默认最多进行 8 次模型调用。超过上限时抛出 `AgentLoopLimitError`，CLI 以现有无堆栈错误形式展示诊断。

Tool Loop 中间态只在当前用户轮次内存在。`Conversation` 在本轮成功结束后追加最终助手文本，不保存 API 特有的工具事件。

## 结果与错误处理

工具结果使用稳定的 JSON 对象。失败示例：

```json
{
  "ok": false,
  "error": {
    "code": "approval_denied",
    "message": "User declined this tool call."
  }
}
```

成功结果使用 `ok: true` 并携带工具特定数据。错误码保持简短且稳定，错误消息用于模型解释，不包含敏感环境变量或无关内部细节。

以下问题可恢复，并作为工具结果回传模型：

- 未知工具或无效 JSON 参数；
- 缺失、多余或类型错误的参数；
- 路径越界、不支持的文件类型或覆盖条件不满足；
- 命令不在白名单；
- 用户拒绝；
- 超时、非零退出或工具执行失败。

以下问题不可恢复，直接终止本轮：

- 认证、连接、限流和其他 API 错误；
- SDK 返回无法适配的响应结构；
- Agent Loop 超过 8 次模型调用。

## CLI 行为

`ask` 与 `chat` 通过同一个 Agent 入口运行，均支持 `--model` 和 `--workspace`。

读取工具不产生确认提示。写入和 Shell 请求到达时，CLI 显示操作类型、准确目标或参数、workspace 及默认拒绝的 `y/N` 提示。拒绝只拒绝当前工具调用，不退出命令；模型会收到结构化拒绝结果并产生最终说明或选择其他操作。

现有配置错误和 OpenAI 请求错误继续使用用户友好的标准错误输出与非零退出码，不展示 traceback。

## 测试策略

测试不使用真实 API、网络或用户文件系统。文件与 Shell 测试使用 pytest 临时目录，SDK 和 subprocess 边界使用伪对象或 mock。

### Registry 与工具测试

- 工具注册、查找和 schema 输出；
- 未知名称、无效 JSON、缺失、多余和错误类型参数；
- 正常读取、创建与显式覆盖；
- 默认拒绝覆盖、目录目标、UTF-8 解码错误和输出截断；
- `..`、绝对路径和符号链接越界；
- 普通命令与 Git 子命令白名单；
- Git 全局选项、路径形式可执行文件和不允许命令；
- Shell 元字符不能触发 Shell 语义；
- 命令超时、非零退出、启动失败和输出截断。

### Agent 与 API 适配测试

- 不使用工具的直接回复；
- 单次、批量和连续工具调用；
- 用户同意与拒绝；
- 工具参数或执行失败后的结果回传；
- 8 次模型调用上限；
- 两种 API 的工具 schema、调用解析、`call_id` 关联与结果回传格式；
- 普通多轮历史只保存用户消息和最终助手消息。

### CLI 与回归测试

- 默认 workspace 与显式 `--workspace`；
- 无效 workspace 的用户错误；
- 写入和 Shell 的确认展示、同意与默认拒绝；
- 拒绝时没有文件或进程副作用；
- `ask`、`chat`、模型覆盖、退出命令和请求错误的现有行为。

完成实现后运行：

```powershell
uv run pytest
uv run cdy-agent --help
uv run cdy-agent ask --help
uv run cdy-agent chat --help
uv build
```

## 验收标准

- 两种 API 模式都能完成模型请求工具、程序执行和结果回传的完整循环。
- 文件操作不能逃离 workspace；覆盖必须显式请求并得到确认。
- Shell 只执行批准的参数化命令，不能通过路径、Git 全局选项或 Shell 语法绕过限制。
- 用户拒绝和工具错误会被模型看到，不导致无 traceback 的正常错误路径失效。
- Agent 在 8 次模型调用后必然终止。
- 自动测试完全离线，完整测试、CLI help 和构建全部通过。
