import { describe, expect, it, vi } from "vitest"

import type { ServerEvent } from "../api/protocol"
import { useChat, type ChatSocket } from "./useChat"

const firstSessionId = "8d7af6dc-32c8-4b2b-9890-c3caa938390c"
const turnId = "7b4c48ea-ddba-4c41-a755-b5222e4d122a"
const approvalId = "3e1a2ebb-95e2-4b9a-86de-849555043088"

class FakeWebSocket implements ChatSocket {
  readyState: number
  sent: string[] = []
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null

  constructor(readyState = 1) {
    this.readyState = readyState
  }

  send(data: string): void {
    this.sent.push(data)
  }

  close(): void {
    this.readyState = 3
    this.onclose?.({} as CloseEvent)
  }

  receive(event: ServerEvent): void {
    this.onmessage?.({ data: JSON.stringify(event) } as MessageEvent<string>)
  }

  closeFromTransport(): void {
    this.readyState = 3
    this.onclose?.({} as CloseEvent)
  }

  open(): void {
    this.readyState = 1
    this.onopen?.({} as Event)
  }

  receiveRaw(payload: unknown): void {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>)
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => {
    resolve = next
  })
  return { promise, resolve }
}

function sentEvents(socket: FakeWebSocket): unknown[] {
  return socket.sent.map((message) => JSON.parse(message))
}

