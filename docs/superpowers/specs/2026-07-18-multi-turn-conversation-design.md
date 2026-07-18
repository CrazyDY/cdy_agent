# Multi-Turn Conversation Design

## Background

CDY Agent currently supports one-shot prompts through either the OpenAI
Responses API or the Chat Completions API. The third roadmap stage adds an
interactive, in-process conversation while preserving the existing `ask`
command and the boundary between terminal interaction, conversation state,
and SDK calls.

## Goals

- Add a `cdy-agent chat` REPL while retaining `cdy-agent ask`.
- Continue conversation context across multiple turns in one process.
- Give Responses and Chat Completions users the same conversation semantics.
- Keep conversation state independent of Typer and the OpenAI SDK.
- Cover all behavior without real credentials, network access, or persistent
  user data.

## Non-Goals

- Persisting or restoring conversations.
- Trimming, summarizing, or otherwise managing the context window.
- Adding system prompts, streaming, retries, tools, Skills, or memory.
- Using provider-specific conversation identifiers such as
  `previous_response_id`.

## Architecture

### Conversation State

Add `src/cdy_agent/conversation.py` as the owner of in-memory conversation
state. It stores an ordered sequence of messages with exactly two supported
roles: `user` and `assistant`.

The public interface allows callers to append a non-empty message and obtain
the current ordered history. Returned history must not allow callers to mutate
the conversation's internal collection accidentally. The conversation layer
does not import Typer or the OpenAI SDK and does not persist data.

### OpenAI Client Boundary

Extend `src/cdy_agent/openai_client.py` with a multi-message request boundary.
It accepts the complete canonical conversation history, validates that it is
non-empty, and translates it at the SDK boundary:

- Responses mode sends the canonical role/content messages as structured
  `input`.
- Chat Completions mode sends the same canonical messages as `messages`.

The existing `generate_reply()` interface remains available for the `ask`
command and delegates to the shared multi-message implementation with one user
message. This preserves the established one-shot behavior while keeping SDK
translation in one module.

Full history is sent on every turn. This is less token-efficient than a
provider-native response cursor, but it gives both API modes one state model
and works with a wider range of OpenAI-compatible providers and gateways.

### CLI Boundary

Add a `chat` command to `src/cdy_agent/cli.py`. It resolves the model override
and configured API mode once, then runs the terminal input loop. The CLI owns
prompts, exit recognition, output labels, and user-facing error messages; it
does not construct SDK payloads.

The existing `ask` command remains unchanged from the user's perspective.

## Data Flow

For each non-empty user turn:

1. The CLI appends the user message to the in-memory conversation.
2. The CLI passes the complete ordered history to the OpenAI client boundary.
3. The client translates the history for the configured API mode and makes one
   SDK call.
4. On success, the CLI appends the assistant text to the conversation and
   prints it.
5. The next user turn includes both preceding user and assistant messages.

An assistant message is never appended before a successful model response.

## REPL Contract

- Start the REPL with `cdy-agent chat` and optionally override the model with
  `--model`.
- Read turns with the prompt `You: `.
- Print replies with the prefix `Assistant: `.
- Ignore blank or whitespace-only input without calling the model.
- Treat `/exit` and `/quit` as case-insensitive exit commands after trimming
  surrounding whitespace.
- Treat EOF and `KeyboardInterrupt` as successful, clean exits.
- Keep all state in the current process and discard it on exit.

## Validation and Error Handling

Conversation state rejects blank content and unsupported roles. The OpenAI
client rejects an empty history, unsupported API modes, and empty model output
before returning control to the CLI.

Configuration, authentication, connection, rate-limit, SDK, and invalid-output
errors use the same concise user-facing messages as `ask`. A failed request
does not add an assistant message and terminates `chat` with exit code 1.
Retries and recovery within the REPL are outside this stage.

## Testing

Add focused offline tests for:

- Appending conversation messages in order, validating roles and content, and
  protecting the internal message collection from accidental mutation.
- Sending complete two-turn histories through both Responses and Chat
  Completions modes.
- Preserving all current one-shot `generate_reply()` and `ask` behavior.
- Running two successful `chat` turns with the second request containing the
  first turn's context.
- Ignoring blank input; handling `/exit`, `/quit`, EOF, and Ctrl-C; resolving
  model and API mode; and presenting request errors without a traceback.

Tests use fake SDK clients, Typer's `CliRunner`, and monkeypatching. They do not
use a real API key, network access, or the contributor's filesystem.

## Documentation and Verification

Update `README.md` to identify multi-turn conversation as the current stage and
document both `ask` and `chat` usage. Verify the implementation with:

```powershell
uv run pytest
uv run cdy-agent --help
uv run cdy-agent ask --help
uv run cdy-agent chat --help
uv build
```
