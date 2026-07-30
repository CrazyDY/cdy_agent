# CDY Agent Web UI Design

Date: 2026-07-30

Status: Approved

## Summary

CDY Agent will add a local, single-user Web UI focused on persistent chat. The
existing CLI remains supported and keeps its current behavior. A new
`cdy-agent web --workspace .` command starts one FastAPI process bound only to
`127.0.0.1`, serves a Vue 3 application, and connects the browser to the existing
bounded Agent Tool Loop through a strict WebSocket protocol.

The first release supports:

- starting a new conversation;
- listing and resuming saved conversations;
- deleting a conversation after browser confirmation;
- streamed assistant text;
- concise tool activity states;
- the complete existing confirmation contract, including deny, allow once, and
  exact persistent Shell approval;
- cancellation when the user clicks Stop, refreshes, closes the page, or loses
  the WebSocket connection; and
- saving only complete, successful user/assistant turns.

The server uses one fixed workspace selected at startup. The browser cannot
choose or change the workspace. The first release is local-only, has no account
system, and permits only one active Agent turn across the process.

## Goals

1. Make CDY Agent usable through a focused two-column chat interface without
   weakening its workspace, confirmation, persistence, or observability
   invariants.
2. Reuse the existing `Agent`, `ModelGateway`, tool registry, Skills, SQLite
   stores, and trace recorder instead of duplicating their behavior in a Web
   implementation.
3. Preserve API neutrality across Responses and Chat Completions and use the
   same bounded Agent Tool Loop as the CLI.
4. Deliver the UI as part of the Python package so a user starts one process
   with one command.
5. Keep the Web transport outside the Agent core. The Agent must not import
   FastAPI, WebSocket, Vue, or browser-specific types.

## Non-goals

The first release does not include:

- LAN or public network binding;
- login, accounts, multiple users, or user-level authorization;
- multiple workspaces or a browser filesystem picker;
- concurrent Agent turns, a background job queue, or reconnectable turns;
- continuing a turn after the browser disconnects;
- conversation rename, search, export, pagination, or automatic summaries;
- dedicated Web pages for memories, notes, Todos, Skills, traces, evals, or
  configuration;
- detailed tool arguments, tool results, or trace payloads in the UI;
- automatic memory extraction;
- a provider abstraction, Agent framework, workflow engine, MCP, or
  multi-Agent orchestration; or
- rollback of file, process, memory, or other side effects that completed before
  a turn was cancelled.

## User Experience

### Layout

The selected layout is a focused two-column chat screen:

- The left sidebar contains the CDY Agent identity, New conversation action,
  saved conversation summaries grouped by update time, and the fixed workspace
  name.
- The main header contains the active conversation preview plus the effective
  model and API mode. These values are read-only.
- The timeline renders user and assistant messages. Assistant Markdown and code
  blocks are supported.
- A transient status row displays concise activity such as "Reading file",
  "Running tool", or "Waiting for confirmation". Ordinary tool arguments and
  results are neither shown nor persisted.
- The composer accepts one prompt when idle. During a turn it is disabled and a
  Stop action is shown.

New conversations are ephemeral until their first successful turn is saved.
Only then do they appear in the sidebar. Loading a saved conversation retrieves
the exact messages in SQLite. Deletion requires a browser confirmation dialog
and calls the existing safe store deletion operation. Conversation rename is
not included.

### Rendering and transient failures

Assistant text is rendered incrementally. The current incomplete message is
clearly marked as running. If the turn fails or is cancelled, the attempted user
message and partial assistant text remain visible only in the current browser
state with a failed or cancelled marker and a Retry action. They are not written
to SQLite and are not included in the next model context.

On reload, the UI reconstructs the conversation only from persisted messages.
This intentionally removes failed, cancelled, and unsaved browser-only content.

If persistence fails after text has streamed, the UI marks the response as not
saved and does not treat the turn as complete. The next reload again returns the
last complete SQLite state.

### Confirmation dialog

