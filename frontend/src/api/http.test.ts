import { afterEach, describe, expect, it, vi } from "vitest"

import {
  ApiError,
  deleteSession,
  loadBootstrap,
  loadSession,
} from "./http"

const sessionId = "8d7af6dc-32c8-4b2b-9890-c3caa938390c"

const bootstrap = {
  workspace_name: "workspace",
  workspace_path: "C:/workspace",
  model: "gpt-5",
  api_mode: "responses" as const,
  busy: false,
  conversations: [
    {
      id: sessionId,
      updated_at: "2026-08-01T12:00:00+00:00",
      message_count: 2,
      preview: "Hello",
    },
  ],
}

const storedSession = {
  id: sessionId,
  created_at: "2026-08-01T11:00:00+00:00",
  updated_at: "2026-08-01T12:00:00+00:00",
  messages: [
    { role: "user" as const, content: "Hello" },
    { role: "assistant" as const, content: "Hi" },
  ],
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("HTTP session client", () => {
  it("loads bootstrap data through the authenticated same-origin route", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(bootstrap))
    vi.stubGlobal("fetch", fetchMock)

    await expect(loadBootstrap()).resolves.toEqual(bootstrap)
    expect(fetchMock).toHaveBeenCalledWith("/api/bootstrap", {
      credentials: "same-origin",
    })
  })

  it("loads a session without sending workspace state", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(storedSession))
    vi.stubGlobal("fetch", fetchMock)

    await expect(loadSession(sessionId)).resolves.toEqual(storedSession)
    expect(fetchMock).toHaveBeenCalledWith(`/api/sessions/${sessionId}`, {
      credentials: "same-origin",
    })
  })

  it("deletes one saved session without a JSON payload", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }))
    vi.stubGlobal("fetch", fetchMock)

    await expect(deleteSession(sessionId)).resolves.toBeUndefined()
    expect(fetchMock).toHaveBeenCalledWith(`/api/sessions/${sessionId}`, {
      method: "DELETE",
      credentials: "same-origin",
    })
  })

  it("returns the server's safe error fields when a request fails", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            code: "server_busy",
            message: "Another turn is already running.",
            retryable: true,
          },
          409,
        ),
      ),
    )

    await expect(loadBootstrap()).rejects.toEqual(
      new ApiError("server_busy", "Another turn is already running.", true, 409),
    )
  })

  it("rejects non-canonical session IDs before issuing HTTP requests", async () => {
    const fetchMock = vi.fn()
    vi.stubGlobal("fetch", fetchMock)

    await expect(loadSession("../?/#")).rejects.toMatchObject({
      code: "invalid_conversation_id",
      retryable: false,
    })
    await expect(deleteSession("8D7AF6DC-32C8-4B2B-9890-C3CAA938390C")).rejects.toMatchObject({
      code: "invalid_conversation_id",
      retryable: false,
    })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it("accepts every lowercase canonical UUID the backend accepts", async () => {
    const nonRfcVariant = "00000000-0000-0000-0000-000000000000"
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(storedSession))
    vi.stubGlobal("fetch", fetchMock)

    await loadSession(nonRfcVariant)

    expect(fetchMock).toHaveBeenCalledWith(`/api/sessions/${nonRfcVariant}`, {
      credentials: "same-origin",
    })
  })
})
