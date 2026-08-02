# CDY Agent MCP 客户端设计

## 目标

CDY Agent 作为 MCP Host/Client 消费外部 Server 的 Tools、Resources、Resource Templates 与 Prompts。它不提供 MCP Server，不改变现有 OpenAI API 边界，并让 CLI、评估和 Web UI 继续复用同一个同步 Agent Tool Loop。

## 配置与安全

配置位于 `<workspace>/.cdy-agent/mcp.yaml`，版本固定为 1，Server 名称必须匹配 `[a-z][a-z0-9_]{0,31}`。stdio 只接受 command 与 argv，固定 workspace cwd 并使用净化环境；额外环境变量通过 `env_from` 引用。Streamable HTTP 远程地址必须为 HTTPS；无 Header 的 loopback HTTP 可用于本机开发。HTTP Header 值同样只通过 `headers_from` 引用环境变量。

配置本身不会启动进程或建立网络连接。模型调用 `connect_mcp_server` 时，现有 prepared-execution 审批展示准确 argv/cwd 或 HTTP origin/Header 名称；凭据值不进入确认文本、日志或 trace。所有远程 Tool 调用逐次确认且不支持永久授权。Resource 和 Prompt 读取按现有只读工具策略自动执行。

## 生命周期与工具映射

每个 Agent runtime 创建一个 `McpManager`。Manager 首次连接时启动专用 asyncio 线程，并在该线程内维护官方 SDK Client 和 transport context。同步工具调用通过有超时、可响应 `RunControl` 的 future 桥接。断线不会自动重启 Server；再次连接必须重新确认。Agent 的幂等 `close()` 在 ask、chat、evals 和 Web 退出路径关闭会话并清理 stdio 进程树。

连接成功后分页读取远程 Tools，并原子替换 registry 中对应 Server 的动态组。合法且不冲突的名称使用 `mcp_<server>_<remote>`；其他名称规范化后附加 SHA-256 摘要。单 Server 上限 64 个远程工具，全局上限 256 个，超限或无效 Schema 不产生部分注册。支持新协议的 Server 可通过 tool-list-changed subscription 触发最后有效快照的原子刷新；旧协议仍保持连接但不订阅。

## 内容和失败语义

固定工具提供 Server 列表与连接管理、Resource/Template/Prompt 分页、Resource 读取和 Prompt 获取。MCP Pydantic 结果按协议别名转换为 JSON，保留 `content`、`structuredContent`、`isError`、Prompt messages 和所有内容块；图片、音频与 blob 的 base64 保持有界。序列化结果超过 1 MiB 时整次失败，不返回看似完整的截断数据。

远程 `isError` 转换为 `mcp_tool_error`；连接、请求、超时、凭据缺失、断线和大小限制分别返回稳定 `ToolResult` 错误。错误信息不包含 Header 值、环境变量值、Server stderr 或完整内部异常。

## 非目标

- OAuth 浏览器授权和凭据持久化。
- SSE transport、MCP Tasks、Roots、Sampling 或 Elicitation。
- 自动把 Prompt 伪造成会话消息，或自动把 Resource 注入上下文。
- 将 CDY 的本地工具或 Agent 回答暴露为 MCP Server。
