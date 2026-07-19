# 长期记忆设计

## 背景

CDY Agent 第 7 阶段的第一个子阶段已经交付 workspace 范围的
SQLite 会话持久化。用户可以列出、恢复和删除完整会话，但无法把一条可复用的
事实或偏好带到新的会话中。本设计完成第 7 阶段的第二个子阶段：由用户明确控制
的长期记忆、检索、修改和遗忘。

长期记忆与会话历史保持独立。会话历史记录一次聊天的完整用户与助手消息；长期
记忆只保存用户明确选择的独立信息。系统不会从会话中自动提取记忆，也不会在新
请求中自动注入记忆。

## 目标

- 在当前 workspace 中持久保存用户明确要求保存的长期记忆。
- 同时提供模型可调用工具和 CLI 命令，用于新增、查看、检索、修改和遗忘记忆。
- 只有用户明确要求时才允许模型检索长期记忆。
- 所有新增、修改和遗忘操作都在执行前向用户展示准确内容并二次确认。
- 使用可解释的关键词与标签检索，不引入向量数据库或新的运行时依赖。
- 复用现有 SQLite 文件和 workspace 路径安全边界，并安全迁移现有会话数据。
- 保持数据库基础设施、会话存储、长期记忆存储、模型工具和 CLI 的职责清晰。

## 非目标

- 不自动从对话中提取、总结或保存记忆。
- 不自动检索或把记忆注入每轮模型上下文。
- 不提供向量检索、嵌入、模糊匹配、同义词推断或模型辅助筛选。
- 不提供全局用户级记忆或跨 workspace 共享。
- 不把全部会话转化为记忆，也不在删除会话时联动删除记忆。
- 不提供分页、批量导入导出、记忆合并或自动解决相似记忆。
- 不创建通用 Provider 或任意实体存储抽象。

## 用户控制语义

长期记忆只能响应用户对当前操作的明确请求：

- 用户明确要求“记住”某项信息时，模型可以请求新增记忆。
- 用户明确要求“回忆”“查找记忆”或同等含义时，模型可以请求检索。
- 用户明确要求修改某条记忆时，模型可以请求完整替换该记录。
- 用户明确要求忘记某条记忆时，模型可以请求删除。

模型工具说明必须声明这一限制。程序通过现有确认边界强制保护所有写操作；检索
意图由当前用户消息和工具说明共同约束。系统不运行后台检索，也不在 Agent、CLI
或模型网关中预加载记忆。

## 架构

`src/cdy_agent/memory/` 划分为三个职责：

- `database.py` 统一负责 workspace 数据路径验证、SQLite 连接、schema 初始化和
  迁移。
- `sqlite.py` 保留 `ConversationStore`，只负责持久化会话。
- `long_term.py` 新增 `MemoryStore`，只负责长期记忆。

现有 `ConversationStore` 的公共行为保持不变。抽取数据库基础设施是为了让两个
领域存储安全地共享同一数据库和 schema 版本，不演化为可替换后端或通用仓储层。

模型工具放在 `src/cdy_agent/tools/memories.py`，遵循现有 `Tool` 协议，并由
`create_builtin_registry()` 使用当前 workspace 的 `MemoryStore` 注册。CLI 只负责
参数解析、确认、输出和安全错误展示；SQL、重复判断和查询规则全部留在存储层。

## 数据位置与安全边界

继续使用现有数据库：

```text
<workspace>/.cdy-agent/cdy-agent.sqlite3
```

数据库基础设施在访问前解析 `.cdy-agent` 目录和数据库文件的真实路径，拒绝越过
workspace 的符号链接、非常规文件或目录。只读空存储不会创建目录或数据库；第
一次成功写入会话或记忆时才创建最新 schema。

长期记忆只在当前 workspace 内可见。`ask` 与 `chat` 都可在用户明确要求时通过
工具访问当前 workspace 的记忆；`ask` 仍不保存任何会话历史。

## SQLite Schema 与迁移

schema 版本从 1 升级至 2。版本 2 保留现有 `sessions` 和 `messages` 表并新增：

