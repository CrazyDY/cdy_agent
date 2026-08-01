export type ApiMode = "responses" | "chat_completions"
export type ApprovalDecision = "deny" | "allow_once" | "allow_always"

export interface ConversationSummary {
  id: string
  updated_at: string
  message_count: number
  preview: string
}

export interface ConversationMessage {
  role: "user" | "assistant"
  content: string
}

export interface StoredConversation {
  id: string
  created_at: string
  updated_at: string
  messages: ConversationMessage[]
}

export interface BootstrapResponse {
  workspace_name: string
  workspace_path: string
  model: string
  api_mode: ApiMode
  busy: boolean
  conversations: ConversationSummary[]
}

export type ClientEvent =
  | { type: "turn.start"; prompt: string; session_id: string | null }
  | { type: "turn.cancel"; turn_id: string }
  | {
      type: "approval.resolve"
      turn_id: string
      approval_id: string
      decision: ApprovalDecision
    }

export interface TurnAccepted {
  type: "turn.accepted"
  turn_id: string
  session_id: string
}

export interface AssistantDelta {
  type: "assistant.delta"
  turn_id: string
  delta: string
}

export interface ToolStatus {
  type: "tool.status"
  turn_id: string
  name: string
  phase: "started" | "finished"
  label: string
}

export interface ApprovalRequired {
  type: "approval.required"
  turn_id: string
  approval_id: string
  description: string
  allow_always: boolean
}

export interface TurnCompleted {
  type: "turn.completed"
  turn_id: string
  assistant_message: string
  conversation: ConversationSummary
}

export interface TurnFailed {
  type: "turn.failed"
  turn_id: string
  code: string
  message: string
  retryable: boolean
}

export interface TurnCancelled {
  type: "turn.cancelled"
  turn_id: string
}

export interface ServerBusy {
  type: "server.busy"
  code: "server_busy"
  message: string
  retryable: true
}

export interface ProtocolError {
  type: "protocol.error"
  code: "protocol_error"
  message: string
}

export type ServerEvent =
  | TurnAccepted
  | AssistantDelta
  | ToolStatus
  | ApprovalRequired
  | TurnCompleted
  | TurnFailed
  | TurnCancelled
  | ServerBusy
  | ProtocolError

export type TerminalServerEvent = TurnCompleted | TurnFailed | TurnCancelled

export function isTerminalEvent(event: ServerEvent): event is TerminalServerEvent {
  return (
    event.type === "turn.completed" ||
    event.type === "turn.failed" ||
    event.type === "turn.cancelled"
  )
}
