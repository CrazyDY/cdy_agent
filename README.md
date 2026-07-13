# cdy_agent

CAN DEAL YOUR EVERYTHING — 一个从 0 开始搭建的事务处理 AI Agent MVP。

## 当前版本：V0.1

本仓库按照最小产品路线图先实现 **单 Agent + Skill Registry + Todo/Notes/Filesystem/Shell Skills**：

- 基于 OpenAI Agents SDK 创建主 Agent。
- 使用显式的手动 Skill Registry 管理能力包。
- 内置 Todo Skill：添加、查看、完成待办事项。
- 内置 Notes Skill：创建、查看、搜索笔记。
- 内置 Filesystem Skill：在工作区内列出、读取、写入文本文件。
- 内置 Shell Skill：在工作区内执行 bash 命令并返回 stdout/stderr/exit code。
- 提供 CLI 入口，方便用自然语言运行一次 Agent 调用。

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

设置 OpenAI API Key：

```bash
export OPENAI_API_KEY="你的 API Key"
```

## 使用

```bash
cdy-agent "帮我添加一个待办：周五前完成项目计划"
```

也可以直接运行模块：

```bash
python -m cdy_agent.cli "记录一条笔记：Agent MVP 先做 todo 和 notes skill"
```

## 项目结构

```text
src/cdy_agent/
  agent.py              # 主 Agent 工厂
  cli.py                # CLI 入口
  models.py             # Skill 数据模型
  skill_registry.py     # V0.1 手动 Skill Registry
  skills/
    todo/               # Todo Skill
    notes/              # Notes Skill
    filesystem/         # Filesystem Skill
    shell/              # Shell Skill
```

## 后续路线图

- V0.2：自动扫描 skills、SQLite 持久化、会话历史。
- V0.3：Reminder Skill、审批机制、tool call 日志。
- V0.4：Calendar Skill、Email draft Skill、用户偏好记忆。
- V0.5：多 Agent、handoff、权限系统、Web UI 或 CLI 增强。