```sql
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL CHECK (length(trim(content)) > 0),
    identity_hash TEXT NOT NULL UNIQUE CHECK (length(identity_hash) = 64),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE memory_tags (
    memory_id TEXT NOT NULL,
    tag TEXT NOT NULL CHECK (length(trim(tag)) > 0),
    PRIMARY KEY (memory_id, tag),
    FOREIGN KEY (memory_id) REFERENCES memories(id) ON DELETE CASCADE
);
```

写连接先执行 `BEGIN IMMEDIATE`，再在持有 SQLite 写锁时读取版本和结构。版本 0
且没有用户表表示未初始化占位，可在当前事务中建立完整版本 2 schema；失败事务
可以留下空的 SQLite 占位文件，后续写入可安全恢复，只读则把它视为空存储且绝不
修改。版本 0 但包含用户对象时拒绝。实现不通过锁外的文件存在性快照判断文件归属，
异常清理也绝不删除数据库文件，因此并发首写者不能删掉另一写入者已提交的数据。

打开版本 1 数据库进行写操作时，先完整验证 v1，再在单一事务中创建新表、更新
`PRAGMA user_version` 并完整验证 v2；任何失败都回滚，原会话数据保持可用且版本
仍为 1。版本 1 的只读会话操作仍可读取现有表；需要读取长期记忆时返回空结果而
不触发迁移。每次 v1/v2 读写都验证精确应用表集合、列名、声明类型、`NOT NULL`、
主键位置、外键及 `ON DELETE CASCADE`、必需唯一/主键索引，以及角色、非空正文/
标签和 64 字符 identity 的关键 `CHECK`。SQLite 内部表可忽略；任何其他版本或结构
不匹配都使用安全的无效存储错误拒绝，不猜测或覆盖。

每个连接启用外键约束。删除 `memories` 行时，标签由数据库级联删除。时间戳继续
使用 UTC ISO 8601 格式，ID 继续使用完整规范 UUID。

## 记忆模型与规范化

一条存储记录包含：

- 完整规范 UUID；
- 正文；
- 零到十个标签；
- 创建时间和最近更新时间。

正文去除首尾空白后必须非空，最多为 8 KiB UTF-8；内部空白和换行保持不变。
标签去除首尾空白并使用 Unicode `casefold()` 转为规范形式。每个标签规范化后必须
包含 1 至 50 个字符；同一记录内的标签去重并按字典序返回。

正文与规范化标签集合都完全相同的记录视为重复。存储层拒绝第二次保存，返回稳定
的重复错误并包含已有记忆的 ID。相似但不完全相同的内容允许共存；系统不猜测它们
是否表达同一事实。`identity_hash` 是以下稳定字节格式的 lowercase SHA-256：

```text
b"CDYMEM1" +
uint64_be(len(content_utf8)) + content_utf8 +
uint32_be(tag_count) +
for each sorted tag: uint32_be(len(tag_utf8)) + tag_utf8
```

长度均为字节数，整数为无符号大端。摘要仅用于数据库级唯一约束，不对用户展示。
唯一约束保证并发写入也不会
产生完全重复项；如果出现摘要相同但原内容不同的理论碰撞，存储层返回安全错误，
不把不同记录误报为重复。

更新采用完整替换语义：调用方提交新的完整正文和完整标签集合。更新保留 ID 和
`created_at`，变更 `updated_at`。如果替换后的内容与另一条已有记录完全重复，则
拒绝更新且不改变原记录。

## 存储接口

`MemoryStore` 提供满足工具和 CLI 用例的具体操作：

- 新增一条记忆；
- 按 ID 获取一条完整记忆；
- 列出全部记忆，可按一个或多个标签过滤；
- 按关键词和标签检索记忆；
- 用完整正文与标签集合替换一条记忆；
- 按 ID 删除一条记忆。

确认写流程使用不可变 prepared 操作：create 包含预分配的规范 UUID 与规范化 draft；
update 包含精确的 `before` 记录和 replacement draft；delete 包含精确的 `before`
记录。prepare 负责存在性与重复预检，commit 在单一写事务内重新加载目标，并以完整
不可变 before 快照执行 compare-and-swap。记录若在确认期间改变或消失，抛出稳定
`MemoryConflictError` 且不修改新状态。直接 CRUD 公共方法继续保留给存储层调用方。

