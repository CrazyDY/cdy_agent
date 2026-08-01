import { mount, flushPromises } from "@vue/test-utils"
import { nextTick } from "vue"
import { describe, expect, it, vi } from "vitest"

import type { BootstrapResponse, ServerEvent, StoredConversation } from "./api/protocol"
import App, { type AppServices } from "./App.vue"
import { useChat, type ChatSocket } from "./composables/useChat"

const sessionId = "8d7af6dc-32c8-4b2b-9890-c3caa938390c"
const turnId = "7b4c48ea-ddba-4c41-a755-b5222e4d122a"

const bootstrap: BootstrapResponse = {
  workspace_name: "cdy_agent",
  workspace_path: "D:/code/cdy_agent",
  model: "gpt-5",
  api_mode: "responses",
  busy: false,
  conversations: [
    {
      id: sessionId,
      updated_at: "2026-08-01T12:00:00+00:00",
      message_count: 2,
      preview: "Saved question",
    },
  ],
}

const storedSession: StoredConversation = {
  id: sessionId,
  created_at: "2026-08-01T11:00:00+00:00",
  updated_at: "2026-08-01T12:00:00+00:00",
  messages: [
    { role: "user", content: "Saved question" },
    { role: "assistant", content: "Saved answer" },
  ],
}

class FakeWebSocket implements ChatSocket {
  readyState = 1
  sent: string[] = []
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  onclose: ((event: CloseEvent) => void) | null = null

  send(data: string): void {
    this.sent.push(data)
  }

  close(): void {
    this.readyState = 3
  }

  receive(event: ServerEvent): void {
    this.onmessage?.({ data: JSON.stringify(event) } as MessageEvent<string>)
  }
}

function mountApp(options: {
  bootstrapLoader?: AppServices["loadBootstrap"]
  sessionLoader?: (id: string) => Promise<StoredConversation>
  sessionDeleter?: AppServices["deleteSession"]
  confirm?: AppServices["confirm"]
} = {}) {
  const socket = new FakeWebSocket()
  const chat = useChat({
    socketFactory: () => socket,
    sessionLoader: options.sessionLoader ?? (async () => storedSession),
  })
  const services: AppServices = {
    createChat: () => chat,
    loadBootstrap: options.bootstrapLoader ?? (async () => bootstrap),
    deleteSession: options.sessionDeleter ?? (async () => undefined),
    confirm: options.confirm ?? (() => true),
  }
  return { wrapper: mount(App, { props: { services } }), socket, chat }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((next) => {
    resolve = next
  })
  return { promise, resolve }
}

