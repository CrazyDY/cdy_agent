# CDY Agent

CDY Agent 是一个本地个人 AI 助理项目，通过渐进式开发学习实用的 Agent 工程。

## 当前阶段

项目支持通过 Responses API 或 Chat Completions API 进行单轮问答和多轮会话，两种 API 模式均可通过同一个 Agent Tool Loop 使用受限的本地工具和流式工具调用。模型还可以从工作区渐进式发现和激活 Skills：激活只返回说明与资源清单，不读取资源内容或运行代码；每一次脚本执行都需要用户单独确认。`chat` 会话和用户显式保存的长期记忆按 workspace 持久化，并提供调用轨迹、Token/费用统计和基于 YAML/JSON 的评估运行器。项目还交付了只监听本机回环地址的 Vue Chat Web UI；它复用相同的 Agent、工具确认、取消和会话持久化边界。

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

`CDY_AGENT_API_MODE` 只接受 `responses` 或 `chat_completions`，默认值为 `responses`。`OPENAI_BASE_URL` 可以指向 OpenAI-compatible 提供商或网关。`--model` 优先于 `CDY_AGENT_MODEL` 和工作区配置；都未设置时使用默认模型 `gpt-5.6-terra`。

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

`OPENAI_API_KEY` 和 `OPENAI_BASE_URL` 不属于工作区配置，仍通过环境变量提供。流式输出的优先级为命令行 `--stream/--no-stream`、`CDY_AGENT_STREAM`、工作区 `stream`、默认关闭；环境变量接受 `1/true/yes/on` 和 `0/false/no/off`。

每个 Agent 回合允许的最大模型调用次数按 `--max-model-calls`、`CDY_AGENT_MAX_MODEL_CALLS`、工作区 `max_model_calls`、默认值 `8` 的优先级解析。该值必须是正整数；`ask`、`chat`、`evals run` 和 `web` 都支持此命令行选项。
可以查看当前 workspace 的有效非敏感配置：

```powershell
uv run cdy-agent config show --workspace .
```

## 使用

发送单轮问题：

```powershell
uv run cdy-agent ask "用一句话介绍你自己"
uv run cdy-agent ask "解释 Agent Loop" --model gpt-5.6-luna
uv run cdy-agent ask "读取 README.md 并总结"
uv run cdy-agent ask "检查仓库状态" --workspace .
uv run cdy-agent ask "流式介绍这个项目" --stream
uv run cdy-agent ask "检查仓库状态" --max-model-calls 12
```

启动多轮会话：

```powershell
uv run cdy-agent chat
uv run cdy-agent chat --model gpt-5.6-luna
uv run cdy-agent chat --workspace .
uv run cdy-agent chat --stream
```

`--stream` 会立即输出模型文本，同时仍保留 Trace、工具调用循环和成功后的会话持久化；`--no-stream` 可以覆盖环境或工作区中启用的流式默认值。在会话中输入 `/exit`、`/quit`，或发送 EOF 即可退出。

```powershell
# 开始并持久化一个新会话
uv run cdy-agent chat --workspace .

# 查看会话，再用完整 ID 恢复或删除
uv run cdy-agent sessions list --workspace .
uv run cdy-agent chat --resume 52c809c6-6e55-4ff1-9220-e4f90a4f6774 --workspace .
uv run cdy-agent sessions delete 52c809c6-6e55-4ff1-9220-e4f90a4f6774 --workspace .
```

### 本地 Chat Web UI

在配置好 provider 环境变量后，启动固定 workspace 的本地界面：

```powershell
uv run cdy-agent web --workspace .
```

从源码树运行时，该命令会在前端生产资产缺失时执行一次构建，再启动服务器；已存在构建产物时默认跳过构建以加快重复启动。因此需先安装 Node.js/npm，并至少执行一次 `npm --prefix frontend install` 安装锁定依赖。构建失败时服务器不会启动。

默认（不强制重建）行为：`src/cdy_agent/web/static/index.html` 已存在则跳过 npm 构建，缺失时仍执行一次首次构建。需要每次启动都重新构建时，用 `--rebuild-frontend` 强制；也可通过环境变量 `CDY_AGENT_REBUILD_FRONTEND=true` 或工作区配置 `rebuild_frontend: true` 开启。配置按四层优先级解析：命令行选项 > 环境变量 > 工作区配置 > 默认值。

