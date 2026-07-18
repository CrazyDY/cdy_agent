# 工作区 Skills 系统设计

## 背景

CDY Agent 已经具备双 API 模式的 Agent Tool Loop，以及文件、Shell、笔记和 Todo 工具。当前所有工具都由 `create_builtin_registry()` 固定注册，模型无法从工作区发现新的操作说明或专用工具。

阶段 6 将加入首版 Skills 系统。Skill 是一个由工作区所有者提供的扩展单元，包含模型可读的操作说明，以及可选的 Python 函数工具。模型先看到 Skill 的名称和摘要，再按任务需要激活具体 Skill，避免启动时把所有说明和工具放入上下文。

## 目标

- 从 `<workspace>/.cdy-agent/skills/` 发现工作区自定义 Skills。
- 支持只含说明的 Skill，以及同时包含说明和 Python 工具的 Skill。
- 发现阶段不执行工作区 Python；模型按需请求激活 Skill。
- 在当前进程首次加载某个 Skill 的 Python 代码前取得用户明确授权。
- 激活后把完整 Skill 说明返回给模型，并在下一次模型调用中暴露新增工具。
- 保持 CLI、Agent、模型 SDK 边界和普通函数工具的现有职责清晰。
- 让单个无效或冲突的 Skill 不影响内置工具和其他有效 Skills。

## 非目标

首版不实现：

- 包内置 Skills 或工作区与包内 Skill 的覆盖规则。
- Python 代码沙箱或独立子进程隔离。
- Skill 依赖声明、第三方包自动安装或虚拟环境管理。
- Skill 内的 Python 包、相对导入或多个源码文件。
- Skill 之间的依赖关系。
- 热重载、进程内卸载或重新激活已修改代码。
- 持久信任清单。
- 持久化会话、长期记忆、MCP 或通用插件协议。

## Skill 与普通工具的区别

普通函数工具是一个可由模型直接调用的、具有 JSON 参数结构和执行结果的操作。Skill 是更高层的扩展单元：它提供完成某类任务所需的完整说明，并且可以在激活时贡献零个或多个普通函数工具。

Skill 的发现和激活由 Skills 系统负责；激活后的函数工具仍由现有 `ToolRegistry` 校验、确认和执行。Skill 说明不能绕过工具自身的参数校验、安全边界或确认策略。

## 目录与文件格式

每个 Skill 使用独立目录：

```text
<workspace>/.cdy-agent/skills/
└── research/
    ├── SKILL.md
    └── tools.py       # 可选
```

`SKILL.md` 使用受限的 YAML 风格头部和非空 Markdown 正文：

```markdown
---
name: research
description: Search and summarize local project information.
---

# Research

激活后提供给模型的完整操作说明。
```

项目使用自身的严格解析器读取头部，不引入 YAML 依赖。头部只接受必需的 `name` 和 `description` 两个单行字符串；未知字段、重复字段、空值或多行值均无效。

Skill 目录名必须与 `name` 完全一致。名称只接受小写 ASCII 字母、数字和下划线，必须以字母开头，长度为 1–64 个字符。`description` 去除首尾空白后必须非空，最长 500 个字符。`SKILL.md` 最大为 256 KiB，正文去除首尾空白后必须非空。

Skills 根目录、Skill 目录、`SKILL.md` 和可选的 `tools.py` 都不得是符号链接，并且解析后的真实路径必须位于 workspace 内。扫描只处理 Skills 根目录的直接子目录，不递归寻找嵌套 Skill。`tools.py` 必须是常规文件且最大为 1 MiB；Manager 在用户授权后、实际导入前再次检查它的类型、大小、符号链接状态和路径边界。

## Python 工具契约

可选的 `tools.py` 必须暴露工厂函数：

```python
from collections.abc import Iterable
from pathlib import Path

from cdy_agent.tools.base import Tool


def create_tools(workspace: Path) -> Iterable[Tool]:
    ...
```

模块通过包含 Skill 名和进程内唯一标识的内部模块名加载，避免不同 Skill 之间发生普通模块名冲突。加载器把当前解析后的 workspace 传给 `create_tools()`；Skill 工具自行使用这个路径建立所需的文件边界。首版只加载单个 `tools.py`，不把 Skill 目录加入 `sys.path`，也不支持相对导入同目录的辅助模块。

