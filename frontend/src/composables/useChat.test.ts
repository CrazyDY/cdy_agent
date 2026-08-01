import { describe, expect, it } from "vitest"

import type { ServerEvent } from "../api/protocol"
import { useChat, type ChatSocket } from "./useChat"

const firstSessionId = "8d7af6dc-32c8-4b2b-9890-c3caa938390c"
const turnId = "7b4c48ea-ddba-4c41-a755-b5222e4d122a"
const approvalId = "3e1a2ebb-95e2-4b9a-86de-849555043088"

class FakeWebSocket implements ChatSocket {
  readyState = 1
  sent: string[] = []
  onopen: (() => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  onclose: (() => void) | null = null

  send(data: string): void {
    this.sent.push(data)
  }

  close(): void {
    this.readyState = 3
    this.onclose?.()
  }

  receive(event: ServerEvent): void {
    this.onmessage?.({ data: JSON.stringify(event) } as MessageEvent<string>)
  }

  closeFromTransport(): void {
    this.readyState = 3
    this.onclose?.()
  }
}

function sentEvents(socket: FakeWebSocket): unknown[] {
  return socket.sent.map((message) => JSON.parse(message))
}

describe("useChat", () => {
  it("applies assistant deltas in protocol order without completing the turn", () => {
    const socket = new FakeWebSocket()
    const chat = useChat({ socketFactory: () => socket })

    chat.startTurn("hello")
    socket.receive({ type: "turn.accepted", turn_id: turnId, session_id: firstSessionId })
    socket.receive({ type: "assistant.delta", turn_id: turnId, delta: "Hi" })
    socket.receive({ type: "assistant.delta", turn_id: turnId, delta: " there" })

    expect(chat.state.value).toBe("running")
    expect(chat.messages.value).toMatchObject([
      { role: "user", content: "hello", persisted: false },
      { role: "assistant", content: "Hi there", persisted: false, status: "running" },
    ])
  })

  it("records concise tool status and conditionally permits always-allow", () => {
    const socket = new FakeWebSocket()
    const chat = useChat({ socketFactory: () => socket })

    chat.startTurn("hello")
    socket.receive({ type: "turn.accepted", turn_id: turnId, session_id: firstSessionId })
    socket.receive({
      type: "tool.status",
      turn_id: turnId,
      name: "shell",
      phase: "started",
      label: "Running command",
    })
    socket.receive({
      type: "approval.required",
      turn_id: turnId,
      approval_id: approvalId,
      description: "Run a command?",
      allow_always: false,
    })

    expect(chat.toolStatus.value).toEqual({
      name: "shell",
      phase: "started",
      label: "Running command",
    })
    expect(chat.state.value).toBe("awaiting_approval")
    expect(chat.resolveApproval("allow_always")).toBe(false)
    expect(chat.resolveApproval("allow_once")).toBe(true)
    expect(sentEvents(socket)).toContainEqual({
      type: "approval.resolve",
      turn_id: turnId,
      approval_id: approvalId,
      decision: "allow_once",
    })
  })

  it("waits for the terminal event after Stop", () => {
    const socket = new FakeWebSocket()
    const chat = useChat({ socketFactory: () => socket })

    chat.startTurn("hello")
    socket.receive({ type: "turn.accepted", turn_id: turnId, session_id: firstSessionId })
    chat.cancelTurn()

    expect(chat.state.value).toBe("running")
    expect(sentEvents(socket)).toContainEqual({ type: "turn.cancel", turn_id: turnId })

    socket.receive({ type: "turn.cancelled", turn_id: turnId })
    expect(chat.state.value).toBe("cancelled")
    expect(chat.messages.value.at(-1)).toMatchObject({ status: "cancelled" })
  })

  it("marks an in-flight turn cancelled when the transport disconnects", () => {
    const socket = new FakeWebSocket()
    const chat = useChat({ socketFactory: () => socket })

    chat.startTurn("hello")
    socket.receive({ type: "turn.accepted", turn_id: turnId, session_id: firstSessionId })
    socket.closeFromTransport()

    expect(chat.state.value).toBe("cancelled")
    expect(chat.messages.value.at(-1)).toMatchObject({ status: "cancelled" })
  })

  it("keeps failed local content out of the retry turn", () => {
    const socket = new FakeWebSocket()
    const chat = useChat({ socketFactory: () => socket })

    chat.setSession({
      id: firstSessionId,
      created_at: "2026-08-01T11:00:00+00:00",
      updated_at: "2026-08-01T12:00:00+00:00",
      messages: [{ role: "user", content: "saved" }],
    })
    chat.startTurn("failed prompt")
    socket.receive({ type: "turn.accepted", turn_id: turnId, session_id: firstSessionId })
    socket.receive({ type: "assistant.delta", turn_id: turnId, delta: "partial" })
    socket.receive({
      type: "turn.failed",
      turn_id: turnId,
      code: "provider_unavailable",
      message: "The provider is unavailable.",
      retryable: true,
    })

    expect(chat.state.value).toBe("failed")
    expect(chat.retry()).toBe(true)
    expect(sentEvents(socket)).toEqual([
      { type: "turn.start", prompt: "failed prompt", session_id: firstSessionId },
      { type: "turn.start", prompt: "failed prompt", session_id: firstSessionId },
    ])
    expect(chat.messages.value).toEqual([
      { role: "user", content: "saved", persisted: true },
      { role: "user", content: "failed prompt", persisted: false },
      { role: "assistant", content: "", persisted: false, status: "running" },
    ])
  })

  it("refreshes the sidebar summary only after persistence completes", () => {
    const socket = new FakeWebSocket()
    const chat = useChat({ socketFactory: () => socket })
    chat.setSummaries([
      {
        id: firstSessionId,
        updated_at: "2026-08-01T12:00:00+00:00",
        message_count: 2,
        preview: "Older",
      },
    ])

    chat.startTurn("hello")
    socket.receive({ type: "turn.accepted", turn_id: turnId, session_id: firstSessionId })
    socket.receive({ type: "assistant.delta", turn_id: turnId, delta: "Hi" })
    expect(chat.summaries.value[0]?.preview).toBe("Older")

    socket.receive({
      type: "turn.completed",
      turn_id: turnId,
      assistant_message: "Hi",
      conversation: {
        id: firstSessionId,
        updated_at: "2026-08-01T13:00:00+00:00",
        message_count: 4,
        preview: "hello",
      },
    })

    expect(chat.state.value).toBe("idle")
    expect(chat.summaries.value[0]).toMatchObject({ preview: "hello", message_count: 4 })
    expect(chat.messages.value).toEqual([
      { role: "user", content: "hello", persisted: true },
      { role: "assistant", content: "Hi", persisted: true },
    ])
  })
})
