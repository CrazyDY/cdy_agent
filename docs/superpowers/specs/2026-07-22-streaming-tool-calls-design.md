# Streaming Tool Calls Design

## Goal

Make streaming mode execute model-requested tools without aborting the stream or
replaying the original request in non-streaming mode. Preserve the existing CLI
options, configuration precedence, confirmation behavior, model-call limit, and
OpenAI-compatible provider boundary.

## Chosen Approach

Aggregate streamed tool-call events inside `ModelGateway`, return the existing
`ToolCallResponse` model, and let `Agent.run_stream()` perform the same bounded
tool loop as `Agent.run()`. This keeps provider-specific event formats in
`openai_client.py` and orchestration in `agent.py`.

The rejected alternatives are:

- Restart the request without streaming when a tool call appears. This duplicates
  model work, can produce different decisions, and can duplicate already-emitted
  text.
- Depend on SDK-specific stream aggregation helpers. This would couple the gateway
  to one SDK implementation and weaken support for OpenAI-compatible providers.

## Gateway Contract

`ModelGateway.stream(...)` returns `ModelResponse`, not only `FinalResponse`.
Text deltas are still delivered immediately through `on_text`.

For Chat Completions, `_stream_chat_completion()` groups tool-call deltas by their
non-negative `index`. It records the first non-empty call ID, concatenates function
name fragments and argument fragments in arrival order, and validates the completed
fields with the existing `ToolCall` normalization rules. Parallel calls preserve
ascending index order. If calls are present, the method returns
`ToolCallResponse(calls, ChatContinuation(...), usage)`; otherwise it returns the
aggregated final text. Streaming requests set
`stream_options={"include_usage": True}` and normalize usage from any chunk,
including an empty-choice final chunk. Repeated usage is accepted only when values
agree. A tool-call stream is executable only after one consistent terminal
`finish_reason` of `tool_calls`; a text stream requires `stop`. Missing, conflicting,
truncated, filtered, or legacy function-call terminal reasons are unsupported.
Once a terminal reason is observed, it is latched: every later chunk is rejected
except one empty-choice chunk carrying valid, non-conflicting usage. Call IDs are
owned by their tool-call index and cannot be reused by another parallel call.

For Responses, `_stream_response()` records the response ID from response lifecycle
events and collects completed `function_call` output items. It accepts providers
that expose the completed item through `response.output_item.done`, while retaining
the initial item metadata needed to associate deltas. It maintains both
`output_index -> item_id` and `item_id -> output_index` identity maps and rejects
changes or reuse in either direction. Every accumulated function-call index must
receive exactly one valid done event. Duplicate done events and partial calls are
never executable. Only `response.completed` is a successful terminal lifecycle
event; failed, incomplete, explicit error, missing, or conflicting terminal states
are unsupported. Every terminal lifecycle event latches the stream, so any later
provider event is malformed. Function call IDs are also owned by output index and
cannot be reused across distinct parallel items. After valid completion, the method
returns `ToolCallResponse(calls, ResponsesContinuation(response_id), usage)` when
calls exist, or the aggregated final text otherwise. Parallel calls preserve
ascending output-index order.

Malformed, incomplete, or mismatched stream data raises the existing unsupported
response `RuntimeError`; it never triggers an automatic second model request.

## Agent Loop

`Agent.run_stream()` mirrors the control flow of `Agent.run()`:

1. Start one model trace span for each streamed model call.
2. Call `gateway.stream()` with the current continuation and tool outputs.
3. Finish that model span on every success or error path.
4. Return when the outcome is `FinalResponse`.
5. Execute every `ToolCallResponse` call through the existing registry and
   confirmation callback, recording tool spans exactly as non-streaming mode does.
6. Continue with the returned continuation and serialized tool outputs, still in
   streaming mode.
7. Raise `AgentLoopLimitError` if the existing model-call budget is exhausted.

The `StreamingToolCallUnsupported` exception and fallback path are removed because
tool calls become a normal stream outcome.

## Output Semantics

Only assistant text received from stream deltas is sent to `on_text`. Tool-call JSON,
tool outputs, and internal continuation data are not printed. If a provider emits
assistant text before a tool call, that text remains visible and is not replayed.
The final returned string is the text from the terminal `FinalResponse`, matching
what chat persistence already stores.

## Testing

Offline tests use fake SDK events and cover:

- Chat tool-call fields split across multiple chunks.
- Multiple parallel Chat tool calls interleaved by index.
- Chat terminal-state validation and final empty-choice usage chunks.
- Terminal latching after Chat finish reasons and Responses lifecycle events.
- Responses function-call completion and response ID capture.
- Responses terminal lifecycle, reverse identity, and duplicate completion checks.
- Cross-index call-ID uniqueness with valid distinct-ID parallel calls preserved.
- Malformed or incomplete streamed tool calls.
- Real second streamed requests for both provider modes, proving continuation data
  is included exactly once without replaying message history.
- A streamed Agent tool loop that executes tools and continues streaming without
  calling the non-streaming gateway.
- Model and tool trace spans on success and failure.
- Existing direct text streaming and CLI output behavior as regressions.

Verification runs the focused OpenAI and Agent tests first, then the complete
`uv run pytest` suite and CLI help smoke checks.

These terminal-state, usage, identity, and completion rules are mandatory final
review corrections to the approved design, not optional follow-up work.
