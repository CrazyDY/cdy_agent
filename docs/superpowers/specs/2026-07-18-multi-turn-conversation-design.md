# 多轮会话设计

## 背景

CDY Agent 目前支持通过 OpenAI Responses API 或 Chat Completions API
发送单轮提示。路线图第三阶段将在保留现有 `ask` 命令的同时，加入进程内的
交互式会话，并维持终端交互、会话状态与 SDK 调用之间的职责边界。

## 目标

- 新增 `cdy-agent chat` REPL，同时保留 `cdy-agent ask`。
- 在单个进程的多轮交互中延续会话上下文。
- 让 Responses 与 Chat Completions 两种模式具有一致的会话语义。
- 使会话状态独立于 Typer 和 OpenAI SDK。
- 所有行为都能在不使用真实凭据、网络或持久化用户数据的情况下测试。

## 非目标

- 持久化或恢复会话。
- 裁剪、总结或以其他方式管理上下文窗口。
- 加入系统提示、流式输出、重试、工具、Skills 或记忆。
- 使用 `previous_response_id` 等提供商专属的会话标识符。

## 架构

### 会话状态

新增 `src/cdy_agent/conversation.py`，由它负责内存中的会话状态。会话按顺序
保存消息，并且只支持 `user` 和 `assistant` 两种角色。

公共接口允许调用方追加非空消息并获取当前有序历史。返回的历史不能让调用方
意外修改会话内部的集合。会话层不导入 Typer 或 OpenAI SDK，也不持久化数据。

### OpenAI 客户端边界

扩展 `src/cdy_agent/openai_client.py`，增加多消息请求边界。它接收完整的标准
会话历史，校验历史非空，并在 SDK 边界进行转换：

- Responses 模式将带角色和内容的标准消息作为结构化 `input` 发送。
- Chat Completions 模式将相同的标准消息作为 `messages` 发送。

现有 `generate_reply()` 接口继续供 `ask` 命令使用，并通过一条用户消息委托给
共享的多消息实现。这样既能保持既有单轮行为，也能将 SDK 格式转换集中在一个
模块中。

每一轮都发送完整历史。与提供商原生的响应游标相比，这种方式消耗的 Token
更多，但能让两种 API 模式采用同一种状态模型，并兼容更多 OpenAI-compatible
提供商和网关。

### CLI 边界

在 `src/cdy_agent/cli.py` 中新增 `chat` 命令。命令启动时解析一次模型覆盖值和
已配置的 API 模式，然后运行终端输入循环。CLI 负责提示符、退出识别、输出标签
和面向用户的错误信息，但不构造 SDK 请求载荷。

从用户视角看，现有 `ask` 命令的行为保持不变。

## 数据流

对于每一条非空的用户输入：

1. CLI 将用户消息追加到内存会话。
2. CLI 将完整的有序历史传给 OpenAI 客户端边界。
3. 客户端根据已配置的 API 模式转换历史并发起一次 SDK 调用。
4. 调用成功后，CLI 将助手文本追加到会话并输出。
5. 下一条用户输入会同时携带之前的用户消息和助手消息。

模型成功返回之前，绝不向会话追加助手消息。

## REPL 交互契约

- 使用 `cdy-agent chat` 启动 REPL，并可通过 `--model` 覆盖模型。
- 使用 `You: ` 提示符读取每轮输入。
- 使用 `Assistant: ` 前缀输出回复。
- 忽略空输入或只包含空白字符的输入，不调用模型。
- 去除首尾空白后，将 `/exit` 和 `/quit` 作为不区分大小写的退出命令。
- 将 EOF 和 `KeyboardInterrupt` 视为正常退出。
- 所有状态只保留在当前进程中，并在退出时丢弃。

## 校验与错误处理

会话状态拒绝空白内容和不支持的角色。OpenAI 客户端拒绝空历史、不支持的 API
模式和空模型输出，然后才将控制权交还给 CLI。

配置、认证、连接、限流、SDK 和无效输出错误沿用 `ask` 的简洁用户提示。请求
失败时不追加助手消息，并以状态码 1 终止 `chat`。REPL 内重试和故障恢复不属于
本阶段范围。

## 测试

新增以下离线测试：

- 按顺序追加会话消息、校验角色与内容，并防止意外修改内部消息集合。
- 通过 Responses 和 Chat Completions 两种模式发送完整的两轮历史。
- 保持当前所有单轮 `generate_reply()` 和 `ask` 行为不变。
- 成功运行两轮 `chat`，并验证第二次请求包含第一轮上下文。
- 忽略空输入；处理 `/exit`、`/quit`、EOF 和 Ctrl-C；解析模型与 API 模式；
  以及在不显示堆栈的情况下呈现请求错误。

测试使用假 SDK 客户端、Typer 的 `CliRunner` 和 monkeypatch，不使用真实 API
Key、网络或贡献者的文件系统。

## 文档与验证

更新 `README.md`，将多轮会话标记为当前阶段，并记录 `ask` 和 `chat` 的用法。
使用以下命令验证实现：

```powershell
uv run pytest
uv run cdy-agent --help
uv run cdy-agent ask --help
uv run cdy-agent chat --help
uv build
```
