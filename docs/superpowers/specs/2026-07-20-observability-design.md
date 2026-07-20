# CDY Agent 可观测性设计

## 背景

CDY Agent 已完成路线图第 7 阶段，具备双 API 模式、Agent Tool Loop、
工作区工具、Skills、持久化会话和显式长期记忆。路线图第 8 阶段包含结构化日志、
调用轨迹、Token 与费用统计、配置分层、流式输出和评估用例。

这些能力可以独立交付。本设计只覆盖第 8 阶段的第一个纵向切片：可观测性。
配置分层、流式输出和评估体系留给后续独立设计。

## 目标

- 为每次 `ask` 调用和每个 `chat` 用户回合保存一条版本化调用轨迹。
- 记录 Agent 回合、模型调用和工具调用的状态与耗时。
- 统一记录 Responses API 与 Chat Completions API 返回的 Token 用量。
- 根据用户配置的单价估算输入、输出和总费用。
- 提供 `traces list` 和 `traces show <trace-id>` 查询命令。
- 使用标准库输出不含敏感内容的结构化诊断日志。
- 保持观测失败与主要 Agent 行为隔离。

## 非目标

- 不记录 prompt、模型回复正文、工具参数、确认内容或工具结果。
- 不内置可能过期的模型价格表。
- 不提供 trace 筛选、分页、导出、删除、轮转或自动清理。
- 不保证多个 `cdy-agent` 进程并发追加同一个 JSONL 文件的行为。
- 不在本切片实现配置文件分层、流式输出、评估框架或外部观测平台集成。
- 不改变现有会话与长期记忆的持久化语义。

## 总体架构

新增 `src/cdy_agent/observability/`，使用显式注入的 `TraceRecorder`，不使用
全局可变 trace 状态。CLI 为一次 `ask` 或一个 `chat` 用户回合创建 recorder，
Agent 在模型与工具调用边界记录 span，CLI 在本回合成功或失败时完成并保存 trace。

```text
CLI 创建 trace
  -> Agent 调用 ModelGateway
     -> ModelGateway 标准化模型结果和 usage
     -> TraceRecorder 完成模型 span
  -> Agent 执行零到多个工具
     -> TraceRecorder 完成工具 span
  -> Agent 返回或抛出异常
  -> CLI 完成 trace 并追加到 TraceStore
  -> CLI 按原有行为显示回复或错误
```

普通诊断日志和持久化轨迹分开：JSONL trace 是稳定、可查询的领域数据；标准库
`logging` 输出只用于运行时诊断，日志文本不是查询接口。

## 模块与职责

### `observability/models.py`

定义不可变数据结构及其严格的 JSON 编解码：

- `TokenUsage`：输入、输出和总 Token。
- `EstimatedCost`：输入、输出和总费用的十进制字符串。
- `ModelCallSpan`：一次 SDK 模型调用的状态、耗时和可选 usage。
- `ToolCallSpan`：一次工具执行的名称、状态和耗时。
- `TraceRecord`：一次 Agent 回合的元数据、汇总和全部 spans。

模型必须验证 schema 版本、UUID、时间、非负数值、枚举字段以及聚合字段的一致性。

### `observability/pricing.py`

从以下环境变量解析每百万 Token 单价：

- `CDY_AGENT_INPUT_COST_PER_MILLION`
- `CDY_AGENT_OUTPUT_COST_PER_MILLION`

两个变量必须同时缺失或同时存在。存在时都必须是非负十进制数，否则 CLI 在创建
Agent 前给出清晰的配置错误。费用使用 `Decimal` 计算，JSON 中保存普通十进制
字符串，避免浮点误差和科学计数法造成的不稳定输出。未配置价格时仍记录 Token，
费用为 `null`。配置的价格只作用于本次 CLI 所选择的模型和提供商。

### `observability/recorder.py`

维护单个 trace 的生命周期。它使用 UTC 时间记录开始时间，使用
`time.perf_counter()` 计算单调耗时，并负责：

- 创建模型和工具 span 的稳定 UUID 与递增序号。
- 在调用成功或失败时完成 span。
- 汇总所有具有 usage 的模型 span。
- 使用价格配置计算费用。
- 将本回合完成为 `succeeded` 或 `failed` 的 `TraceRecord`。

recorder 不负责文件 IO，也不接触 prompt、回复或工具载荷。

### `observability/store.py`

`TraceStore` 将每条完整 trace 序列化为一行 UTF-8 JSON，追加到
`<workspace>/.cdy-agent/traces.jsonl`。只有保存第一条 trace 时才创建目录和文件；
只读查询空存储不会产生文件。

读取时逐行严格验证。空行、无效 JSON、未知 schema 版本或字段错误都会转换为包含
具体行号的存储错误，不静默忽略。列表按开始时间倒序返回记录；按 ID 查询要求完整、
规范的 UUID。

### `observability/logging.py`

配置标准库 `logging` 向 stderr 输出单行 JSON。`CDY_AGENT_LOG_LEVEL` 只接受
`DEBUG`、`INFO`、`WARNING`、`ERROR`，默认 `WARNING`；无效值属于启动阶段配置错误。

`INFO` 记录 trace 开始和结束，`DEBUG` 记录模型与工具 span 完成。固定字段包括
时间、级别、事件名、trace ID、可选 span ID、状态和耗时。不得包含 prompt、回复、
工具参数、工具结果、完整异常消息、API Key 或其他环境变量值。

### 现有模块调整

- `openai_client.py` 从两种 SDK 响应提取 usage，并在标准化模型结果中携带它。
  Responses API 映射 `input_tokens` 和 `output_tokens`；Chat Completions API 映射
  `prompt_tokens` 和 `completion_tokens`。
