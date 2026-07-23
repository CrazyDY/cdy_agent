# Standard Agent Skills Refactor Design

## Summary

Refactor the workspace Skill subsystem to support the standard Agent Skills
directory format defined by the official
[Agent Skills specification](https://agentskills.io/specification).

The project will continue discovering Skills only from:

```text
<workspace>/.cdy-agent/skills/<skill-name>/
```

Each Skill will use this standard internal structure:

```text
<skill-name>/
├── SKILL.md
├── scripts/       # optional executable files
├── references/    # optional on-demand documentation
└── assets/        # optional templates, data, and binary resources
```

The existing root-level `tools.py` and `create_tools(workspace)` extension will
be removed without a compatibility layer. A root-level `tools.py` may remain as
an unrecognized extra file, but CDY Agent will never scan, import, register, or
execute it.

## Goals

- Strictly validate standard `SKILL.md` frontmatter.
- Support the standard `scripts/`, `references/`, and `assets/` directories.
- Preserve progressive disclosure: catalog metadata at discovery, instructions
  at activation, and individual resources only when needed.
- Allow scripts written for any installed runtime without using shell string
  interpretation.
- Require explicit user confirmation for every script execution.
- Keep Skill resource access confined to the activated Skill and workspace.
- Preserve the existing module boundaries and offline test requirements.

## Non-goals

- Discovering project Skills from `.agents/skills/`, user-level directories, or
  any location other than `.cdy-agent/skills/`.
- Backward compatibility for `tools.py` or dynamic registration of tools
  returned by `create_tools(workspace)`.
- Installing script runtimes or dependencies.
- Sandboxing script processes beyond path validation, argument-array execution,
  timeouts, output limits, and explicit confirmation.
- Loading all resources during activation.
- Returning binary resources as Base64 model context.
- Treating `allowed-tools` as authorization to bypass CDY Agent confirmations.
- Scanning custom directories outside `scripts/`, `references/`, and `assets/`.

## Architecture

### `src/cdy_agent/skills/models.py`

The model layer will describe standard metadata and discovered resources.

`SkillMetadata` will contain:

- `name: str`
- `description: str`
- `license: str | None`
- `compatibility: str | None`
- `metadata: Mapping[str, str]`
- `allowed_tools: str | None`

`SkillResource` will contain:

- `category: Literal["scripts", "references", "assets"]`
- `relative_path: str`
- `path: Path`
- `size: int`

`DiscoveredSkill` will contain metadata, the resolved Skill directory, the
Markdown instruction body, and an ordered tuple of resources. It will no longer
contain `tools_path` or `has_tools`.

Existing `SkillDiagnostic` and `SkillDiscovery` result types remain responsible
for isolating invalid entries from valid Skills.

### `src/cdy_agent/skills/loader.py`

The loader remains the only filesystem discovery and format-validation
boundary. It will:

1. Resolve the workspace and scan direct children of
   `.cdy-agent/skills/`.
2. require a safe regular `SKILL.md`;
3. parse and strictly validate standard frontmatter;
4. retain the non-empty Markdown body as activation instructions;
5. recursively enumerate regular files only under the three recognized
   resource directories;
6. sort resources deterministically by category and relative path; and
7. return diagnostics for invalid Skills without blocking valid siblings.

Discovery will not read resource contents or execute code.

The loader will also expose narrowly scoped revalidation helpers used before
resource reads and script execution. Those helpers will resolve real paths
again, reject symbolic links and changed file types, and prove that the target
still belongs to the expected standard directory inside the workspace.

### `src/cdy_agent/skills/manager.py`

`SkillManager` will own the in-memory catalog and activation state. It will no
longer import modules, request activation approval, or mutate the Tool Registry.

It will provide operations for:

- listing and searching discovered Skills;
- activating a Skill and returning its instructions, metadata, root directory,
  and resource manifest;
- resolving an activated reference or asset for on-demand reading; and
- resolving an activated script for confirmed execution.

Search scoring continues to use the name, description, and an instruction-body
prefix. Optional metadata and resource contents do not affect search ranking.

### `src/cdy_agent/skills/tools.py`

The CLI will register five Skill tools up front:

- `list_skills`
- `search_skills`
- `activate_skill`
- `read_skill_resource`
- `run_skill_script`

No tool will be added dynamically after activation. The ordinary `read_file`
and `shell` tools retain their current behavior and allowlists.

## Strict `SKILL.md` Validation

The accepted frontmatter fields are exactly:

- `name` (required)
- `description` (required)
- `license` (optional)
- `compatibility` (optional)
- `metadata` (optional)
- `allowed-tools` (optional and experimental in the standard)

Unknown fields make the Skill invalid rather than being silently ignored.
Duplicate YAML keys remain invalid.

Field rules are:

- `name` is 1–64 characters, contains only lowercase ASCII letters, digits, and
  single hyphens, does not begin or end with a hyphen, does not contain `--`,
  and exactly matches the parent directory name.
- `description` is a non-empty string of at most 1024 characters.
- `license`, when present, is a non-empty string.
- `compatibility`, when present, is a non-empty string of at most 500
  characters.
- `metadata`, when present, is a mapping whose keys and values are strings.
- `allowed-tools`, when present, is a non-empty, space-separated string. It is
  returned to the model but never changes CDY Agent's confirmation rules.
- The Markdown body after frontmatter is non-empty.

`SKILL.md` retains the existing 256 KiB safety limit. The specification's
recommendation to keep the file under 500 lines will be documented but will not
be enforced as a validity rule.

This changes the current underscore-based naming rule. For example,
`code_review` must migrate to a directory and metadata name of `code-review`.

## Resource Discovery and Safety

Only `scripts/`, `references/`, and `assets/` are recognized. They may contain
nested directories. Other root files and directories are ignored.

Resource paths are represented relative to the Skill root with POSIX
separators, for example:

```text
references/api/http.md
assets/templates/report.docx
scripts/convert.py
```

The Skill directory, `SKILL.md`, recognized resource directories, nested
directories, and resource files must not be symbolic links. Every resolved path
must remain inside both the Skill root and workspace.

Each Skill may contain at most 512 recognized resource files. Exceeding the
limit invalidates the Skill and produces an explicit diagnostic. Resource
contents are not read during discovery, so individual file size limits are
enforced by the operation that consumes a resource.

Activation returns each resource's category, relative path, and byte size. It
does not eagerly return content.

## Tool Behavior

### `list_skills`

The listing retains each Skill's name, description, activation state, and
search keywords. The old `has_tools` field is replaced with resource counts:

```json
{
  "resource_counts": {
    "scripts": 2,
    "references": 3,
    "assets": 1
  }
}
```

Diagnostics retain stable entry, code, and message fields.

### `search_skills`

Search behavior and limits remain unchanged, except matches return resource
counts instead of `has_tools`.

### `activate_skill`

Activation is a read-only operation and does not require confirmation. The
first activation returns:

- `status: "activated"`;
- the Skill name;
- all parsed standard metadata;
- the full Markdown instruction body;
- the absolute Skill directory;
- a statement that relative paths resolve from that directory; and
- the ordered resource manifest.

Repeated activation returns `status: "already_active"` and the same stable
payload without duplicating activation state.

### `read_skill_resource`

The tool accepts:

```text
name: activated Skill name
path: exact manifest-relative path
```

The Skill must be active. The path must exactly identify a discovered
`references/` or `assets/` resource. Script files are deliberately excluded
from this content-loading tool; script interfaces should be documented in
`SKILL.md` or exposed through the script's `--help` output.

Before reading, the manager revalidates the resource against the workspace,
Skill root, category, regular-file requirement, and no-symlink policy.

For valid UTF-8 text up to 1 MiB, the result includes the content and
`binary: false`. For non-UTF-8 resources, the result includes the absolute path,
relative path, size, and `binary: true`, without Base64 content. A text resource
larger than 1 MiB fails with a stable size-limit error rather than returning
silently incomplete reference material.

### `run_skill_script`

The tool accepts:

```text
name: activated Skill name
argv: non-empty array of strings
timeout_seconds: optional integer from 1 through 300, default 30
```

The Skill must be active. `argv` must contain exactly one argument that exactly
matches a discovered `scripts/` resource. This supports both interpreter-based
and directly executable forms:

```json
["python", "scripts/report.py", "--format", "json"]
["uv", "run", "scripts/extract.py", "input.pdf"]
["scripts/process.exe", "--input", "data.csv"]
```

Before confirmation, the script is revalidated and its relative argument is
replaced by the resolved absolute path. Execution uses:

- the final argument array with `shell=False`;
- the Skill root as the working directory;
- the current user's permissions;
- the inherited environment after the same sensitive command-configuration
  sanitization used by the ordinary shell tool;
- the requested timeout; and
- separate 64 KiB limits for stdout and stderr, with explicit truncation flags.

The runtime or interpreter is intentionally not allowlisted. The current
machine must already provide it. This allows Python, PowerShell, shell,
JavaScript, native binaries, and other runtimes without CDY Agent installing
dependencies.

Every invocation requires confirmation. The prompt displays the Skill name,
resolved script path, final argument array, working directory, and that the
process runs with current-user permissions. Approval applies only to that
single invocation. `allowed-tools` never suppresses this prompt.

The result reports the exit code, stdout, stderr, and truncation flags. A
non-zero exit is a script failure, not a successful result.

## Error Handling

Public failures will distinguish at least:

- invalid or unknown Skill;
- inactive Skill;
- unknown resource;
- wrong resource category;
- unsafe or changed resource path;
- resource too large;
- invalid script command;
- confirmation denied;
- missing runtime or executable;
- execution timeout; and
- non-zero script exit.

Filesystem and process exceptions will not expose tracebacks through
user-facing tool results. Diagnostics will identify invalid Skill entries
without preventing valid siblings from loading.

## Data Flow

1. CLI startup constructs the builtin registry and `SkillManager`.
2. The manager discovers and validates all workspace Skills without executing
   code or reading resource contents.
3. The five Skill tools are registered once.
4. The model lists or searches catalog metadata.
5. The model activates a matching Skill and receives instructions plus the
   resource manifest.
6. The model reads individual references or assets as needed.
7. When instructions require a script, the model submits an argument array.
8. CDY Agent revalidates and resolves the script, asks for confirmation, and
   executes it only after approval.

## Migration

The refactor intentionally removes:

- root-level `tools.py` discovery;
- `create_tools(workspace)`;
- activation-time Python imports;
- activation-time code approval;
- dynamic Tool Registry mutation; and
- `has_tools` in catalog and search results.

Existing standard `SKILL.md` files using underscore names must be renamed to
hyphenated names with matching directories. Existing executable logic must move
from `tools.py` into one or more command-line programs under `scripts/`, with
arguments and output behavior documented in `SKILL.md`.

Scripts should be non-interactive, provide useful `--help` output, write
machine-readable results to stdout where practical, diagnostics to stderr, and
use meaningful exit codes, following the official
[script guidance](https://agentskills.io/skill-creation/using-scripts).

## Testing Strategy

Implementation will follow test-driven development.

Loader tests will cover:

- all required and optional frontmatter fields;
- duplicate and unknown keys;
- field types and exact length constraints;
- hyphenated valid names and every invalid naming form;
- directory-name mismatch;
- non-empty instructions and file-size limits;
- recursive discovery and deterministic ordering in all three standard
  directories;
- ignored non-standard directories and root `tools.py`;
- the 512-resource limit; and
- symbolic links and workspace escapes at every relevant level.

Manager tests will cover:

- resource counts in list and search results;
- activation payloads and idempotent repeated activation;
- absence of activation confirmation or registry mutation;
- optional metadata and `allowed-tools` disclosure;
- rejection of inactive Skill access;
- exact resource matching and category enforcement; and
- revalidation after replacement, removal, or symbolic-link substitution.

Tool tests will cover:

- UTF-8 reference and asset reads;
- binary resource metadata;
- the 1 MiB text limit;
- interpreter, launcher, and direct-executable argument forms;
- missing or multiple script references;
- argument-array execution without shell interpretation;
- final command and working-directory confirmation text;
- denial, missing executables, timeouts, output limits, and non-zero exits; and
- confirmation on every invocation even when `allowed-tools` is present.

Integration tests will verify:

- the CLI registers all five Skill tools once;
- activation does not alter registry definitions;
- model-visible activation and resource payloads;
- user-facing confirmation and error presentation; and
- the complete offline pytest suite.

## Documentation

`README.md` will document:

- the standard directory structure;
- all supported frontmatter fields;
- strict naming and validation rules;
- progressive resource disclosure;
- arbitrary installed runtimes;
- per-execution confirmation and current-user permissions;
- the lack of dependency installation or sandboxing; and
- migration away from `tools.py`.

`docs/SKILL调用时序图.md` will replace the dynamic `tools.py` import and
registration sequence with discovery, activation, on-demand resource reads, and
confirmed script execution.

## Verification

The completed implementation must pass:

```powershell
uv run pytest
uv run cdy-agent --help
uv run cdy-agent ask --help
uv run cdy-agent chat --help
uv build
```

Tests must remain offline and isolated from provider environment variables.
The final diff must contain no credentials, model responses, caches, IDE files,
virtual environments, or unrelated formatting churn.
