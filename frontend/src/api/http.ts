import type {
  BootstrapResponse,
  StoredConversation,
} from "./protocol"

interface SafeErrorPayload {
  code: string
  message: string
  retryable: boolean
}

export class ApiError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly retryable: boolean,
    public readonly status: number,
  ) {
    super(message)
    this.name = "ApiError"
  }
}

const sameOriginOptions: RequestInit = { credentials: "same-origin" }

export async function loadBootstrap(): Promise<BootstrapResponse> {
  return requestJson<BootstrapResponse>("/api/bootstrap", sameOriginOptions)
}

export async function loadSession(id: string): Promise<StoredConversation> {
  return requestJson<StoredConversation>(
    `/api/sessions/${validatedSessionId(id)}`,
    sameOriginOptions,
  )
}

export async function deleteSession(id: string): Promise<void> {
  const response = await fetch(`/api/sessions/${validatedSessionId(id)}`, {
    method: "DELETE",
    credentials: "same-origin",
  })
  if (!response.ok) {
    throw await responseError(response)
  }
}

function validatedSessionId(id: string): string {
  if (!isCanonicalUuid(id)) {
    throw new ApiError(
      "invalid_conversation_id",
      "Conversation ID must be a complete canonical UUID.",
      false,
      400,
    )
  }
  return encodeURIComponent(id)
}

function isCanonicalUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(
    value,
  )
}

async function requestJson<T>(path: string, options: RequestInit): Promise<T> {
  const response = await fetch(path, options)
  if (!response.ok) {
    throw await responseError(response)
  }
  return (await response.json()) as T
}

async function responseError(response: Response): Promise<ApiError> {
  const payload = await response.json().catch(() => null)
  if (isSafeErrorPayload(payload)) {
    return new ApiError(
      payload.code,
      payload.message,
      payload.retryable,
      response.status,
    )
  }
  return new ApiError(
    "request_failed",
    "The request failed safely.",
    response.status >= 500,
    response.status,
  )
}

function isSafeErrorPayload(value: unknown): value is SafeErrorPayload {
  if (typeof value !== "object" || value === null) {
    return false
  }
  const payload = value as Record<string, unknown>
  return (
    typeof payload.code === "string" &&
    typeof payload.message === "string" &&
    typeof payload.retryable === "boolean"
  )
}