- `agent.py` 接受可选 recorder，在每次 gateway 调用与 registry 执行前后记录 span。
  recorder 可选以保持 Agent 可单独使用和现有调用方兼容。
- `cli.py` 负责解析观测配置、按回合创建 recorder、完成并持久化 trace，以及注册
  `traces` 命令组。
- `ToolRegistry` 不依赖 observability；工具观测保持在 Agent 编排边界。

## Trace 数据模型

每条 JSON 对象包含：

- `schema_version`：首版固定为 `1`。
- `trace_id`：完整 UUID。
- `started_at`：UTC ISO 8601 时间。
- `duration_ms`：非负整数毫秒。
- `command`：`ask` 或 `chat`。
- `status`：`succeeded` 或 `failed`。
- `model` 和 `api_mode`。
- `session_id`：`chat` 的持久化会话 ID；`ask` 为 `null`。
- `error_type`：失败时的稳定异常类名；成功时为 `null`。
- `usage`：可选 Token 汇总。
- `estimated_cost`：可选费用汇总。
- `model_calls`：有序模型 span 数组。
- `tool_calls`：有序工具 span 数组。

模型 span 包含 `span_id`、`sequence`、`duration_ms`、`status`、`error_type` 和
可选 `usage`。工具 span 额外包含 `tool_name`，但不包含调用参数和结果。

模型 API 未返回 usage 时，该 span 的 usage 为 `null`。只要至少一个模型 span 提供
usage，trace 就汇总所有已知 usage；所有模型 span 都没有 usage 时，trace usage 为
`null`。只有价格和 trace usage 均存在时才生成估算费用。

## 回合与状态语义

`ask` 每次实际执行 Agent 都产生一个 trace。`chat` 每个实际发送给 Agent 的用户回合
产生独立 trace，并关联当前会话 ID。空输入、`/exit`、`/quit` 和 EOF 不产生 trace。

模型调用返回标准化结果时，模型 span 成功；抛出异常时，模型 span 与 trace 失败，
然后重新抛出异常供现有 CLI 错误处理。工具返回 `ToolResult(ok=False)` 时，工具 span
失败，但 Agent Loop 可以把结构化失败交回模型后继续。工具执行意外抛出异常时，
工具 span 失败并沿用现有异常传播行为。达到 Agent 模型调用上限时 trace 失败，已完成
spans 和 usage 仍然保存。

最终回复成功生成后，trace 状态表示 Agent 回合成功。`chat` 后续会话保存失败不改变
这条 Agent trace 的状态，因为会话持久化不属于 Agent Loop；原有 CLI 会话错误行为
保持不变。

## CLI 查询

新增命令组：

```text
cdy-agent traces list --workspace <path>
cdy-agent traces show <trace-id> --workspace <path>
```

`traces list` 按时间倒序显示 trace ID、开始时间、状态、命令、模型、耗时、总 Token
和估算总费用。没有文件或记录时显示 `No saved traces.`。

`traces show` 要求完整 UUID，展示 trace 汇总、usage、费用，以及按序号排列的模型和
工具 spans。不存在的 ID 给出清晰错误。CLI 不展示任何未存储的敏感载荷。

## 错误隔离与安全

无论 Agent 成功或失败，CLI 都尝试完成并写入 trace。观测属于辅助能力：trace 构建
或写入失败时向 stderr 输出简短警告，但不吞掉成功回复，也不替换原始模型、工具或
会话错误。查询命令遇到损坏文件时则直接失败，避免提供不完整且看似可信的结果。

trace 与日志均禁止记录：

- 用户 prompt 和模型回复正文。
- 工具参数、确认文本和工具返回内容。
- 长期记忆、笔记、Todo 或文件内容。
- API Key、完整环境变量和异常正文。

轨迹文件继续位于现有默认被 Git 忽略的 `.cdy-agent` 工作区数据目录。

## 测试策略

单元测试覆盖：

- 两种 API usage 映射及 usage 缺失行为。
- Token 聚合、`Decimal` 费用计算和稳定序列化。
- 价格变量成对校验、非负校验和日志级别校验。
- 成功、模型失败、工具结构化失败、工具异常和循环超限的 recorder 生命周期。
- JSONL 追加、空存储、损坏行、未知版本、倒序列表和完整 UUID 查询。
- JSON 日志的字段、级别过滤和敏感数据缺失。

Agent 与 CLI 回归测试覆盖：

- `ask` 一次执行保存一条 trace。
- `chat` 每个用户回合保存独立且关联 session ID 的 trace。
- 退出命令和空输入不产生 trace。
- trace 写入失败不改变成功回复或主要错误。
- `traces list` 与 `traces show` 的输出和错误展示。
- trace 与日志序列化结果不包含测试 prompt、回复、工具参数和工具结果。

所有自动测试使用临时 workspace 和伪造 SDK 或 gateway，不读取用户真实文件、不要求
真实 API Key，也不访问网络。

## 验收标准

- `ask` 和 `chat` 按定义生成可查询的版本化 JSONL trace。
- 两种 API 模式的已返回 Token 用量都能统一统计。
- 完整价格配置产生精确费用估算；未配置时费用为 `null`；错误配置启动失败。
- 模型和工具 spans 能表达调用顺序、耗时与状态，且不泄漏敏感载荷。
- `traces list` 和 `traces show` 对空、正常、未知 ID 和损坏存储提供明确结果。
- 结构化日志遵守级别配置和敏感数据限制。
- 观测写入失败不改变 Agent 的主要成功或失败结果。
- `uv run pytest`、CLI help 检查和 `uv build` 全部通过。