工厂返回的每个对象必须满足现有 `Tool` 协议所需的数据与方法：非空工具名和描述、有效的参数对象、布尔型 `requires_confirmation`，以及可调用的 `preflight()`、`confirmation_description()` 和 `execute()`。工具名称遵循与现有函数工具兼容的标识符约束。

Manager 在修改 Registry 前完整物化并验证工厂结果。一个 Skill 内不得出现重复工具名；工具名不得与内置工具、`list_skills`、`activate_skill` 或已激活 Skill 的工具重名。Registry 提供原子的批量注册操作：全部工具通过验证且没有冲突时一次性加入，否则 Registry 保持不变。

## 架构与职责

新增 `src/cdy_agent/skills/` 包：

- `models.py` 定义不可变的 Skill 元数据、已发现 Skill、发现诊断和激活结果。
- `loader.py` 扫描 Skills 根目录，验证路径与文件格式，读取元数据和正文，但绝不导入 `tools.py`。
- `manager.py` 保存本进程已发现与已激活状态，执行授权后的动态加载，并协调工具的原子注册。
- `tools.py` 实现模型可调用的 `list_skills` 和 `activate_skill` 管理工具。
- `__init__.py` 暴露构建工作区 Skills 系统所需的最小公共入口。

现有组件调整如下：

- `ToolRegistry` 增加受控的原子批量注册能力，仍只负责工具定义和调用分发，不扫描文件或导入代码。
- `create_builtin_registry()` 仍构建固定的安全内置工具。CLI 在此基础上创建 Skill Manager，并把两个 Skill 管理工具加入同一个 Registry。
- CLI 负责提供用户确认回调和展示面向用户的错误，不承担发现、导入或注册逻辑。
- Agent 和 `ModelGateway` 的接口及循环保持不变。Agent 在每次模型调用时读取当前 `registry.definitions`，所以一次激活产生的新工具会自然出现在下一次模型调用中。

## 发现与激活数据流

启动 `ask` 或 `chat` 时：

1. CLI 解析 workspace 并创建内置 Registry。
2. Loader 扫描 `<workspace>/.cdy-agent/skills/`，只读取 Skill 文件。
3. Manager 保存有效 Skills 和无效条目的诊断。
4. Registry 注册 `list_skills` 和 `activate_skill`。
5. Skills 目录不存在时返回空集合，不创建目录或文件。

模型处理任务时：

1. 模型可调用 `list_skills()` 查看名称、摘要、是否含 Python 工具和激活状态。
2. 模型调用 `activate_skill(name)` 请求具体 Skill。
3. 对仅含说明的 Skill，Manager 直接标记激活并返回完整 Markdown 正文。
4. 对含 `tools.py` 的 Skill，Manager 先请求用户授权。
5. 用户批准后，Manager 导入模块、调用工厂、验证全部工具并原子注册。
6. 激活结果返回完整 Markdown 正文和新增工具名称。
7. Agent 把结果交给模型；下一次模型调用同时包含新增工具定义。

已成功激活的 Skill 再次激活时，不重复读取或导入代码、不重复注册工具，也不再次请求授权；它返回 `already_active` 状态、完整说明和已注册工具名称。用户修改 Skill 后必须重启进程才能加载新版本。

## 模型管理工具

### `list_skills`

`list_skills()` 不接受参数，返回：

- 有效 Skill 的 `name`、`description`、`has_tools` 和 `active`。
- 无效直接子目录的稳定标识与简短诊断。

有效 Skills 与诊断都按目录名排序，使模型输入和测试结果保持确定性。列表操作不执行 Python，也不请求用户确认。

### `activate_skill`

`activate_skill(name)` 只接受一个 Skill 名称。成功结果包含：

- Skill 名称。
- `activated` 或 `already_active` 状态。
- `SKILL.md` 的完整 Markdown 正文。
- 本次激活后可用的 Skill 工具名称列表。

激活工具自身不使用 Registry 的静态 `requires_confirmation`，因为说明型 Skill 不需要授权。它通过 Manager 持有的确认回调，仅在将要加载现存 `tools.py` 时提出代码执行授权。