When a tool requests confirmation, the current Agent worker pauses and the UI
shows a blocking dialog containing the exact server-generated
`ConfirmationRequest.description`.

Every request supports:

- Deny; and
- Allow once.

When `ConfirmationRequest.allow_always` is true, the dialog additionally
supports:

- Always allow.

For Shell, "Always allow" preserves the existing semantics: it saves only the
prepared complete executable and exact argv, including argument order and
content. The browser cannot broaden an approval into a prefix, wildcard, or
different command. The prepared execution used after approval is the same
prepared execution that produced the confirmation description.

While a dialog is open, the composer stays disabled. Closing, refreshing, losing
the WebSocket, or pressing Stop resolves the wait as cancellation and never as
approval.

## Command Surface and Startup

The Typer application adds:

```text
cdy-agent web --workspace <path> [--port <port>] [--open/--no-open]
```

Behavior:

- `--workspace` uses the same workspace resolution and validation rules as
  existing commands and is fixed for the server lifetime.
- The server binds exactly to `127.0.0.1`; the first release exposes no `--host`
  option.
- `--port` defaults to `8000`. An occupied or invalid port fails startup instead
  of silently binding elsewhere.
- `--open` is the default and opens the capability URL in the system browser.
  `--no-open` prints the URL without opening it.
- Startup resolves and validates the workspace configuration, effective model,
  API mode, system prompt, pricing, logging, and provider credential before
  accepting browser work.
- The Web UI uses provider streaming by default. A streamed provider request is
  required for prompt cancellation and incremental output. Existing `ask` and
  `chat` streaming defaults and overrides do not change.
- Startup errors are rendered by the CLI using its existing user-facing error
  conventions.

`OPENAI_API_KEY` and `OPENAI_BASE_URL` remain environment-only. No secret is sent
to the browser or written to workspace configuration.

## Architecture

### Process model

One process contains:

1. the Typer `web` command and startup configuration;
2. a Uvicorn/FastAPI server;
3. the built Vue static assets;
4. an HTTP conversation API;
5. one WebSocket turn coordinator;
6. the existing Agent, model gateway, tools, Skills, trace recorder, and stores;
   and
7. a process-wide active-turn lock.

The process-wide lock permits one active turn for the fixed workspace. Read-only
HTTP session requests may continue, but conversation deletion is rejected while
any turn is active. A second turn request receives `server_busy`; it is not
queued. This avoids concurrent writes to workspace JSON state, approvals, Skill
state, traces, and SQLite from independent Agent turns.

### Backend module boundaries

New backend code lives under `src/cdy_agent/web/`:

- `app.py` creates the FastAPI application, installs security middleware, serves
  static files, and wires routes.
- `auth.py` owns the per-process browser capability, cookie exchange, Host and
  Origin validation, and WebSocket authentication.
- `schemas.py` owns strict Pydantic HTTP and WebSocket payload models.
- `sessions.py` adapts `ConversationStore` operations to HTTP without moving
  persistence policy into the route functions.
- `turns.py` owns the active-turn coordinator, thread/async event bridge,
  confirmation wait, cancellation lifecycle, and WebSocket protocol.
- `errors.py` maps known domain and provider failures to stable safe API error
  codes.

A small shared application-composition module outside `cli.py` may construct the
model gateway, registry, Skills, trace recorder, and Agent for both CLI and Web
callers. It does not own terminal input/output, Web protocols, or persistence
policy. CLI confirmation prompting remains in `cli.py`; Web confirmation stays
in the turn coordinator.

Existing ownership remains unchanged:

- `agent.py` owns the bounded API-neutral model/tool loop;
- `openai_client.py` remains the only SDK boundary;
- `tools/` and `skills/` retain preparation, security, approval persistence, and
  execution;
- `memory/` retains SQLite persistence; and
- `observability/` retains sanitized tracing.

### Frontend boundaries

Vue source lives in `frontend/` and uses Vue 3, TypeScript, Vite, and the
Composition API. The first release does not add Pinia or another global state
framework.

Focused components include:

