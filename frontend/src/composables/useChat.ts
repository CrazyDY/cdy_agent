import { computed, ref } from "vue"

import type {
  ApprovalDecision,
  ApprovalRequired,
  BootstrapResponse,
  ClientEvent,
  ConversationMessage,
  ConversationSummary,
  ServerEvent,
  StoredConversation,
  TurnCompleted,
  TurnFailed,
} from "../api/protocol"
import { loadSession } from "../api/http"

export type ChatState =
  | "idle"
  | "running"
  | "awaiting_approval"
  | "failed"
  | "cancelled"

export interface ChatMessage extends ConversationMessage {
  persisted: boolean
  status?: "running" | "failed" | "cancelled"
}

export interface ToolDisplayStatus {
  name: string
  phase: "started" | "finished"
  label: string
}

export interface ChatSocket {
  readyState: number
  onopen: ((event: Event) => void) | null
  onmessage: ((event: MessageEvent<string>) => void) | null
  onclose: ((event: CloseEvent) => void) | null
  send(data: string): void
  close(): void
}

export interface UseChatOptions {
  socketFactory?: (url: string) => ChatSocket
  sessionLoader?: (id: string) => Promise<StoredConversation>
}

interface TransientTurn {
  prompt: string
  reply: string
  turnId: string | null
  status: Exclude<ChatState, "idle" | "awaiting_approval">
  startSent: boolean
  cancelRequested: boolean
  cancelSent: boolean
}

interface ChatFailure {
  code: string
  message: string
  retryable: boolean
}

const OPEN = 1

