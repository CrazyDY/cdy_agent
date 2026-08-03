# Restored README content from parent commit 33fd9d6dc30f6a91352838e4b3783df3bbb36ba3
# The full README content was restored to remove unintended changes from the reverted commit.

# CDY Agent

CDY Agent 是一个本地个人 AI 助理项目，通过渐进式开发学习实用的 Agent 工程。

## 当前阶段

项目支持通过 Responses API 或 Chat Completions API 进行单轮问答和多轮会话，两种 API 模式均可通过同一个 Agent Tool Loop 使用受限的本地工具和流式工具调用[...]

## 配置

配置按以下顺序分层解析：命令行选项、环境变量、工作区配置文件、内置默认值。
API 凭证仍只从环境变量读取，不写入配置文件。

在当前 PowerShell 会话中选择 API 模式并配置相应的提供商：

```powershell
# OpenAI Responses API
$env:OPENAI_API_KEY = "your-openai-key"
$env:OPENAI_BASE_URL = "https://api.openai.com/v1"
$env:CDY_AGENT_MODEL = "gpt-5.6-terra"
$env:CDY_AGENT_API_MODE = "responses"
$env:CDY_AGENT_MAX_MODEL_CALLS = "8"

# 或 DeepSeek Chat Completions API
$env:OPENAI_API_KEY = "your-deepseek-key"
$env:OPENAI_BASE_URL = "https://api.deepseek.com"
$env:CDY_AGENT_MODEL = "deepseek-v4-flash"
$env:CDY_AGENT_API_MODE = "chat_completions"
```

`CDY_AGENT_API_MODE` 只接受 `responses` 或 `chat_completions`，默认值为 `responses`。`OPENAI_BASE_URL` 可以指向 OpenAI-compatible 提供商或网关。`--model` 优先于 `CDY_AGENT_[...]

工作区可以提供非敏感默认配置，文件路径为 `<workspace>/.cdy-agent/config.yaml`：

```yaml
model: deepseek-v4-flash
api_mode: chat_completions
stream: false
max_model_calls: 8
log_level: INFO
observability:
  input_cost_per_million: "1.25"
  output_cost_per_million: "2.50"
```

`OPENAI_API_KEY` 和 `OPENAI_BASE_URL` 不属于工作区配置，仍通过环境变量提供。流式输出的优先级为命令行 `--stream/--no-stream`、`CDY_AGENT_STREAM`、工作区 `stream[...]