- application shell and startup state;
- conversation sidebar;
- conversation timeline;
- Markdown message renderer;
- composer and Stop action;
- concise tool status;
- confirmation dialog; and
- transient error/retry notification.

A single composable owns the authenticated WebSocket and applies protocol events
to in-memory turn state. HTTP session access is kept in a small typed client.
Browser storage does not persist prompts, replies, credentials, capability
tokens, tool data, or approvals.

## HTTP API

All `/api` routes require the authenticated local browser session.

### `GET /api/bootstrap`

Returns:

- a display-safe workspace name and resolved path;
- effective model and API mode;
- whether a turn is currently busy; and
- saved `ConversationSummary` records in existing update order.

It never returns API credentials, environment variables, system prompts, tool
definitions, approvals, trace payloads, pricing configuration, or workspace
configuration file contents.

### `GET /api/sessions/{session_id}`

Requires a complete canonical UUID and returns the exact persisted messages.
Missing or invalid sessions return stable safe errors.

### `DELETE /api/sessions/{session_id}`

Requires a complete canonical UUID. The browser performs a confirmation before
calling it. The server returns `server_busy` while a turn is active and otherwise
uses the existing store deletion behavior. No abbreviated ID is accepted.

Unknown request fields are rejected. Request and response sizes are bounded.

## WebSocket Protocol

The authenticated endpoint is:

```text
WS /api/turns
```

Payloads are JSON objects validated by discriminated Pydantic models with
unknown fields forbidden. Each active turn has a server-generated `turn_id`.
Each confirmation has a server-generated, single-use `approval_id`.

### Client events

- `turn.start`: contains a bounded non-empty prompt and an optional complete
  saved `session_id`.
- `turn.cancel`: contains the current `turn_id`.
- `approval.resolve`: contains the current `turn_id`, current `approval_id`, and
  exactly one of `deny`, `allow_once`, or `allow_always`.

### Server events

- `turn.accepted`: reports `turn_id` and the conversation `session_id`.
- `assistant.delta`: contains one non-empty text delta.
- `tool.status`: contains a safe tool name, phase, and concise display label but
  no arguments or results.
- `approval.required`: contains `turn_id`, `approval_id`, the exact confirmation
  description, and whether always-allow is supported.
- `turn.completed`: contains the final assistant message and updated session
  summary after persistence succeeds.
- `turn.failed`: contains a stable code, safe message, and retryable flag.
- `turn.cancelled`: confirms cooperative cancellation.
- `server.busy`: rejects a turn while the process lock is held.
- `protocol.error`: rejects malformed, oversized, unknown, stale, or
  out-of-order input.

An approval is accepted at most once and only for the current turn and current
pending request. `allow_always` is rejected when the request does not advertise
it. Stale decisions cannot approve later tool calls.

## Turn Lifecycle and Persistence

1. The coordinator authenticates and validates `turn.start`.
2. It acquires the process-wide lock or returns `server.busy`.
3. It generates a `turn_id`. For a new conversation it also preallocates a
   canonical conversation UUID without writing an empty conversation.
4. It loads persisted history when resuming and appends the new user message
   only to an in-memory `Conversation`.
5. It creates the trace and starts `Agent.run_stream` in a worker thread.
6. Thread-safe events carry text deltas and tool states to the async WebSocket
   sender.
7. A Web confirmation callback publishes `approval.required` and blocks on a
   per-request synchronization primitive.
8. `approval.resolve` returns an existing `ConfirmationDecision` to the tool
   registry. The registry continues its existing prepared-execution path.
9. When the Agent returns a final non-empty reply, the coordinator calls
   `ConversationStore.append_turn` with the complete user and assistant
   messages.
10. Only after the append succeeds does the server publish `turn.completed`.
11. All terminal paths finish or invalidate the trace as appropriate, clear
    pending approvals, and release the active-turn lock.

The Web layer does not save partial messages and does not ask the Agent loop to
own persistence.

## Cooperative Cancellation

A framework-neutral run-control object exposes a thread-safe cancellation signal
to the Agent, model gateway, tool registry, and bounded process execution. The
Agent remains independent of FastAPI.