export function useChat(options: UseChatOptions = {}) {
  const state = ref<ChatState>("idle")
  const sessionId = ref<string | null>(null)
  const persistedMessages = ref<ChatMessage[]>([])
  const transientTurn = ref<TransientTurn | null>(null)
  const summaries = ref<ConversationSummary[]>([])
  const bootstrap = ref<BootstrapResponse | null>(null)
  const toolStatus = ref<ToolDisplayStatus | null>(null)
  const approval = ref<ApprovalRequired | null>(null)
  const failure = ref<ChatFailure | null>(null)
  const protocolError = ref<string | null>(null)
  let socket: ChatSocket | null = null
  let sessionRequestGeneration = 0
  let disposed = false

  const messages = computed<ChatMessage[]>(() => {
    const saved = persistedMessages.value
    const transient = transientTurn.value
    if (transient === null) {
      return saved
    }
    return [
      ...saved,
      { role: "user", content: transient.prompt, persisted: false },
      {
        role: "assistant",
        content: transient.reply,
        persisted: false,
        status: transient.status,
      },
    ]
  })

  function connect(): void {
    if (disposed) {
      return
    }
    if (socket !== null && socket.readyState !== 3) {
      return
    }
    const candidate = (options.socketFactory ?? defaultSocketFactory)(socketUrl())
    socket = candidate
    candidate.onopen = () => {
      if (disposed || socket !== candidate) {
        return
      }
      sendStartIfReady()
    }
    candidate.onmessage = (event) => {
      if (disposed || socket !== candidate) {
        return
      }
      const serverEvent = parseServerEvent(event.data)
      if (serverEvent !== null) {
        applyServerEvent(serverEvent)
      }
    }
    candidate.onclose = () => {
      if (socket !== candidate) {
        return
      }
      socket = null
      markTransportCancelled()
    }
  }

  function startTurn(prompt: string): boolean {
    if (disposed || state.value !== "idle" || !prompt.trim()) {
      return false
    }
    sessionRequestGeneration += 1
    transientTurn.value = {
      prompt,
      reply: "",
      turnId: null,
      status: "running",
      startSent: false,
      cancelRequested: false,
      cancelSent: false,
    }
    failure.value = null
    toolStatus.value = null
    approval.value = null
    protocolError.value = null
    state.value = "running"
    connect()
    sendStartIfReady()
    return true
  }

  function cancelTurn(): boolean {
    const turn = transientTurn.value
    if (turn === null || (state.value !== "running" && state.value !== "awaiting_approval")) {
      return false
    }
    approval.value = null
    turn.cancelRequested = true
    if (!turn.startSent) {
      cancelActiveTurn()
      return true
    }
    state.value = "running"
    sendCancelIfReady(turn)
    return true
  }

  function resolveApproval(decision: ApprovalDecision): boolean {
    const pending = approval.value
    const turn = transientTurn.value
    if (
      state.value !== "awaiting_approval" ||
      pending === null ||
      turn === null ||
      turn.turnId === null ||
      (decision === "allow_always" && !pending.allow_always)
    ) {
      return false
    }
    send({
      type: "approval.resolve",
      turn_id: turn.turnId,
      approval_id: pending.approval_id,
      decision,
    })
    approval.value = null
    state.value = "running"
    return true
  }

  function retry(): boolean {
    const failed = transientTurn.value
    const canRetry =
      state.value === "cancelled" ||
      (state.value === "failed" && failure.value?.retryable === true)
    if (!canRetry || failed === null) {
      return false
    }
    transientTurn.value = null
    state.value = "idle"
    return startTurn(failed.prompt)
  }

  function disconnect(): void {
    if (state.value === "running" || state.value === "awaiting_approval") {
      cancelTurn()
    }
    socket?.close()
  }

  function dispose(): void {
    if (disposed) {
      return
    }
    disposed = true
    window.removeEventListener("beforeunload", disconnect)
    invalidateSessionLoads()
    disconnect()
  }

  function setBootstrap(value: BootstrapResponse): void {
    bootstrap.value = value
    summaries.value = [...value.conversations]
  }

  function setSession(value: StoredConversation | null): void {
    sessionRequestGeneration += 1
    applySession(value)
  }

  function invalidateSessionLoads(): void {
    sessionRequestGeneration += 1
  }

  async function selectSession(id: string): Promise<boolean> {
    if (disposed || isTurnActive()) {
      return false
    }
    const generation = ++sessionRequestGeneration
    try {
      const session = await (options.sessionLoader ?? loadSession)(id)
      if (
        disposed ||
        generation !== sessionRequestGeneration ||
        isTurnActive()
      ) {
        return false
      }
      applySession(session)
      return true
    } catch {
      return false
    }
  }

  function applySession(value: StoredConversation | null): void {
    sessionId.value = value?.id ?? null
    persistedMessages.value = (value?.messages ?? []).map((message) => ({
      ...message,
      persisted: true,
    }))
    transientTurn.value = null
    approval.value = null
    toolStatus.value = null
    failure.value = null
    protocolError.value = null
    state.value = "idle"
  }

  function setSummaries(value: ConversationSummary[]): void {
    summaries.value = [...value]
  }

  function sendStartIfReady(): void {
    const turn = transientTurn.value
    if (turn === null || turn.startSent || turn.cancelRequested || socket?.readyState !== OPEN) {
      return
    }
    send({ type: "turn.start", prompt: turn.prompt, session_id: sessionId.value })
    turn.startSent = true
  }

  function sendCancelIfReady(turn: TransientTurn): void {
    if (turn.turnId === null || turn.cancelSent) {
      return
    }
    send({ type: "turn.cancel", turn_id: turn.turnId })
    turn.cancelSent = true
  }

  function send(event: ClientEvent): void {
    if (socket?.readyState === OPEN) {
      socket.send(JSON.stringify(event))
    }
  }

  function applyServerEvent(event: ServerEvent): void {
    if (event.type === "server.busy") {
      failActiveTurn(event)
      return
    }
    if (event.type === "protocol.error") {
      protocolError.value = event.message
      return
    }

    const turn = transientTurn.value
    if (turn === null || ("turn_id" in event && event.turn_id !== turn.turnId && turn.turnId !== null)) {
      return
    }
    if (event.type === "turn.accepted") {
      turn.turnId = event.turn_id
      if (turn.cancelRequested) {
        sendCancelIfReady(turn)
      }
      return
    }
    if (turn.turnId === null || event.turn_id !== turn.turnId) {
      return
    }
    if (event.type === "assistant.delta") {
      turn.reply += event.delta
      return
    }
    if (event.type === "tool.status") {
      toolStatus.value = {
        name: event.name,
        phase: event.phase,
        label: event.label,
      }
      return
    }
    if (event.type === "approval.required") {
      approval.value = event
      state.value = "awaiting_approval"
      return
    }
    if (event.type === "turn.completed") {
      completeTurn(event)
      return
    }
    if (event.type === "turn.failed") {
      failActiveTurn(event)
      return
    }
    if (event.type === "turn.cancelled") {
      cancelActiveTurn()
    }
  }

  function completeTurn(event: TurnCompleted): void {
    const turn = transientTurn.value
    if (turn === null) {
      return
    }
    persistedMessages.value = [
      ...persistedMessages.value,
      { role: "user", content: turn.prompt, persisted: true },
      { role: "assistant", content: event.assistant_message, persisted: true },
    ]
    sessionId.value = event.conversation.id
    summaries.value = [
      event.conversation,
      ...summaries.value.filter((summary) => summary.id !== event.conversation.id),
    ]
    transientTurn.value = null
    toolStatus.value = null
    approval.value = null
    failure.value = null
    state.value = "idle"
  }

  function failActiveTurn(event: Pick<TurnFailed, "code" | "message" | "retryable">): void {
    const turn = transientTurn.value
    if (turn === null) {
      return
    }
    turn.status = "failed"
    approval.value = null
    failure.value = event
    state.value = "failed"
  }

  function cancelActiveTurn(): void {
    const turn = transientTurn.value
    if (turn === null) {
      return
    }
    turn.status = "cancelled"
    approval.value = null
    state.value = "cancelled"
  }

  function markTransportCancelled(): void {
    if (isTurnActive()) {
      cancelActiveTurn()
    }
  }

  function isTurnActive(): boolean {
    return state.value === "running" || state.value === "awaiting_approval"
  }

  window.addEventListener("beforeunload", disconnect)

  return {
    state,
    sessionId,
    persistedMessages,
    transientTurn,
    messages,
    summaries,
    bootstrap,
    toolStatus,
    approval,
    failure,
    protocolError,
    connect,
    startTurn,
    cancelTurn,
    resolveApproval,
    retry,
    disconnect,
    dispose,
    setBootstrap,
    setSession,
    invalidateSessionLoads,
    selectSession,
    setSummaries,
  }
}