describe("useChat", () => {
  it("removes its unload listener before disconnecting on dispose", () => {
    const addEventListener = vi.spyOn(window, "addEventListener")
    const removeEventListener = vi.spyOn(window, "removeEventListener")
    const socket = new FakeWebSocket()
    const close = vi.spyOn(socket, "close")

    try {
      const chat = useChat({ socketFactory: () => socket })
      const unloadHandler = addEventListener.mock.calls.find(
        ([type]) => type === "beforeunload",
      )?.[1]
      expect(unloadHandler).toBeDefined()

      chat.startTurn("hello")
      socket.receive({ type: "turn.accepted", turn_id: turnId, session_id: firstSessionId })
      chat.dispose()

      expect(removeEventListener).toHaveBeenCalledWith("beforeunload", unloadHandler)
      expect(sentEvents(socket)).toContainEqual({ type: "turn.cancel", turn_id: turnId })
      expect(close).toHaveBeenCalledOnce()
    } finally {
      addEventListener.mockRestore()
      removeEventListener.mockRestore()
    }
  })

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

  it("does not start a connecting turn after Stop", () => {
    const socket = new FakeWebSocket(0)
    const chat = useChat({ socketFactory: () => socket })

    chat.startTurn("hello")
    chat.cancelTurn()
    socket.open()

    expect(sentEvents(socket)).toEqual([])
    expect(chat.state.value).toBe("cancelled")
  })

  it("sends one cancellation after an already-started turn is accepted", () => {
    const socket = new FakeWebSocket()
    const chat = useChat({ socketFactory: () => socket })

    chat.startTurn("hello")
    chat.cancelTurn()
    socket.receive({ type: "turn.accepted", turn_id: turnId, session_id: firstSessionId })

    expect(sentEvents(socket)).toEqual([
      { type: "turn.start", prompt: "hello", session_id: null },
      { type: "turn.cancel", turn_id: turnId },
    ])
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

  it("keeps a retryable failure when its socket closes", () => {
    const socket = new FakeWebSocket()
    const chat = useChat({ socketFactory: () => socket })

    chat.startTurn("hello")
    socket.receive({ type: "turn.accepted", turn_id: turnId, session_id: firstSessionId })
    socket.receive({
      type: "turn.failed",
      turn_id: turnId,
      code: "provider_unavailable",
      message: "The provider is unavailable.",
      retryable: true,
    })
    socket.closeFromTransport()

    expect(chat.state.value).toBe("failed")
    expect(chat.retry()).toBe(true)
  })

  it("keeps an active turn running after a protocol error", () => {
    const socket = new FakeWebSocket()
    const chat = useChat({ socketFactory: () => socket })

    chat.startTurn("hello")
    socket.receive({ type: "turn.accepted", turn_id: turnId, session_id: firstSessionId })
    socket.receive({
      type: "protocol.error",
      code: "protocol_error",
      message: "Invalid WebSocket event.",
    })
    socket.receive({ type: "assistant.delta", turn_id: turnId, delta: "Hi" })

    expect(chat.state.value).toBe("running")
    expect(chat.protocolError.value).toBe("Invalid WebSocket event.")
    expect(chat.messages.value.at(-1)).toMatchObject({ content: "Hi", status: "running" })
  })

  it("ignores malformed terminal events before changing state", () => {
    const socket = new FakeWebSocket()
    const chat = useChat({ socketFactory: () => socket })

    chat.startTurn("hello")
    socket.receive({ type: "turn.accepted", turn_id: turnId, session_id: firstSessionId })
    socket.receiveRaw({
      type: "turn.completed",
      turn_id: "not-a-uuid",
      assistant_message: "Hi",
      conversation: {
        id: firstSessionId,
        updated_at: "2026-08-01T13:00:00+00:00",
        message_count: 1.5,
        preview: "hello",
      },
    })

    expect(chat.state.value).toBe("running")
    expect(chat.messages.value.at(-1)).toMatchObject({ persisted: false, status: "running" })
  })

  it("refuses to change sessions while a turn is active", async () => {
    const socket = new FakeWebSocket()
    const loader = async () => ({
      id: firstSessionId,
      created_at: "2026-08-01T11:00:00+00:00",
      updated_at: "2026-08-01T12:00:00+00:00",
      messages: [{ role: "user" as const, content: "other" }],
    })
    const chat = useChat({ socketFactory: () => socket, sessionLoader: loader })
    chat.startTurn("hello")

    await expect(chat.selectSession(firstSessionId)).resolves.toBe(false)
    expect(chat.messages.value[0]).toMatchObject({ content: "hello", persisted: false })
  })

  it("keeps the newest asynchronous session selection when an older load arrives late", async () => {
    const pendingA = deferred<{
      id: string
      created_at: string
      updated_at: string
      messages: { role: "user"; content: string }[]
    }>()
    const pendingB = deferred<{
      id: string
      created_at: string
      updated_at: string
      messages: { role: "user"; content: string }[]
    }>()
    const secondSessionId = "74e94a48-5a15-426f-9cb1-638acd5c7b99"
    const loader = (id: string) => (id === firstSessionId ? pendingA.promise : pendingB.promise)
    const chat = useChat({ sessionLoader: loader })

    const selectA = chat.selectSession(firstSessionId)
    const selectB = chat.selectSession(secondSessionId)
    pendingB.resolve({ id: secondSessionId, created_at: "b", updated_at: "b", messages: [{ role: "user", content: "B" }] })
    await selectB
    pendingA.resolve({ id: firstSessionId, created_at: "a", updated_at: "a", messages: [{ role: "user", content: "A" }] })
    await selectA

    expect(chat.sessionId.value).toBe(secondSessionId)
    expect(chat.messages.value).toEqual([{ role: "user", content: "B", persisted: true }])
  })

  it("invalidates a pending session load without changing the current session", async () => {
    const pending = deferred<{
      id: string
      created_at: string
      updated_at: string
      messages: { role: "user"; content: string }[]
    }>()
    const chat = useChat({ sessionLoader: () => pending.promise })
    chat.setSession({
      id: firstSessionId,
      created_at: "a",
      updated_at: "a",
      messages: [{ role: "user", content: "current" }],
    })

    const selection = chat.selectSession("74e94a48-5a15-426f-9cb1-638acd5c7b99")
    chat.invalidateSessionLoads()
    pending.resolve({
      id: "74e94a48-5a15-426f-9cb1-638acd5c7b99",
      created_at: "b",
      updated_at: "b",
      messages: [{ role: "user", content: "stale" }],
    })

    await expect(selection).resolves.toBe(false)
    expect(chat.sessionId.value).toBe(firstSessionId)
    expect(chat.messages.value).toEqual([
      { role: "user", content: "current", persisted: true },
    ])
  })

  it("ignores late close events from a replaced socket", () => {
    const socketA = new FakeWebSocket()
    const socketB = new FakeWebSocket()
    const sockets = [socketA, socketB]
    const chat = useChat({ socketFactory: () => sockets.shift()! })

    chat.connect()
    socketA.close()
    chat.startTurn("hello")
    socketB.receive({ type: "turn.accepted", turn_id: turnId, session_id: firstSessionId })
    socketA.onclose?.({} as CloseEvent)

    expect(chat.state.value).toBe("running")
    expect(chat.cancelTurn()).toBe(true)
    expect(sentEvents(socketB)).toContainEqual({ type: "turn.cancel", turn_id: turnId })
  })

  it("invalidates a pending session load when a new turn starts", async () => {
    const pending = deferred<{
      id: string
      created_at: string
      updated_at: string
      messages: { role: "user"; content: string }[]
    }>()
    const socket = new FakeWebSocket()
    const chat = useChat({ socketFactory: () => socket, sessionLoader: () => pending.promise })

    const selection = chat.selectSession(firstSessionId)
    chat.startTurn("new")
    socket.receive({ type: "turn.accepted", turn_id: turnId, session_id: firstSessionId })
    socket.receive({
      type: "turn.completed",
      turn_id: turnId,
      assistant_message: "done",
      conversation: { id: firstSessionId, updated_at: "2026-08-01T13:00:00+00:00", message_count: 2, preview: "new" },
    })
    pending.resolve({ id: firstSessionId, created_at: "a", updated_at: "a", messages: [{ role: "user", content: "stale" }] })

    await expect(selection).resolves.toBe(false)
    expect(chat.messages.value).toEqual([
      { role: "user", content: "new", persisted: true },
      { role: "assistant", content: "done", persisted: true },
    ])
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

  it("retries a cancelled prompt without adding cancelled content to saved history", () => {
    const socket = new FakeWebSocket()
    const chat = useChat({ socketFactory: () => socket })

    chat.setSession({
      id: firstSessionId,
      created_at: "2026-08-01T11:00:00+00:00",
      updated_at: "2026-08-01T12:00:00+00:00",
      messages: [{ role: "user", content: "saved" }],
    })
    chat.startTurn("cancelled prompt")
    socket.receive({ type: "turn.accepted", turn_id: turnId, session_id: firstSessionId })
    socket.receive({ type: "assistant.delta", turn_id: turnId, delta: "partial" })
    socket.receive({ type: "turn.cancelled", turn_id: turnId })

    expect(chat.retry()).toBe(true)
    expect(sentEvents(socket)).toEqual([
      { type: "turn.start", prompt: "cancelled prompt", session_id: firstSessionId },
      { type: "turn.start", prompt: "cancelled prompt", session_id: firstSessionId },
    ])
    expect(chat.messages.value).toEqual([
      { role: "user", content: "saved", persisted: true },
      { role: "user", content: "cancelled prompt", persisted: false },
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