```powershell
uv run cdy-agent web --workspace . --rebuild-frontend
```

服务只绑定 `127.0.0.1`，默认端口为 `8000`，且不提供 `--host`。`--port` 可以选择另一个明确端口；`--no-open` 会只在终端打印初始 URL，不自动打开浏览器：

```powershell
uv run cdy-agent web --workspace . --port 8765 --no-open
```

初始地址形如 `http://127.0.0.1:8000/?access_token=<process-local-capability>`。该地址是当前进程的本地访问能力，不应分享或保存。浏览器首次访问后会把它交换为 `HttpOnly`、`SameSite=Strict` Cookie，并立即重定向到不含 token 的干净 `/`；若浏览器留下了旧 Cookie 或打开了旧进程的地址，关闭该页面并使用本次启动新打印的能力 URL。进程退出后旧 token 和 Cookie 不能用于新进程。

workspace 在服务器启动时解析并固定，浏览器不能切换或提交另一个根目录。整个进程同一时间只运行一个 Agent 回合；第二个标签页或客户端不会排队，而会收到 busy 错误。只读会话请求仍可工作，但活动回合期间不能删除会话。

界面支持新建、恢复和删除已保存会话、增量显示助手回复、普通工具状态、Stop 与 Retry。删除会话前浏览器会再次确认。需要确认的工具会显示服务器生成的完整操作说明，并提供 Deny 和 Allow once；只有工具明确支持时才显示 Always allow。Shell 的 Always allow 仍只保存已经准备好的完整可执行文件和精确 argv，参数内容与顺序必须完全匹配，不会扩大成前缀或通配符。

Stop、刷新、关闭页面或 WebSocket 断开都会协作取消模型流、待确认操作和可取消的子进程；服务器会等 worker 确实停止后才释放活动回合。失败或取消的回合只保留在当前页面供 Retry，不写入 SQLite，也不进入后续模型上下文。取消不会回滚此前已经完成的文件、进程、记忆或其他副作用。

常见启动问题：

- 缺少 `OPENAI_API_KEY` 或 provider 配置无效：先在当前终端设置环境变量，再重新启动；凭证不会进入浏览器或 workspace 配置。
- 端口已占用：使用 `--port <空闲端口>`；服务不会自动改绑其他地址或端口。
- 提示缺少 npm 或前端构建失败：确认 Node.js/npm 可用，并执行 `npm --prefix frontend install` 后重试。
- 显示 `Web assets are unavailable`：当前安装包不含有效的预构建资产；从源码树安装依赖后重新运行 `cdy-agent web`。
- 显示 busy：等待当前标签页的回合结束，或在发起回合的页面点击 Stop；请求不会在后台排队。

### 持久化会话

`chat` 只在模型成功回复后保存完整的用户/助手轮次。直接退出、模型失败或保存失败不会留下空会话或半个轮次；保存失败的助手回复不会显示。

会话数据库位于 `<workspace>/.cdy-agent/cdy-agent.sqlite3`。`sessions list` 不会为了空结果创建数据库。恢复和删除必须使用完整会话 ID，删除操作默认拒绝并需要用户确认。

`ask` 仍然是无状态命令。会话首版不提供自动恢复、重命名、搜索、导出、分页或摘要。

### 显式长期记忆

持久化会话保存 `chat` 的完整对话轮次，用于以后显式恢复上下文；长期记忆则是用户明确要求保存、检索、修改或遗忘的独立信息。两者均限定在指定 workspace，保存在该 workspace 的 `<workspace>/.cdy-agent/cdy-agent.sqlite3` 中，不会跨 workspace 共享。

可以直接管理长期记忆：

```powershell
uv run cdy-agent memories add "Python 项目统一使用 uv 管理依赖" --tag python --tag tooling --workspace .
uv run cdy-agent memories list --workspace .
uv run cdy-agent memories search "uv" --tag python --workspace .
uv run cdy-agent memories update <memory-id> --content "Python 项目统一使用 uv sync 管理依赖" --tag python --tag tooling --workspace .
uv run cdy-agent memories delete <memory-id> --workspace .
```