返回值使用不可变的专用记忆记录类型，错误使用存储层定义的可安全展示异常。存储
接口不返回 `ToolResult`，因为它同时服务 CLI 和模型工具，不属于工具协议本身。

调用方提供查询字符串时，字符串去除首尾空白后必须非空且不超过 500 个字符。
查询使用 Unicode `casefold()` 规范化并按空白拆分为关键词；所有关键词都必须
出现在规范化正文或任一标签中。中文等不以空格分词的查询按完整字符串匹配。标签
过滤采用 AND 语义，即记录必须包含调用方提供的全部标签。搜索操作的查询和标签
不能同时为空。

Python 标准库 SQLite 不提供一致的 Unicode `casefold` SQL 函数，因此首版从
SQLite 读取已验证的候选记录，再在存储层使用 Python 执行确定性过滤和排序。该
选择换取跨平台一致行为；首版不为尚未出现的规模问题引入全文索引或分页。

搜索最多返回 20 条，按 `updated_at` 降序、ID 升序稳定排序。列表返回全部匹配
记录，按相同规则排序；首版不提供分页。

## 模型工具接口

新增四个内置工具：

```text
remember_memory(content, tags)
search_memories(query, tags)
update_memory(memory_id, content, tags)
forget_memory(memory_id)
```

`search_memories` 的 `query` 和 `tags` 至少有一个非空，从而也支持仅按标签回忆。
新增、更新和遗忘设置 `requires_confirmation = True`；搜索不需要确认。

工具预检在确认前建立不可变 prepared 操作并完成参数校验、目标存在性和重复检查。
create 此时预分配并验证 UUID。确认文案必须展示将被提交的完整 UUID、正文和规范化
标签；更新同时展示 prepared 的变更前后正文与标签。用户拒绝时沿用现有
`approval_denied` 结果，清除该同步 registry 调用持有的 prepared 状态且存储保持
不变；执行完成后同样清除，不跨调用缓存可变预检状态。

工具把成功记录和错误转换为稳定、结构化的 `ToolResult`。重复项使用
`duplicate_memory`，缺失项使用 `memory_not_found`，确认冲突使用 `memory_conflict`，
无效参数使用 `invalid_arguments`，安全存储失败使用 `memory_store_error`。记忆内容只作为 JSON
工具数据返回，不会被导入为 Python、Skill 或系统指令。

## CLI 接口

新增命令组：

```text
cdy-agent memories add TEXT [--tag TAG ...] [--workspace PATH]
cdy-agent memories list [--tag TAG ...] [--workspace PATH]
cdy-agent memories search QUERY [--tag TAG ...] [--workspace PATH]
cdy-agent memories update MEMORY_ID --content TEXT [--tag TAG ...] [--workspace PATH]
cdy-agent memories delete MEMORY_ID [--workspace PATH]
```

`add`、`update` 和 `delete` 通过与工具相同的 prepared/commit 接口显示即将执行的
完整变更并使用默认 No 的确认。新增在确认前显示预分配 UUID且 commit 使用同一值。
拒绝或中断确认不修改数据并输出 `Aborted.`。确认期间目标变化会显示安全冲突错误，
要求用户重新运行命令。所有命令使用完整 UUID；不接受前缀匹配。

`list` 和 `search` 展示完整 UUID、正文、规范化标签与更新时间。没有匹配结果时
输出清晰的空结果提示。只读命令不创建数据目录、数据库或触发 schema 迁移。

## 数据流

用户明确要求保存时：

1. 模型调用 `remember_memory`，提交准确正文与标签。
2. 工具预检参数、存储可访问性和完全重复项。
3. Registry 将完整的规范化内容展示给用户并请求确认。
4. 用户批准后，`MemoryStore` 在事务中写入记忆和标签。
5. 结构化结果返回模型，由模型向用户说明保存结果。

用户明确要求检索时：

