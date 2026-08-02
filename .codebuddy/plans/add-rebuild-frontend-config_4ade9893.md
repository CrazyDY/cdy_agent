---
name: add-rebuild-frontend-config
overview: 为 `cdy-agent web` 新增「是否重新构建前端」配置项，遵循四层优先级（CLI 标志 > 环境变量 > 工作区配置 > 默认值）。默认 False=不强制重建：若 `src/cdy_agent/web/static/index.html` 已存在则跳过 npm 构建，不存在时仍执行一次首次构建；显式 True 才强制每次重建。
todos:
  - id: config-layer
    content: 在 config.py 新增 rebuild_frontend 字段、解析与 resolve_rebuild_frontend，并补 test_config.py 测试
    status: completed
  - id: cli-build-wiring
    content: 改 _build_web_frontend 签名与跳过逻辑，web 命令加 --rebuild-frontend 并接线，补 test_cli.py 测试
    status: completed
    dependencies:
      - config-layer
  - id: docs-sync
    content: 更新 README.md 与 AGENTS.md 配置键、环境变量与构建行为说明
    status: completed
    dependencies:
      - cli-build-wiring
---

## 产品概述

为 `cdy-agent web` 命令新增一个「是否重新构建前端资源」的配置项，避免每次启动都重复执行 npm 生产构建。

## 核心功能

- 新增配置项 `rebuild_frontend`，遵循项目既有四层优先级（CLI > 环境变量 > 工作区配置 > 默认值），与 `stream` 选项解析模式完全一致
- 默认值为 False（不强制重建）：当 `src/cdy_agent/web/static/index.html` 已存在时跳过 npm 构建；资产缺失时仍执行一次首次构建
- 显式置为 True 时强制每次重新构建
- 支持三种触发方式：CLI `--rebuild-frontend/--no-rebuild-frontend`、环境变量 `CDY_AGENT_REBUILD_FRONTEND`、工作区 `config.yaml` 的 `rebuild_frontend` 键
- 视觉效果：无界面变化，仅影响启动时的构建行为与控制台流程

## 技术栈

- 沿用现有技术栈：Python 3.10+、Typer、dataclass 配置模式；不引入新依赖

## 实现方案

复用 `config.py` 中 `resolve_streaming` 的四层解析模板，新增 `resolve_rebuild_frontend(override, workspace_config) -> bool`，环境变量名 `CDY_AGENT_REBUILD_FRONTEND`，默认 False。在 `cli.py` 的 `_build_web_frontend` 增加关键字参数 `rebuild: bool = False`：源码存在分支后，若 `not rebuild and (_WEB_STATIC_DIRECTORY / "index.html").is_file()` 则提前 return 跳过 npm。`web` 命令新增 `--rebuild-frontend/--no-rebuild-frontend` 选项，并把「加载工作区配置 + resolve」前移到 `_build_web_frontend` 调用之前，端口绑定仍先于构建以保持现有占端口语义。

## 实现注意事项

- `_build_web_frontend` 改为关键字参数 `rebuild` 带默认 False，现有无参调用（test_cli.py 多处）仍兼容；但 web 命令调用改为传 `rebuild=`，需把 test_cli.py 第 2004 行无参 lambda 改为 `lambda **kwargs`
- 复用既有 `_optional_bool` / `_parse_bool`，真值表与 `CDY_AGENT_STREAM` 一致（true/yes/on/1）
- 测试须离线、用 tmp_path、`monkeypatch.delenv("CDY_AGENT_REBUILD_FRONTEND")` 隔离；不读写贡献者真实工作区
- 改动聚焦新增配置与跳过逻辑，不做无关重构；保持 config.py 拥有 resolve、cli.py 拥有命令与 I/O 的边界

## 架构设计

现有分层架构不变。数据流：web 命令 → resolve_rebuild_frontend（CLI/env/config/默认）→ _build_web_frontend(rebuild=...) → 命中跳过条件则直接返回，否则执行 npm ci + npm run build → uvicorn 启动。

## 目录结构

```
src/cdy_agent/
├── config.py            # [MODIFY] WorkspaceConfig 新增 rebuild_frontend 字段；load_workspace_config 允许并解析该键；新增 resolve_rebuild_frontend
├── cli.py               # [MODIFY] 导入 resolve_rebuild_frontend；_build_web_frontend 增加 rebuild 关键字参数与跳过逻辑；web 命令新增 --rebuild-frontend 选项并调整调用顺序
tests/
├── test_config.py       # [MODIFY] 新增 resolve_rebuild_frontend 的四层优先级、真值表、非法值与 config 键测试
├── test_cli.py          # [MODIFY] 修正无参 lambda；新增 rebuild=False 跳过/缺失构建/True 强制重建测试
README.md                # [MODIFY] 更新 web 命令构建行为说明，补充 --rebuild-frontend 与 config.yaml rebuild_frontend
AGENTS.md                # [MODIFY] 工作区配置键与环境变量清单补充 rebuild_frontend / CDY_AGENT_REBUILD_FRONTEND
```

## 关键代码结构

```python
# config.py 新增解析函数（四层优先级，模板同 resolve_streaming）
def resolve_rebuild_frontend(
    override: bool | None = None,
    workspace_config: WorkspaceConfig | None = None,
) -> bool: ...
```