`add`、`update` 和 `delete` 都会先展示变更并请求确认，默认答案为 No。新增确认会展示预先分配的完整 UUID，最终写入使用同一 UUID；`update` 和 `delete` 的 `<memory-id>` 必须是完整 UUID，不接受缩写。如果记录在确认期间被其他进程修改或删除，操作会安全失败，用户必须重新运行命令查看并确认新状态。

`search` 对关键词和显式标签过滤都使用 AND 语义。每个关键词可以出现在规范化正文或任一标签中，但所有关键词都必须各自命中；记录还必须同时具有全部显式 `--tag` 标签。

`ask` 和 `chat` 只会在用户明确要求检索长期记忆后调用记忆检索工具。系统不会从对话中自动提取记忆，也不会把已保存记忆自动注入提示或上下文。

### 调用轨迹与费用统计

可以为本次 CLI 选择的提供商和模型配置每百万 Token 的输入、输出单价，并查询按 workspace 保存的调用轨迹：

```powershell
$env:CDY_AGENT_INPUT_COST_PER_MILLION = "1.25"
$env:CDY_AGENT_OUTPUT_COST_PER_MILLION = "2.50"
$env:CDY_AGENT_LOG_LEVEL = "INFO"

uv run cdy-agent traces list --workspace .
uv run cdy-agent traces show <trace-id> --workspace .
```

两个价格变量都是可选项；一旦配置，就必须成对设置，且都必须是非负十进制数。它们也可以写入工作区配置文件的 `observability` 区块。`CDY_AGENT_LOG_LEVEL` 只接受 `DEBUG`、`INFO`、`WARNING`、`ERROR`，默认值为 `WARNING`；单行 JSON 日志写入 stderr。

每次实际执行 `ask` 都会创建一条轨迹；`chat` 中每个非空且不是退出命令的用户回合都会创建一条轨迹，并关联当前会话。空输入、`/exit`、`/quit` 和 EOF 不会创建轨迹。

如果提供商未返回 usage，Token 用量和估算费用在查询中显示为 `unknown`，轨迹 JSON 中对应值为 `null`。如果提供商返回了 usage 但未配置价格，Token 用量仍然可用，估算费用显示为 `unknown`（JSON 中为 `null`）。

轨迹文件位于 `<workspace>/.cdy-agent/traces.jsonl`。轨迹和日志均排除用户 prompt、模型回复正文以及工具参数、确认内容和返回载荷，不会保存这些敏感内容。

轨迹初始化、完成或写入失败时，CLI 只向 stderr 输出通用警告，不会替换主要回复或原始错误。

### 本地工具与安全边界

`ask` 和 `chat` 都向模型提供以下工具：

- `read_file`：读取工作区内的 UTF-8 常规文件；单次最多返回 1 MiB，超出时明确标记截断。
- `write_file`：在工作区内创建或写入 UTF-8 文件；不会创建缺失的父目录，覆盖已有文件时必须显式传入 `overwrite=true`。
- `shell`：以参数数组在工作区内运行受限命令，不通过 Shell 解释命令字符串。
- `create_note`、`list_notes`、`get_note`、`delete_note`：创建、列出、查看和删除 workspace 笔记。
- `create_todo`、`list_todos`、`complete_todo`、`delete_todo`：创建、列出、完成和删除 workspace Todo。
- `remember_memory`、`search_memories`、`update_memory`、`forget_memory`：在用户明确要求时新增、检索、完整替换和遗忘 workspace 长期记忆。

工作区默认为命令启动时解析后的当前目录，也可通过 `--workspace` 指定。文件工具会解析真实路径（包括符号链接）并拒绝访问工作区之外的路径。

每次 `write_file` 调用都会显示操作说明并请求确认，默认答案为 No；`read_file` 不需要确认。记忆工具中 `remember_memory`、`update_memory` 和 `forget_memory` 需要默认 No 的确认，`search_memories` 不需要确认；所有四个工具都只允许响应用户的明确记忆请求。

### Shell 命令审批

