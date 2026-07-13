# cdy_agent

CAN DEAL YOUR EVERYTHING — a minimal personal AI agent built on the OpenAI Python SDK.

## Features

- Uses the OpenAI Responses API through the official `openai` Python package.
- Discovers local skills from `skills/**/SKILL.md`.
- Exposes each skill as a function tool so the model can choose when to use it.
- Supports instruction-only skills and executable skills.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
export OPENAI_API_KEY='your-api-key'
cdy-agent run "帮我总结这段会议纪要：..."
```

## Skill format

Create a folder under `skills/` with a `SKILL.md` file:

```markdown
---
name: summarize
description: Summarize long text into clear bullets, decisions, and next actions.
---

When this skill is used, read the supplied text and produce a concise structured summary.
```

Optional executable skills can add a `command` front-matter value. The command is run from the skill folder and receives the task on stdin.

```markdown
---
name: normalize-text
description: Normalize messy text.
command: ./normalize.py
---
```

## Architecture

- `cdy_agent.skills.SkillRegistry` discovers skills and converts them into Responses API tool schemas.
- `cdy_agent.agent.Agent` runs the OpenAI Responses API loop, executes requested skill tools, and submits tool outputs back to the model.
- `cdy_agent.cli` provides the `cdy-agent run` command.