describe("App", () => {
  it("loads bootstrap metadata and starts a new conversation", async () => {
    const pending = deferred<BootstrapResponse>()
    const { wrapper, chat } = mountApp({ bootstrapLoader: () => pending.promise })

    expect(wrapper.get('[role="status"]').text()).toContain("Loading")
    pending.resolve(bootstrap)
    await flushPromises()

    expect(wrapper.text()).toContain("cdy_agent")
    expect(wrapper.text()).toContain("gpt-5")
    expect(wrapper.text()).toContain("Responses API")
    expect(wrapper.get("[data-test=new-conversation]").attributes("disabled")).toBeUndefined()

    chat.setSession(storedSession)
    await nextTick()
    await wrapper.get("[data-test=new-conversation]").trigger("click")
    expect(chat.sessionId.value).toBeNull()
    expect(wrapper.text()).toContain("Start a conversation")
  })

  it("resumes a saved conversation and preserves message order", async () => {
    const { wrapper } = mountApp()
    await flushPromises()

    await wrapper.get(`[data-session-id="${sessionId}"]`).trigger("click")
    await flushPromises()

    const messages = wrapper.findAll(".user-content, .message-content")
    expect(messages.map((message) => message.text())).toEqual([
      "Saved question",
      "Saved answer",
    ])
  })

  it("disables the composer and exposes Stop while running", async () => {
    const { wrapper, socket } = mountApp()
    await flushPromises()

    await wrapper.get("textarea").setValue("Explain the loop")
    await wrapper.get("[data-test=composer-form]").trigger("submit")

    expect(wrapper.get("textarea").attributes("disabled")).toBeDefined()
    expect(wrapper.get('[data-test="stop"]').text()).toBe("Stop")
    expect(JSON.parse(socket.sent[0])).toEqual({
      type: "turn.start",
      prompt: "Explain the loop",
      session_id: null,
    })

    socket.receive({ type: "turn.accepted", turn_id: turnId, session_id: sessionId })
    await wrapper.get('[data-test="stop"]').trigger("click")
    expect(JSON.parse(socket.sent[1])).toEqual({ type: "turn.cancel", turn_id: turnId })
  })

  it("shows generic tool status and Retry for a failed turn", async () => {
    const { wrapper, socket } = mountApp()
    await flushPromises()
    await wrapper.get("textarea").setValue("Try this")
    await wrapper.get("[data-test=composer-form]").trigger("submit")
    socket.receive({ type: "turn.accepted", turn_id: turnId, session_id: sessionId })
    socket.receive({
      type: "tool.status",
      turn_id: turnId,
      name: "shell",
      phase: "started",
      label: "Running command",
    })
    await nextTick()

    expect(wrapper.get("[data-test=tool-status]").text()).toContain("Running command")
    expect(wrapper.get("[data-test=tool-status]").text()).not.toContain("shell")

    socket.receive({
      type: "turn.failed",
      turn_id: turnId,
      code: "provider_unavailable",
      message: "The provider is unavailable.",
      retryable: true,
    })
    await nextTick()
    expect(wrapper.get("[data-test=turn-error]").text()).toContain(
      "The provider is unavailable.",
    )
    await wrapper.get("[data-test=retry]").trigger("click")
    expect(socket.sent).toHaveLength(2)
  })

  it("offers Retry after a cancelled turn", async () => {
    const { wrapper, socket } = mountApp()
    await flushPromises()
    await wrapper.get("textarea").setValue("Try this")
    await wrapper.get("[data-test=composer-form]").trigger("submit")
    socket.receive({ type: "turn.accepted", turn_id: turnId, session_id: sessionId })
    socket.receive({ type: "turn.cancelled", turn_id: turnId })
    await nextTick()

    expect(wrapper.get("[data-test=retry]").text()).toBe("Retry")
  })

  it("confirms deletion, refreshes the sidebar, and reports safe failures", async () => {
    const deleteSession = vi
      .fn<AppServices["deleteSession"]>()
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error("private detail"))
    const confirm = vi.fn(() => true)
    const { wrapper } = mountApp({ sessionDeleter: deleteSession, confirm })
    await flushPromises()

    await wrapper.get(`[data-delete-session="${sessionId}"]`).trigger("click")
    await flushPromises()
    expect(confirm).toHaveBeenCalledOnce()
    expect(deleteSession).toHaveBeenCalledWith(sessionId)
    expect(wrapper.find(`[data-session-id="${sessionId}"]`).exists()).toBe(false)

    const replacement = { ...bootstrap.conversations[0], id: "74e94a48-5a15-426f-9cb1-638acd5c7b99" }
    const services = (wrapper.props("services") as AppServices)
    services.createChat().setSummaries([replacement])
    await nextTick()
    await wrapper.get(`[data-delete-session="${replacement.id}"]`).trigger("click")
    await flushPromises()
    expect(wrapper.get("[data-test=app-error]").text()).toBe(
      "The conversation could not be deleted.",
    )
  })

  it("opens and closes the responsive conversation drawer", async () => {
    const { wrapper } = mountApp()
    await flushPromises()
    const toggle = wrapper.get("[data-test=sidebar-toggle]")

    expect(toggle.attributes("aria-expanded")).toBe("false")
    await toggle.trigger("click")
    expect(toggle.attributes("aria-expanded")).toBe("true")
    expect(wrapper.get("#conversation-sidebar").classes()).toContain("is-open")
    await wrapper.get("[data-test=sidebar-backdrop]").trigger("click")
    expect(toggle.attributes("aria-expanded")).toBe("false")
  })

  it("offers a safe retry when bootstrap fails", async () => {
    const loadBootstrap = vi
      .fn<AppServices["loadBootstrap"]>()
      .mockRejectedValueOnce(new Error("secret provider detail"))
      .mockResolvedValueOnce(bootstrap)
    const { wrapper } = mountApp({ bootstrapLoader: loadBootstrap })
    await flushPromises()

    expect(wrapper.get("[data-test=bootstrap-error]").text()).not.toContain("secret")
    await wrapper.get("[data-test=bootstrap-retry]").trigger("click")
    await flushPromises()
    expect(wrapper.text()).toContain("D:/code/cdy_agent")
  })
})