Shell 工具使用参数数组和 `shell=False`，并固定在当前 workspace 中运行。Shell 超时可设为 1–30 秒（默认 10 秒），标准输出和标准错误分别最多返回 64 KiB。

带有安全参数且只访问 workspace 内文件的 `pwd`、`ls`、`rg`、`grep`、`head`、`tail`、`wc`、`sort`、`uniq`、`git status` 和 `git diff` 可以自动执行。未知参数、写入参数、外部程序委托、可能读取标准输入、访问 `.cdy-agent` 机器状态或 workspace 外路径的调用会请求确认。Shell 工具没有 stdin 参数，启动进程时会把标准输入连接到空设备，避免继承终端输入或管道并发生等待。

自动批准会保守地绑定到已解析的可执行文件：只有名称精确匹配、解析到操作系统受信任命令目录、具有本机可执行文件格式的内置安全命令才会自动执行。workspace 内 wrapper、workspace 外任意 PATH wrapper、相对 PATH wrapper 和脚本 wrapper 都会请求确认。一次工具调用只解析一次可执行文件，并把提示、持久授权和实际启动绑定到同一个绝对 argv；无法解析的程序在确认后也会失败关闭，不会在启动时重新查询 PATH。

`git status` 和 `git diff` 还会清除继承的 `GIT_*` 仓库、对象、配置和命名空间覆盖，并验证 Git 实际报告的 git-dir、common-dir 和 worktree 都在 workspace 内。Git 元数据位于 workspace 外、无法验证、使用外部链接工作区元数据，或 workspace 已包含 `.cdy-agent` 机器状态目录时，会降级为请求确认。

确认时输入 `y` 仅允许本次执行；输入 `a` 会把最终实际执行的完整 argv 保存到 `<workspace>/.cdy-agent/shell-approvals.json`，以后在同一 workspace 中精确匹配时不再询问。匹配区分大小写、参数顺序和参数内容，不支持前缀或通配符。编辑或删除该 JSON 文件即可撤销授权。版本 1 的文件结构为：

```json
{
  "version": 1,
  "allowed_commands": [
    ["/usr/bin/python3", "script.py"],
    ["/usr/bin/uv", "run", "pytest"]
  ]
}
```

`allowed_commands` 中的每一项都是非空字符串 argv 数组。未知字段、未知版本、重复 JSON key 或其他 schema 错误都会失败关闭；损坏的文件不会产生任何自动授权。

解释器、脚本和路径程序以当前用户权限运行。选择永久允许意味着信任相同 argv 的后续执行，即使对应脚本或程序内容后来发生变化。审批文件已由 Git 忽略，工具参数不会写入结构化日志或 trace。

审批文件写入会拒绝符号链接和 Windows 重解析点，并在原子替换前后复验目录与文件身份。这里仍保留一个已知的本机并发攻击残余风险：如果另一个具有同等文件系统权限的进程恰好在最后一次复验与路径替换之间调换 `.cdy-agent` 的祖先目录，跨平台路径 API 无法原子地阻止一次可能写向 workspace 外的操作。替换后的复验会检测变化并失败关闭，不会接受或使用该授权，但无法撤销已经发生的外部写入。需要抵御这种本机恶意并发修改的部署，应通过操作系统权限阻止其他进程修改 workspace 及其祖先目录。

### 笔记与 Todo 数据

笔记保存在 `<workspace>/.cdy-agent/notes.json`，Todo 保存在 `<workspace>/.cdy-agent/todos.json`。创建、完成和删除操作每次都需要默认 No 的用户确认；列表和查看不会请求确认，也不会为了空列表创建数据目录。

数据文件使用严格校验的版本化 JSON 和原子替换写入。格式损坏、版本未知或路径越过 workspace 时，工具会拒绝操作，不会用空数据覆盖原文件。同一 workspace 首版只允许一个 `cdy-agent` 进程执行修改。

### 工作区 Skills

只扫描 `<workspace>/.cdy-agent/skills/`；每个 Skill 使用名称相同的目录，名称只能由小写字母、数字和单个连字符组成。Skill 必须包含采用标准 frontmatter 的 `SKILL.md`，并且仅会递归识别 `scripts/`、`references/` 和 `assets/` 中的资源：

