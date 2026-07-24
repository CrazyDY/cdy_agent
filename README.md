# CDY Agent

CDY Agent 是一个本地个人 AI 助理项目，通过渐进式开发学习实用的 Agent 工程。

## 当前阶段

项目支持通过 Responses API 或 Chat Completions API 进行单轮问答和多轮会话，两种 API 模式均可通过同一个 Agent Tool Loop 使用受限的本地工具。模型还可以从工作区渐进式发现和激活 Skills：激活只返回说明与资源清单，不读取资源内容或运行代码；每一次脚本执行都需要用户单独确认。`chat` 会话和用户显式保存的长期记忆现在都按 workspace 持久化。

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
log_level: INFO
observability:
  input_cost_per_million: "1.25"
  output_cost_per_million: "2.50"
```

`OPENAI_API_KEY` 和 `OPENAI_BASE_URL` 不属于工作区配置，仍通过环境变量提供。
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
```

启动多轮会话：

```powershell
uv run cdy-agent chat
uv run cdy-agent chat --model gpt-5.6-luna
uv run cdy-agent chat --workspace .
```

在会话中输入 `/exit`、`/quit`，或发送 EOF 即可退出。

```powershell
# 开始并持久化一个新会话
uv run cdy-agent chat --workspace .

# 查看会话，再用完整 ID 恢复或删除
uv run cdy-agent sessions list --workspace .
uv run cdy-agent chat --resume 52c809c6-6e55-4ff1-9220-e4f90a4f6774 --workspace .
uv run cdy-agent sessions delete 52c809c6-6e55-4ff1-9220-e4f90a4f6774 --workspace .
```

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

每次 `write_file` 和 `shell` 调用都会显示操作说明并请求确认，默认答案为 No；`read_file` 不需要确认。记忆工具中 `remember_memory`、`update_memory` 和 `forget_memory` 需要默认 No 的确认，`search_memories` 不需要确认；所有四个工具都只允许响应用户的明确记忆请求。Shell 超时可设为 1–30 秒（默认 10 秒），标准输出和标准错误分别最多返回 64 KiB。Shell 只允许 `pwd`、`ls`、`find`、`rg`、`grep`、`sed`、`head`、`tail`、`wc`、`sort`、`uniq`，以及 `git status` 和 `git diff`。

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