Cancellation is requested when:

- the browser sends `turn.cancel`;
- its WebSocket disconnects;
- the page refreshes or closes; or
- the server shuts down.

The implementation:

- prevents a new model call or tool call from starting after cancellation;
- closes an active Responses or Chat Completions SDK stream and checks the
  cancellation signal between streamed events;
- wakes a pending confirmation callback and raises an internal cancellation
  exception rather than returning a decision;
- adds cancellation polling to the shared bounded-process runner and uses its
  existing process-tree cleanup on POSIX and Windows;
- routes Shell execution through the cancellable bounded-process path while
  preserving `shell=False`, exact prepared argv, sanitized environment, timeout,
  bounded output, workspace cwd, and process-tree cleanup;
- applies the same cancellation signal to Skill script execution; and
- checks before and after other tool boundaries.

Short synchronous filesystem or SQLite operations may finish before their next
cancellation check. Completed side effects are never rolled back automatically.
After cancellation no additional model or tool step begins, the turn is not
persisted, and the lock is released only after the worker has actually stopped.
The server never abandons a still-running worker and reports cancellation as
complete.

## Security

### Local binding and browser capability

The service listens only on `127.0.0.1`. On startup it generates a
cryptographically random, process-local browser capability and constructs an
initial URL containing it. The capability:

- is printed to the launching terminal and optionally opened in the browser;
- is exchanged once for an `HttpOnly`, `SameSite=Strict` session cookie;
- is removed from the address bar by redirecting to a clean URL;
- is never stored in local storage, workspace files, logs, or traces; and
- becomes invalid when the process exits.

HTTP requests validate the expected Host and cookie. WebSocket upgrades validate
the exact local Origin, Host, and cookie. CORS is disabled. Invalid browser
sessions cannot read static application data through authenticated routes, open
the turn socket, list sessions, or delete sessions.

This is a local capability boundary, not a multi-user authentication system. A
malicious process already running as the same OS user is outside the first
release threat model, but a cross-origin browser page must not be able to drive
the local Agent.

### Existing workspace and tool invariants

The Web layer supplies only the startup-resolved workspace to tools and stores.
It does not accept paths that redefine the workspace. Existing traversal,
symlink, reparse-point, regular-file, atomic-write, approval, prepared-command,
timeout, environment, output-limit, and corrupt-state failure behavior remains
unchanged.

The confirmation protocol transports `ConfirmationRequest` and returns
`ConfirmationDecision`; it does not recreate Shell policy in the Web layer.

### Content and observability

Assistant Markdown is rendered with a Markdown library and sanitized with
DOMPurify. Raw HTML, script execution, unsafe URLs, inline event handlers, and
other active content are removed.

Logs and traces retain their current exclusions. They do not gain prompts,
replies, tool arguments/results, confirmation descriptions, capability tokens,
cookies, credentials, or environment dumps. Tool status events contain only a
safe tool identifier and generic phase. Confirmation descriptions are sent only
to the authenticated active browser because user review requires them.

## Error Handling

Known failures map to stable codes without exposing stack traces or SDK internals:

- invalid request or protocol ordering;
- authentication or origin rejection;
- conversation not found or invalid conversation store;
- server busy;
- approval denied or invalid persistent approval;
- turn cancelled;
- model call limit exceeded;
- provider unavailable or unsupported provider response;
- process timeout or execution failure;
- trace warning; and
- conversation persistence failure.

Recoverable tool failures continue to serialize back to the model exactly as the
current Agent loop requires. Observability remains best effort and never changes
the Agent result.

Unexpected exceptions close the active turn safely, clear pending approvals,
stop further work, release the lock after worker termination, and send only a
generic failure event when the socket remains available.

## Dependencies and Packaging

Python runtime dependencies add FastAPI and Uvicorn. Existing Pydantic models
remain the schema foundation.

Frontend runtime dependencies are intentionally small:

- Vue 3;
- a Markdown renderer; and
- DOMPurify.

