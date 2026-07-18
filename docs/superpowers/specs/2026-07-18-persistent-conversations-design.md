# 持久化会话设计

## 背景

CDY Agent 的第 7 阶段包含会话持久化，以及由用户明确控制的长期记忆、检索和遗忘。为保持纵向小步交付，本设计只覆盖第一个子阶段：持久化 `chat` 会话。长期记忆将在会话持久化完成后单独设计。

当前 `Conversation` 只在进程内保存按顺序排列的用户与助手消息。退出 `chat` 后历史丢失，用户也无法列出、恢复或删除旧会话。

## 目标

- 将成功完成的 `chat` 轮次保存到当前 workspace。
- 允许用户显式列出、恢复和删除会话。
- 保证一次完整的用户消息与助手回复原子写入，不保存失败或残缺轮次。
- 保持 Conversation、CLI 和存储层职责清晰且可独立测试。
- 使用 Python 标准库 SQLite，不引入向量数据库或新的运行时依赖。

## 非目标

- `ask` 继续保持无状态。
- 不自动恢复最近会话。
- 不提供会话重命名、搜索、导出或分页。
- 不接受会话 ID 的缩写或前缀匹配。
- 不在本子阶段实现长期记忆、语义检索、摘要或上下文裁剪。
- 不提前创建通用存储后端抽象。

## 架构

新增 `src/cdy_agent/memory/` 包，并在 `memory/sqlite.py` 中实现具体的 `ConversationStore`。它集中负责 SQLite 路径验证、schema 初始化、事务、查询和错误转换。

现有职责保持不变：

- `Conversation` 是纯内存领域对象，只负责规范化消息和维护有序历史，不读取或写入数据库。
- `Agent` 继续接收消息快照并运行模型与工具循环，不感知会话是否持久化。
- CLI 负责创建或恢复内存会话、调用 Agent，并在成功回复后请求存储层提交完整轮次。
- `ConversationStore` 不依赖 CLI、Agent 或 OpenAI SDK。

不将 SQL 放入 CLI，也不让 `Conversation` 自己管理数据库，从而避免用户交互、会话规则和持久化细节互相耦合。

## 数据位置与安全边界

数据库位于：

```text
<workspace>/.cdy-agent/cdy-agent.sqlite3
```

该位置沿用笔记、Todo 和 Skills 的 workspace 隔离约定，并已由仓库忽略规则排除。存储层在访问前解析 `.cdy-agent` 及数据库文件的真实路径，拒绝越过 workspace 的符号链接、非常规文件或目录。

只读操作不会为了空结果创建 `.cdy-agent` 或数据库。第一次成功保存会话时才创建数据目录和数据库。

## SQLite Schema

首版使用两张表：

```sql
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE messages (
    session_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL CHECK (length(trim(content)) > 0),
    PRIMARY KEY (session_id, sequence),
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
```

会话 ID 使用规范格式的 UUID。时间戳使用 UTC ISO 8601 格式。`sequence` 从零开始并在单个会话内连续递增，使恢复顺序不依赖插入顺序或时间戳精度。

SQLite 的 `PRAGMA user_version` 记录 schema 版本。新数据库初始化为版本 1；版本高于或不同于当前支持版本时拒绝打开并给出清晰错误。本阶段没有旧 schema，因此不实现迁移。

每个连接启用外键约束。删除 `sessions` 行时，对应消息由数据库级联删除。

## 新会话数据流

1. `cdy-agent chat` 解析 workspace、模型和 API 模式，并准备一个新的 UUID，但不立即创建数据库行。
2. CLI 创建空的内存 `Conversation` 并进入输入循环。
3. 用户输入经过现有规范化和退出命令处理后追加到内存会话。
4. Agent 使用当前完整历史生成回复。
5. Agent 成功返回后，CLI 请求 `ConversationStore` 在一个事务中保存本轮用户消息和助手回复；首次保存同时创建会话元数据。
6. 事务成功后，CLI 将助手回复追加到内存会话并显示给用户。
7. 后续轮次重复同一流程，并更新 `sessions.updated_at`。

如果用户在第一轮成功前退出、发送 EOF 或模型调用失败，不创建空会话。模型调用失败时，内存中临时追加的用户消息不会写入数据库，进程按当前错误处理方式退出。

如果模型已经返回但存储失败，事务回滚且 CLI 不显示助手回复，并以明确错误退出。这样不会向用户暗示该轮已被可靠保存。

