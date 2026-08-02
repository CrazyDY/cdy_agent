---
name: commit-and-push-rebuild-frontend
overview: 将 `--rebuild-frontend` 功能改动提交并推送到远端 main 分支。先还原被 aliyun 镜像污染的 uv.lock，再用官方索引重新生成，最后与 rebuild-frontend 相关的源码、测试、文档一起提交并推送。
todos:
  - id: regen-uvlock
    content: 还原 uv.lock 镜像污染并用官方索引重新生成，验证 diff 行数小
    status: completed
  - id: stage-files
    content: 显式 git add 8 个目标文件并确认暂存内容不含 planning 等无关文件
    status: completed
    dependencies:
      - regen-uvlock
  - id: commit
    content: 用 imperative summary 风格 commit message 提交
    status: completed
    dependencies:
      - stage-files
  - id: push-verify
    content: 推送到 origin/main 并验证提交内容与工作区状态
    status: completed
    dependencies:
      - commit
---

## 任务说明

用户要求将当前工作区的修改提交到 git 仓库并推送到远端（origin/main）。

## 提交范围（单 commit，rebuild-frontend 主题）

只提交以下 8 个文件：

- `src/cdy_agent/cli.py`：新增 `--rebuild-frontend/--no-rebuild-frontend` 选项；`_build_web_frontend(*, rebuild=False)` 在产物已存在且 rebuild=False 时跳过构建；调用处改用 `resolve_rebuild_frontend` 解析。
- `src/cdy_agent/config.py`：新增 `WorkspaceConfig.rebuild_frontend` 字段、`resolve_rebuild_frontend()` 函数（CLI override > env > workspace config > 默认 False），白名单加 `rebuild_frontend`。
- `tests/test_cli.py`：补 `_WEB_STATIC_DIRECTORY` monkeypatch、build 函数签名兼容 `**kwargs`、新增 rebuild 行为用例。
- `tests/test_config.py`：新增 rebuild_frontend 解析、env 规范化、优先级、非法值拒绝、workspace 配置校验用例。
- `AGENTS.md`：workspace 字段白名单加 `rebuild_frontend`；环境变量表加 `CDY_AGENT_REBUILD_FRONTEND`。
- `README.md`：web 命令章节说明默认跳过构建、新增 `--rebuild-frontend` 用法与四层优先级说明。
- `pyproject.toml`：version 0.1.1 → 0.1.2。
- `uv.lock`：先用官方索引重新生成后纳入。

## 不提交的文件

- 独立功能文件（本次主题无关）：`src/cdy_agent/planning.py`、`tests/test_planning.py`、`frontend/src/components/ChatComposer.test.ts`
- 本地工具/IDE/临时文件：`.claude/`、`.codebuddy/`、`.codegraph/`、`.idea/`、`frontend/.idea/`、`.mcp.json`、`opencode.jsonc`、`debug_cli.py`、`quick_sort.py`

## uv.lock 处理

当前 uv.lock 被 aliyun 镜像 URL 污染（1666 行 diff，几乎全是 registry URL 改写）。需先 `git checkout -- uv.lock` 还原，再用 `uv lock --default-index https://pypi.org/simple` 重新生成，验证 diff 行数小（仅 cdy-agent 版本号变化）后方可纳入提交。

## Commit Message

imperative summary 风格，标题 + 正文说明新增 flag、配置层、文档更新、版本号、uv.lock 重新生成。

## 验收标准

- 1 个新提交在 main 上，仅含上述 8 个文件
- 推送成功，origin/main 与本地 main 同步
- planning.py 等未跟踪文件仍保留在工作区未被提交

## 技术方案

### 实现策略

按 AGENTS.md 的 git 规范执行单 commit + push 流程，核心风险点是 uv.lock 镜像污染的清理。

### uv.lock 重新生成流程

1. `git checkout -- uv.lock` 还原被镜像污染的版本
2. `uv lock --default-index https://pypi.org/simple` 用官方索引重新生成（仅刷新 pyproject.toml 中 version 0.1.1→0.1.2 带来的 cdy-agent 版本号变化）
3. `git diff --stat uv.lock` 验证行数小（仅 cdy-agent 自身 version 行）；若仍有大量 URL 改动说明本地镜像配置未清（需检查 `~/.config/uv/uv.toml` 或 `UV_INDEX`/`UV_DEFAULT_INDEX` 环境变量），此时停止并报告用户

### Commit Message

```
Add --rebuild-frontend option for web command

- New `--rebuild-frontend/--no-rebuild-frontend` CLI flag and
  `CDY_AGENT_REBUILD_FRONTEND` env var plus `rebuild_frontend` workspace
  config key, resolved via the existing CLI > env > config > default
  precedence.
- `_build_web_frontend(rebuild=...)` now skips the npm build when
  `src/cdy_agent/web/static/index.html` already exists and rebuild is
  False; passes True to force a fresh build.
- Updated AGENTS.md and README.md to document the new flag and default
  behavior; bumped version to 0.1.2.
- Regenerated uv.lock via `uv lock --default-index https://pypi.org/simple`.
```

### 安全约束

- 不使用 `git add -A` 或 `git add .`，仅显式列出 8 个文件路径，避免误纳入本地文件
- 不修改 git config，不使用 `--no-verify`、`--force`、`--amend`
- 推送前再次 `git status` 确认工作区仍有 planning.py 等未跟踪文件未被纳入
- 使用 `--default-index https://pypi.org/simple` 符合 AGENTS.md 明确要求

### 执行命令链

1. `git checkout -- uv.lock`
2. `uv lock --default-index https://pypi.org/simple`
3. `git diff --stat uv.lock`（验证，若异常则停止）
4. `git add src/cdy_agent/cli.py src/cdy_agent/config.py tests/test_cli.py tests/test_config.py AGENTS.md README.md pyproject.toml uv.lock`
5. `git --no-pager diff --cached --stat`（确认暂存内容只含 8 个文件）
6. `git status`（确认 planning 等仍 untracked）
7. `git commit -m "..."`
8. `git push origin main`
9. `git --no-pager log -1 --stat`（验证提交内容）

## Agent Extensions

### MCP

- **codegraph**
- Purpose: 在执行前用 codegraph_explore 快速确认 _build_web_frontend / resolve_rebuild_frontend 的当前实现签名，确保 commit message 描述与代码实际一致
- Expected outcome: 验证 cli.py 和 config.py 的改动签名与 commit message 正文描述匹配，避免提交信息失真