Development dependencies include Vite, TypeScript, Vitest, Vue Test Utils, and
the Vue Vite plugin. The Node lockfile is committed.

`frontend/` contains source code. Production assets are built into
`src/cdy_agent/web/static/`, included as Python package data, and shipped in both
wheel and source distribution. Built application assets are committed so Python
installation and wheel consumption do not require Node. Source maps are not
committed or packaged.

Frontend changes must run the reproducible locked build and update the packaged
assets. `uv build` verifies that required static assets exist and includes them;
it does not download Node dependencies or run an implicit networked frontend
install.

## Testing

All Python tests remain offline and deterministic.

### Agent and cancellation tests

- cancellation before a model call prevents the call;
- cancellation during Responses and Chat Completions streaming closes the
  stream;
- cancellation during confirmation wakes the worker without approving;
- cancellation before the next tool prevents execution;
- cancellation of Shell and Skill scripts terminates the process tree on
  supported platforms;
- cancellation never saves a partial turn;
- the model-call limit still guards streaming and non-streaming loops; and
- CLI behavior is unchanged when no run control is supplied.

### Coordinator and protocol tests

- one accepted turn holds the global lock;
- a second turn receives `server_busy` and is not queued;
- text and tool status events preserve order;
- approval deny, allow once, and allow always return the correct existing enum;
- unsupported always-allow, stale IDs, duplicate decisions, and malformed
  payloads fail closed;
- disconnect and explicit Stop take the same cancellation path;
- completion is emitted only after `append_turn` succeeds;
- provider, Agent, trace, and persistence failures release the lock; and
- both API modes use the same Web coordinator.

### HTTP and security tests

- the capability exchange sets the intended cookie and redirects to a clean URL;
- invalid tokens, cookies, Host headers, and Origins are rejected;
- cross-origin WebSocket upgrades fail;
- bootstrap returns only allowlisted non-secret fields;
- complete UUIDs are required for session load and deletion;
- deletion is rejected during an active turn; and
- missing or corrupt stores retain existing failure behavior.

### Frontend tests

Vitest and Vue Test Utils cover:

- bootstrap and empty states;
- new and resumed conversation rendering;
- ordered streaming deltas;
- safe Markdown sanitization;
- concise tool statuses;
- each confirmation option and unsupported always-allow;
- disabled composer and Stop;
- failed/cancelled local messages and Retry;
- sidebar refresh after successful persistence; and
- deletion confirmation and API failure.

### Final verification

Before completion:

```powershell
uv run pytest
npm --prefix frontend test
npm --prefix frontend run build
uv run cdy-agent --help
uv run cdy-agent web --help
uv build
```

The Web server receives a manual local smoke test using a fake or explicitly
configured provider, without adding real credentials or model responses to the
repository.

## Documentation

Implementation updates:

- `README.md` with prerequisites, frontend development, the local capability
  URL, startup examples, confirmation semantics, cancellation semantics, and
  troubleshooting;
- the active roadmap/design record for the delivered phase;
- CLI help for all `web` options; and
- packaging instructions for rebuilding Vue assets.

Examples never include real credentials, cookies, capability tokens, prompts,
model replies, or local runtime state.

## Acceptance Criteria

The first release is complete when:

1. `cdy-agent web --workspace .` opens or prints an authenticated local UI bound
   only to `127.0.0.1`.
2. A user can start, stream, stop, save, list, resume, and delete conversations.
3. Ordinary tools show concise status and confirmation-required tools preserve
   every existing decision and Shell persistent-approval invariant.
4. Disconnecting cancels the worker, confirmation wait, model stream, and
   cancellable child process before the lock is released.
5. Failed, cancelled, and unsaved turns never enter SQLite or later model
   context.
6. Both Responses and Chat Completions pass the same offline Web tests.
7. Browser security checks prevent a cross-origin page from controlling the
   local service.
8. The full Python suite, frontend suite, frontend production build, CLI help,
   and Python package build pass.
9. Existing CLI behavior and security contracts remain covered and unchanged.