function defaultSocketFactory(url: string): ChatSocket {
  return new WebSocket(url)
}

function socketUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:"
  return `${scheme}//${window.location.host}/api/turns`
}

function parseServerEvent(payload: string): ServerEvent | null {
  try {
    const value: unknown = JSON.parse(payload)
    if (!isRecord(value) || typeof value.type !== "string") {
      return null
    }
    if (value.type === "turn.completed" && isTurnCompleted(value)) {
      return value
    }
    if (value.type === "turn.failed" && isTurnFailed(value)) {
      return value
    }
    if (value.type === "turn.cancelled" && isTurnCancelled(value)) {
      return { type: "turn.cancelled", turn_id: value.turn_id }
    }
    if (
      value.type === "turn.accepted" &&
      hasCanonicalIds(value, "turn_id", "session_id")
    ) {
      return {
        type: "turn.accepted",
        turn_id: value.turn_id,
        session_id: value.session_id,
      }
    }
    if (
      value.type === "assistant.delta" &&
      hasCanonicalIds(value, "turn_id") &&
      hasNonEmptyStrings(value, "delta")
    ) {
      return { type: "assistant.delta", turn_id: value.turn_id, delta: value.delta }
    }
    if (
      value.type === "tool.status" &&
      hasCanonicalIds(value, "turn_id") &&
      hasNonEmptyStrings(value, "name", "phase", "label") &&
      (value.phase === "started" || value.phase === "finished")
    ) {
      return {
        type: "tool.status",
        turn_id: value.turn_id,
        name: value.name,
        phase: value.phase,
        label: value.label,
      }
    }
    if (
      value.type === "approval.required" &&
      hasCanonicalIds(value, "turn_id", "approval_id") &&
      hasNonEmptyStrings(value, "description") &&
      typeof value.allow_always === "boolean"
    ) {
      return {
        type: "approval.required",
        turn_id: value.turn_id,
        approval_id: value.approval_id,
        description: value.description,
        allow_always: value.allow_always,
      }
    }
    if (
      value.type === "server.busy" &&
      value.code === "server_busy" &&
      hasNonEmptyStrings(value, "message") &&
      value.retryable === true
    ) {
      return {
        type: "server.busy",
        code: "server_busy",
        message: value.message,
        retryable: true,
      }
    }
    if (
      value.type === "protocol.error" &&
      value.code === "protocol_error" &&
      hasNonEmptyStrings(value, "message")
    ) {
      return {
        type: "protocol.error",
        code: "protocol_error",
        message: value.message,
      }
    }
  } catch {
    return null
  }
  return null
}

function isTurnCompleted(value: object): value is TurnCompleted {
  const record = value as Record<string, unknown>
  return (
    hasCanonicalIds(record, "turn_id") &&
    hasNonEmptyStrings(record, "assistant_message") &&
    isConversationSummary(record.conversation)
  )
}

function isTurnFailed(value: object): value is TurnFailed {
  const record = value as Record<string, unknown>
  return (
    hasCanonicalIds(record, "turn_id") &&
    hasNonEmptyStrings(record, "code", "message") &&
    typeof record.retryable === "boolean"
  )
}

function isTurnCancelled(value: Record<string, unknown>): value is { turn_id: string } {
  return hasCanonicalIds(value, "turn_id")
}

function isConversationSummary(value: unknown): value is ConversationSummary {
  return (
    isRecord(value) &&
    hasCanonicalIds(value, "id") &&
    hasNonEmptyStrings(value, "updated_at", "preview") &&
    typeof value.message_count === "number" &&
    Number.isInteger(value.message_count) &&
    value.message_count >= 0
  )
}

function hasStrings<Key extends string>(
  value: Record<string, unknown>,
  ...keys: Key[]
): value is Record<string, unknown> & Record<Key, string> {
  return keys.every((key) => typeof value[key] === "string")
}

function hasNonEmptyStrings<Key extends string>(
  value: Record<string, unknown>,
  ...keys: Key[]
): value is Record<string, unknown> & Record<Key, string> {
  return hasStrings(value, ...keys) && keys.every((key) => value[key].length > 0)
}

function hasCanonicalIds<Key extends string>(
  value: Record<string, unknown>,
  ...keys: Key[]
): value is Record<string, unknown> & Record<Key, string> {
  return hasStrings(value, ...keys) && keys.every((key) => isCanonicalUuid(value[key]))
}

function isCanonicalUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(
    value,
  )
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null
}