## 恢复数据流

`cdy-agent chat --resume <session-id>` 在进入输入循环前执行以下操作：

1. 要求完整且规范的 UUID，不进行前缀匹配。
2. 从当前 workspace 的数据库加载会话及按 `sequence` 排序的消息。
3. 校验角色、非空内容、连续序号和成对的用户/助手轮次。
4. 将消息重建为内存 `Conversation`。
5. 后续成功轮次通过与新会话相同的事务路径追加。

目标不存在、数据库不存在、数据损坏或 schema 不受支持时，在提示用户输入前失败。恢复失败不会自动创建同 ID 或新的会话。

## CLI 接口

新增和调整后的接口为：

```text
cdy-agent chat
cdy-agent chat --resume <session-id>
cdy-agent sessions list [--workspace PATH]
cdy-agent sessions delete <session-id> [--workspace PATH]
```

`chat` 保留现有 `--model` 和 `--workspace`。`--resume` 只影响会话历史，不改变模型和 API 模式的解析规则。

`sessions list` 按 `updated_at` 降序列出：

- 完整会话 ID；
- 最近更新时间；
- 消息数量；
- 第一条用户消息的单行截断摘要。

空存储输出清晰的空列表提示，并且不创建文件。

`sessions delete` 在显示完整会话 ID 后使用默认 No 的确认。拒绝确认不修改数据库；确认后在事务内删除会话及其消息。目标不存在时返回非零退出码和明确错误。

## 存储接口

`ConversationStore` 提供满足当前 CLI 用例的具体操作：

- 保存一个完整轮次，首次保存时创建会话；
- 按 ID 加载完整会话；
- 列出会话摘要；
- 按 ID 删除会话。

返回值使用专用的会话记录和摘要数据类型，或抛出存储层定义的、可安全展示的异常。它不复用工具层的 `ToolResult`，因为这些操作是 CLI 内部基础设施而不是模型可调用工具。

## 一致性与错误处理

- 保存一轮的两条消息、会话创建及 `updated_at` 更新位于同一事务。
- 写入前查询现有最大序号，并验证它代表完整轮次；新消息使用接续的两个序号。
- 所有写事务使用 SQLite 原子提交；任何异常均回滚。
- SQLite 打开、读取、锁冲突、写入及损坏错误转换为稳定的存储错误，不向 CLI 泄漏 SQL 或堆栈。
- 不支持的 schema 版本、无效 UUID、缺失会话和损坏历史使用可区分的错误消息。
- 数据库文件存在但不是正常 SQLite 数据库时拒绝读取或覆盖。
- 本阶段依赖 SQLite 自身的并发锁和事务，不额外实现跨进程应用锁或自动重试。

## 测试策略

所有自动测试使用临时 workspace，不访问真实用户数据、网络或真实模型。

存储层测试覆盖：

- 只读空存储不创建目录或数据库；
- 首个完整轮次创建会话并按顺序保存两条消息；
- 多轮追加和 `updated_at` 更新；
- 加载后恢复完整且有序的历史；
- 列表按最近更新时间排序，并返回正确计数与摘要；
- 删除会话级联删除消息；
- 无效 UUID、缺失会话、未知 schema 版本和损坏数据库；
- 消息序号、角色或轮次结构损坏时拒绝恢复；
- `.cdy-agent` 或数据库路径通过符号链接越过 workspace 时拒绝访问；
- 事务失败时不留下部分轮次。

CLI 测试覆盖：

- `chat` 新会话成功保存及多轮追加；
- `chat --resume` 在首次 Agent 调用前传入完整历史；
- 首轮模型失败、后续模型失败、持久化失败和立即退出不留下残缺轮次；
- `sessions list` 的空结果和正常输出；
- `sessions delete` 的确认、拒绝、成功和不存在目标；
- 恢复失败在提示输入前以简洁错误退出；
- `ask` 仍不创建或修改会话数据库；
- Responses 与 Chat Completions 模式继续通过相同的 `Message` 历史调用 Agent。

最终验证命令：

```powershell
uv run pytest
uv run cdy-agent --help
uv run cdy-agent chat --help
uv run cdy-agent sessions --help
uv build
```

## 后续阶段

本设计完成后，再单独设计长期记忆。长期记忆可以复用 `.cdy-agent/cdy-agent.sqlite3` 和受控 schema 演进机制，但不会将所有会话自动转化为记忆；保存、检索和遗忘仍必须由用户明确控制。