## 信任与安全边界

Skill Python 与 CDY Agent 在同一进程中运行，拥有当前用户权限。首版授权是明确的信任边界，而不是权限隔离机制。

授权提示显示 Skill 名称、`tools.py` 的绝对路径，并明确说明代码会以当前用户权限在主进程执行。默认答案为 No；EOF、中断或 CLI Abort 均视为拒绝。授权只对当前进程中该 Skill 的本次成功加载有效，程序重启后需要重新授权。

发现阶段禁止导入工作区 Python。用户拒绝授权时，Skill 保持未激活且 Registry 不变。加载或验证失败也不建立信任缓存；后续再次请求激活时会再次授权。成功激活后不重新导入代码。

Skill 说明属于工作区提供的模型指令，只在具体 Skill 被激活后作为工具结果进入模型上下文。说明不能关闭路径检查、Shell 允许列表、确认回调或其他程序级控制。

## 错误处理

发现采用条目隔离：一个无效 Skill 产生一条诊断，但不阻止其他有效 Skill 被列出。无法读取整个 Skills 根目录时，Manager 仍可创建，但 `list_skills` 返回根目录级诊断且没有工作区 Skills。

激活错误使用结构化 `ToolResult`，主要错误码为：

- `unknown_skill`：名称不存在于有效 Skill 集合。
- `invalid_skill`：目标对应无效 Skill，或从发现到激活之间文件状态已失效。
- `approval_denied`：用户拒绝运行 Python。
- `load_failed`：Python 语法、导入或工厂执行失败。
- `invalid_tools`：工厂缺失、不可调用、返回值无效或工具对象不满足契约。
- `tool_name_conflict`：工具名与 Registry 中现有名称冲突。

异常不会越过管理工具退出 Agent Loop。面向模型和 CLI 的消息包含 Skill 名、错误类别及必要的文件定位信息，但不包含 traceback、环境变量、文件正文或任意异常对象的完整表示。内部导入失败时移除本次创建的临时模块条目，Registry 保持原状。

## 测试策略

所有自动测试使用临时 workspace 和伪造对象，不访问真实 API、用户文件或网络。

- `tests/test_skill_loader.py` 覆盖空目录、确定性发现、严格元数据解析、名称与大小限制、符号链接、路径边界和坏 Skill 隔离。
- `tests/test_skill_manager.py` 覆盖说明型激活、代码授权与拒绝、成功后只授权一次、动态导入、工厂失败、工具验证、冲突和原子注册。
- `tests/test_skill_tools.py` 覆盖两个管理工具的参数预检、定义与结构化结果。
- `tests/test_tool_registry.py` 增加原子批量注册成功和冲突不产生部分写入的测试。
- `tests/test_agent.py` 验证 Skill 激活后，下一次模型调用收到新增工具定义。
- `tests/test_cli.py` 验证 workspace 传递、代码执行授权提示和错误展示。

两种 API 模式共享同一个 Registry 和 Agent Loop，因此动态工具的核心测试放在 Agent 层；OpenAI 客户端已有的 Responses 与 Chat Completions 工具定义转换测试继续保证协议兼容。

## 文档与验收

README 将说明：

- 工作区 Skill 目录和 `SKILL.md` 示例。
- 可选 `tools.py` 的工厂契约。
- 模型按需发现与激活流程。
- Python 代码授权、同进程权限和非沙箱性质。
- 首版不支持依赖安装、热重载和持久信任。

阶段 6 完成时必须满足：

1. 模型能够列出 Skills，并按需激活说明型或带工具的 Skill。
2. 发现过程不会执行工作区 Python。
3. 未经授权的 Skill Python 绝不导入，拒绝后 Registry 不变。
4. 激活后的工具会在下一次模型调用中出现，并同时适用于 Responses 和 Chat Completions 模式。
5. 损坏、加载失败或工具冲突的 Skill 不影响内置工具和其他 Skills。
6. Skills 目录不存在时 CLI 正常工作且不产生文件。
7. `uv run pytest`、`uv run cdy-agent --help`、`uv run cdy-agent ask --help`、`uv run cdy-agent chat --help` 和 `uv build` 全部通过。
