# CDY Agent 第二阶段：单轮 Responses API 调用设计

## 背景

第一阶段已经建立可安装、可测试、可构建的 Typer CLI 骨架。第二阶段要完成第一次真实模型调用，让用户可以通过一个命令向 OpenAI Responses API 提交文本，并在终端看到文本回复。

本阶段继续采用纵向切片方式：交付一个完整的单轮问答能力，但不提前实现会话状态、工具调用、流式输出或通用多模型抽象。

## 目标

- 提供 `cdy-agent ask PROMPT` 单轮问答命令。
- 使用官方 OpenAI Python SDK 的 Responses API。
- 默认使用 `gpt-5.6-terra`，支持环境变量和命令行覆盖。
- 从环境变量读取 API Key 和 Base URL，不在代码或命令参数中传递密钥。
- 隔离 CLI 与 SDK 网络边界，使自动测试不访问网络。
- 为常见配置和 API 错误提供简短、可行动的终端提示。
- 在自动测试完成后执行一次真实 API 手动验收。

## 非目标

- 不实现多轮会话或 `previous_response_id`。
- 不实现 Agent Tool Loop、函数工具或 Skills。
- 不实现流式输出。
- 不实现 `.env` 文件加载。
- 不实现应用级自定义重试。
- 不抽象通用 `ModelProvider` 或接入其他模型提供商。
- 不把真实 API 请求加入 pytest。

## 配置

### 环境变量

- `OPENAI_API_KEY`：API 凭据，由 OpenAI SDK 原生读取。
- `OPENAI_BASE_URL`：API 地址，由 OpenAI SDK 原生读取；未设置时 SDK 使用 OpenAI 官方端点。
- `CDY_AGENT_MODEL`：CDY Agent 的默认模型配置。

### 模型优先级

模型按以下顺序解析：

1. `--model` 命令行选项。
2. `CDY_AGENT_MODEL` 环境变量。
3. 内置默认值 `gpt-5.6-terra`。

空字符串或只有空白的命令行值、环境变量值均视为未设置，并继续使用下一层配置。有效值在使用前去除首尾空白。

## 模块设计

### `src/cdy_agent/config.py`

只负责应用配置解析：

```text
DEFAULT_MODEL: str = "gpt-5.6-terra"
resolve_model(model_override: str | None = None) -> str
```

`resolve_model()` 不创建 SDK 客户端，也不读取 API Key 或 Base URL。

### `src/cdy_agent/openai_client.py`

提供单轮文本生成边界：

```text
generate_reply(
    prompt: str,
    *,
    model: str,
    client: OpenAI | None = None,
) -> str
```

职责如下：

1. 去除 Prompt 首尾空白，并拒绝空 Prompt。
2. 未注入客户端时创建 `OpenAI()`，让 SDK 原生读取 `OPENAI_API_KEY` 和 `OPENAI_BASE_URL`。
3. 调用 `client.responses.create(model=model, input=normalized_prompt)`。
4. 读取并返回 `response.output_text`。
5. 拒绝空白的 `output_text`，避免 CLI 无输出却以成功状态结束。

此模块不打印终端文本、不解析模型优先级、不管理会话、不实现重试。

### `src/cdy_agent/cli.py`

保留现有 Typer 根应用，新增：

```text
cdy-agent ask PROMPT [--model MODEL]
```

`ask` 命令负责：

1. 接收 Prompt 和可选的 `--model`。
2. 通过 `resolve_model()` 得到最终模型。
3. 调用 `generate_reply()`。
4. 使用 `typer.echo()` 输出回复。
5. 将预期异常转换为简短错误提示和退出码 `1`。

## 正常数据流

```text
用户输入
  -> Typer ask 命令
  -> resolve_model(--model, CDY_AGENT_MODEL, 默认值)
  -> generate_reply(prompt, model)
  -> OpenAI().responses.create(model=resolved_model, input=normalized_prompt)
  -> response.output_text
  -> typer.echo(reply)
```

官方 Python 文本生成示例使用 `client.responses.create` 发起调用，并通过 `response.output_text` 获取聚合后的文本输出：<https://developers.openai.com/api/docs/guides/text>。

## 错误处理

### 本地校验错误

- Prompt 为空时，`generate_reply()` 抛出 `ValueError`。
- 模型配置全部为空时仍回退到内置默认值，不产生配置错误。
- `response.output_text` 为空时，`generate_reply()` 抛出 `RuntimeError`。

### SDK 和网络错误

CLI 针对以下错误输出用户友好提示：

- `AuthenticationError` 或缺少凭据导致的 `OpenAIError`：提示检查 `OPENAI_API_KEY`。
- `APIConnectionError`：提示检查 `OPENAI_BASE_URL` 和网络连接。
- `RateLimitError`：提示稍后重试或检查账户限额。
- 其他 `APIError`：提示 OpenAI 请求失败，并保留简短的 SDK 错误信息。

所有预期错误写入 stderr，并以退出码 `1` 结束。默认不向用户展示 Python traceback。错误信息不得打印 API Key、完整环境变量或请求头。

本阶段不增加应用级重试设置，保持 OpenAI SDK 自身的默认行为。

## 测试设计

### `tests/test_config.py`

覆盖：

- 命令行模型覆盖环境变量。
- 环境变量覆盖默认模型。
- 空白命令行值回退到环境变量。
- 空白环境变量值回退到 `gpt-5.6-terra`。
- 返回的有效模型值去除首尾空白。

### `tests/test_openai_client.py`

使用带有 `responses.create()` 的假客户端，覆盖：

- 请求传递最终模型和规范化 Prompt。
- 返回 `response.output_text`。
- 空 Prompt 在调用客户端前失败。
- 空白输出转换为明确错误。
- 未注入客户端时调用无参数 `OpenAI()`，从而保留 SDK 环境变量配置路径。

### `tests/test_cli.py`

在现有帮助测试基础上覆盖：

- `ask` 命令向模型解析和生成边界传递正确参数。
- `--model` 覆盖值被使用。
- 成功回复写入 stdout，退出码为 `0`。
- 认证、连接、限流和本地校验错误写入 stderr，退出码为 `1`。

CLI 测试通过 monkeypatch 替换 `generate_reply()`，不访问网络、不读取真实密钥。

## 文档更新

README 增加配置与使用示例：

```powershell
$env:OPENAI_API_KEY="your-api-key"
$env:OPENAI_BASE_URL="https://api.openai.com/v1"  # 可省略
$env:CDY_AGENT_MODEL="gpt-5.6-terra"             # 可省略

uv run cdy-agent ask "用一句话介绍你自己"
uv run cdy-agent ask "解释 Agent Loop" --model gpt-5.6-luna
```

官方快速入门确认 OpenAI SDK 会从 `OPENAI_API_KEY` 环境变量读取凭据：<https://developers.openai.com/api/docs/quickstart>。

README 不包含真实 API Key，也不建议通过命令行参数传递密钥。

## 验收标准

自动验收命令：

```powershell
uv run pytest
uv run cdy-agent --help
uv run cdy-agent ask --help
uv build
```

所有自动测试必须在未配置真实 API Key、未访问网络的条件下通过。

手动验收由用户在本机设置环境变量后执行：

```powershell
uv run cdy-agent ask "用一句话介绍你自己"
```

命令必须输出非空模型回复并以退出码 `0` 结束。手动响应不写入测试快照，不提交到仓库。