```text
<workspace>/.cdy-agent/skills/pdf-processing/
├── SKILL.md
├── scripts/
│   └── extract.py
├── references/
│   └── formats.md
└── assets/
    └── report-template.docx
```

```markdown
---
name: pdf-processing
description: Extract text and tables from PDF files. Use for PDF extraction and document-processing tasks.
license: Apache-2.0
compatibility: Requires an installed Python runtime
metadata:
  author: example-org
  version: "1.0"
allowed-tools: Read
---

# PDF processing

Read `references/formats.md` when format details are needed.
Run `python scripts/extract.py --help` before the first extraction.
```

`SKILL.md` 使用以下严格校验：

| 字段 | 必需性 | 校验规则 |
| --- | --- | --- |
| `name` | 必需 | 1–64 个字符；只允许小写 ASCII 字母、数字和单个连字符；不得以连字符开头或结尾；必须与目录名完全一致 |
| `description` | 必需 | 非空字符串，最多 1024 个字符 |
| `license` | 可选 | 非空字符串 |
| `compatibility` | 可选 | 非空字符串，最多 500 个字符 |
| `metadata` | 可选 | 键和值均为字符串的映射 |
| `allowed-tools` | 可选 | 非空 token 字符串；token 之间只能使用一个 ASCII 空格；仅用于披露，不改变确认规则 |

Markdown 正文也必须非空，未知字段和重复 YAML 键会使 Skill 无效。`SKILL.md` 最大为 256 KiB，每个 Skill 最多包含 512 个已识别资源文件；标准建议将 `SKILL.md` 保持在 500 行以内，但该建议不作为有效性校验。

`list_skills` 和 `search_skills` 只返回目录元数据；首次 `activate_skill` 会重新校验 Skill，然后返回完整说明、元数据和资源清单，但不会读取资源内容或运行代码。重复激活会立即返回稳定的 `already_active` 载荷，不会再次校验。激活后，可用 `read_skill_resource` 按需读取 UTF-8 文本 reference 或 asset；二进制资源只返回其路径和大小等元数据。

`run_skill_script` 只能运行已激活 Skill 的 `scripts/` 清单中恰好一个脚本。每一次运行都需要单独确认，即使 frontmatter 中声明了 `allowed-tools`；该字段只用于披露，绝不会绕过确认。确认信息会展示最终 argv、Skill 目录和当前用户权限。命令以参数数组执行，不经过 shell 解释（`shell=False`），可使用任意已安装的运行时；系统不会安装依赖，也不提供脚本沙箱。脚本超时必须为 1–300 秒（默认 30 秒），stdout 和 stderr 分别最多返回 64 KiB，并标记截断。

资源在发现时记录文件状态身份；读取资源或准备脚本时会逐级重新校验路径组件并拒绝符号链接和 Windows reparse point。脚本确认时还会暂存仅绑定本次同步调用的内容摘要，执行前在重新校验路径后比较摘要；该摘要不会返回给模型或持久化，并会在拒绝、完成或失败后清除。因此可以检测资源被重写、替换或经祖先链接重定向。此校验缩小了确认与使用之间的风险窗口，但不能消除操作系统层面的最终 check/use 竞争。

根目录中的额外条目不会成为资源；尤其 `tools.py` 和 `create_tools(workspace)` 均不受支持、会被忽略，且绝不会执行。

### 评估用例

`evals run` 从 YAML 或 JSON 文件逐条运行单轮提示，并使用本地、确定性的 `exact` 和 `contains` 断言检查最终回复。仓库提供一个基础用例集：

```powershell
uv run cdy-agent evals run evals/smoke.yaml --workspace .
uv run cdy-agent evals run evals/smoke.yaml --workspace . --model gpt-5.6-luna
```

用例文件格式如下：

```yaml
cases:
  - name: exact reply
    prompt: "Reply with exactly: CDY_EVAL_OK"
    expect:
      exact: CDY_EVAL_OK
  - name: required concepts
    prompt: "Include the literal terms Agent Loop and tool calling in one sentence."
    expect:
      contains:
        - Agent Loop
        - tool calling
```