1. 模型根据用户措辞调用 `search_memories`，提交关键词和/或标签。
2. `MemoryStore` 执行确定性的关键词与标签查询。
3. 至多 20 条结构化记录作为工具输出返回模型。
4. 模型根据检索结果回答；系统不会把结果持久追加到长期记忆。

CLI 直接调用相同的 `MemoryStore`。变更命令由 CLI 自己执行默认 No 的确认，不通过
模型工具或 Registry 绕行。

## 一致性与错误处理

- 记忆正文、标签和时间戳的新增或替换位于同一事务。
- 删除记忆及级联删除标签位于同一事务。
- 迁移与 `user_version` 更新位于同一事务。
- schema 初始化决策与结构检查在 `BEGIN IMMEDIATE` 写锁内完成；异常绝不 unlink 数据库。
- prepared 更新和删除的快照比较与变更位于同一事务，冲突时完整回滚。
- SQLite 打开、读取、锁冲突、写入和损坏错误转换为稳定的存储错误，不向 CLI 或
  模型泄漏 SQL 和堆栈。
- 无效 UUID、无效内容、无效标签、无效查询、重复记忆和缺失记忆使用可区分错误。
- 本阶段继续依赖 SQLite 自身的事务和并发锁，不实现跨进程应用锁或自动重试。
- 数据库文件存在但不是正常 SQLite 数据库时拒绝读取或覆盖。

## 测试策略

所有自动测试使用临时 workspace、固定时钟和固定 UUID，不访问真实用户数据、
网络或模型。

数据库基础设施测试覆盖：

- 只读空存储不创建目录或数据库；
- 新 workspace 首次写入直接建立版本 2 schema；
- 版本 1 原子迁移到版本 2 并保留全部会话及消息；
- 两个并发首写者都成功且保留非冲突数据；失败首写留下的 v0 空占位可恢复并可只读为空；
- v1/v2 缺表、错误列/主键、缺外键级联、缺唯一 identity 和缺关键 CHECK 均被拒绝；
- 迁移失败回滚且不留下部分表或错误版本；
- 未知版本、损坏数据库、符号链接越界和非常规文件被拒绝；
- 原有会话保存、恢复、列表和删除行为在重构后不变。

`MemoryStore` 测试覆盖：

- 新增与加载完整记录；
- 正文和标签规范化、大小与数量限制；
- 完全重复新增与重复更新被拒绝；
- 固定 identity 编码向量及标点、换行和 Unicode 边界歧义；
- 两个真实 Store 间 prepared 更新/删除冲突保留较新记录；
- Unicode 大小写无关关键词检索、中文完整字符串、AND 关键词和 AND 标签；
- 搜索上限、更新时间排序与稳定 ID 次序；
- 完整替换保留 ID 与创建时间；
- 删除级联移除标签；
- 写入或标签替换失败时事务回滚。

模型工具测试覆盖：

- 参数 schema、预检与稳定结果结构；
- 新增、更新和遗忘的确认、拒绝及准确确认文案；
- 新增确认 UUID 与最终记录一致，确认期间状态变化返回 `memory_conflict`；
- 搜索无需确认；
- 重复、缺失和存储错误转换；
- 工具描述只允许响应用户的明确记忆请求。

CLI 与回归测试覆盖：

- 五个 `memories` 子命令的正常结果与空结果；
- 所有写命令默认拒绝、用户批准和确认中断；
- workspace 隔离和完整 UUID 校验；
- `ask` 与 `chat` 注册记忆工具；
- 模型未调用检索工具时，记忆内容不会进入模型消息；
- `ask` 继续不保存会话，`chat` 会话持久化行为不变；
- Responses 与 Chat Completions 模式继续使用相同工具协议。

最终验证命令：

```powershell
uv run pytest
uv run cdy-agent --help
uv run cdy-agent memories --help
uv run cdy-agent ask --help
uv run cdy-agent chat --help
uv build
```

## 文档与阶段完成

实现完成后更新 README 的当前阶段、命令示例、数据位置和明确控制规则，并将路线图
中的第 7 阶段标记为完成。文档明确说明长期记忆与会话历史的区别、所有写操作需要
确认、检索不会自动发生，以及记忆仅在当前 workspace 内生效。