每个用例必须提供非空的 `name`、`prompt` 和 `expect`；`expect` 至少包含 `exact` 或 `contains`，两者同时提供时必须全部满足。命令逐项输出 `PASS`/`FAIL` 和汇总，只要有一项失败就以退出码 1 结束。

断言和用例加载完全在本地完成，但提示仍由所选 Agent 执行，因此通常需要有效的 provider 配置并可能产生网络请求和费用。自动化测试通过注入假 Agent 保持离线，不使用真实 API Key。

## 开发

非 Web 命令只需要 Python 3.10+ 和 [uv](https://docs.astral.sh/uv/)。从源码运行 Web UI 还需要 Node.js/npm；前端使用 `frontend/package-lock.json` 锁定依赖。生产构建写入 `src/cdy_agent/web/static/`，该目录是被 Git 忽略的生成物，不提交 source map 或其他构建产物。

```powershell
uv sync --extra dev --default-index https://pypi.org/simple
uv run pytest -p no:cacheprovider
uv run cdy-agent --help
uv run cdy-agent ask --help
uv run cdy-agent chat --help
uv run cdy-agent web --help
uv build
```

首次进行前端开发时安装锁定依赖，然后启动 Vite 开发服务器：

```powershell
npm --prefix frontend install
npm --prefix frontend run dev
```

Vite 开发服务器用于独立迭代界面；真实的认证 HTTP/WebSocket API 由 `cdy-agent web` 与生产静态资产从同一 origin 提供。`cdy-agent web` 在前端生产资产缺失时执行一次生产构建；已存在构建产物时默认跳过，需要时用 `--rebuild-frontend` 强制重建。交付前仍应运行完整前端测试；发布 wheel 前应显式生成一次用于打包的生产资产：

```powershell
npm --prefix frontend test
npm --prefix frontend run build
```

构建会先执行 `vue-tsc`，再清空并写入 `src/cdy_agent/web/static/`。哈希化的 `index.html`、CSS 和 JavaScript 都是本地生成物，不由 Git 管理，也不进入 wheel 或 sdist。wheel 会在 `cdy_agent/frontend/` 中携带前端源码、锁文件和构建配置，但不包含 `node_modules`；从 wheel 安装后首次启动 Web 服务会先执行 `npm ci` 安装锁定依赖，再把生产资源构建到已安装包的 `cdy_agent/web/static/`。发布流程运行前端构建作为质量检查，并在执行 `uv build` 前删除生成目录。

## 发布

`.github/workflows/ci.yml` 在 `main` 推送和 Pull Request 上运行 Python 3.10–3.14 测试以及前端测试和生产构建。`.github/workflows/release.yml` 在推送 `v*` 标签时验证标签与 `pyproject.toml` 版本一致，重新运行测试和前端生产构建，删除生成的静态目录，再构建并冒烟测试 wheel 和 sdist。工作流会验证分发包不包含生成的前端资源、wheel 和 sdist 均包含前端源码；验证通过后，通过 PyPI Trusted Publishing 发布 `cdy-agent`，随后创建带有两个分发文件的 GitHub Release。

首次发布前，在 GitHub 仓库中创建名为 `pypi` 的 Environment，并在 PyPI 项目的 Publishing 设置中添加 GitHub Trusted Publisher：Owner 为 `CrazyDY`，Repository 为 `cdy_agent`，Workflow 为 `release.yml`，Environment 为 `pypi`。该流程使用 OIDC 短期凭证，不需要保存 `PYPI_TOKEN`。

发布新版本时，先更新并提交项目版本，然后创建同版本标签：

```powershell
uv version 0.1.0
git add pyproject.toml uv.lock
git commit -m "Prepare release 0.1.0"
git tag -a v0.1.0 -m "Release 0.1.0"
git push origin main
git push origin v0.1.0
```

不要在版本提交进入 `main` 前推送标签；标签中的 `v` 会被发布工作流去除后与项目版本比较。

如果 PyPI 已发布成功、但 GitHub Release 创建失败，可以从 `main` 手动补建现有标签的 Release，而不会再次上传 PyPI：

```powershell
gh workflow run release.yml --ref main -f tag=v0.1.0
```
