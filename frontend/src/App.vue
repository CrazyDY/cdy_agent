<script lang="ts">
import type { useChat } from "./composables/useChat"
import type { deleteSession, loadBootstrap } from "./api/http"

export interface AppServices {
  createChat: () => ReturnType<typeof useChat>
  loadBootstrap: typeof loadBootstrap
  deleteSession: typeof deleteSession
  confirm: (message: string) => boolean
}
</script>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue"

import { ApiError, deleteSession as deleteSavedSession, loadBootstrap as fetchBootstrap } from "./api/http"
import type { ApprovalDecision } from "./api/protocol"
import ChatComposer from "./components/ChatComposer.vue"
import ChatTimeline from "./components/ChatTimeline.vue"
import ConfirmationDialog from "./components/ConfirmationDialog.vue"
import ConversationSidebar from "./components/ConversationSidebar.vue"
import ToolStatus from "./components/ToolStatus.vue"
import { useChat as createChat } from "./composables/useChat"

const props = defineProps<{ services?: AppServices }>()
const defaultServices: AppServices = {
  createChat,
  loadBootstrap: fetchBootstrap,
  deleteSession: deleteSavedSession,
  confirm: (message) => window.confirm(message),
}
const services = props.services ?? defaultServices
const chat = services.createChat()
const loading = ref(true)
const bootstrapError = ref<string | null>(null)
const appError = ref<string | null>(null)
const sidebarOpen = ref(false)

const state = chat.state
const sessionId = chat.sessionId
const messages = chat.messages
const summaries = chat.summaries
const bootstrap = chat.bootstrap
const toolStatus = chat.toolStatus
const approval = chat.approval
const failure = chat.failure
const protocolError = chat.protocolError

const active = computed(() => state.value === "running" || state.value === "awaiting_approval")
const composerDisabled = computed(() => loading.value || state.value !== "idle")
const canRetry = computed(
  () => state.value === "cancelled" || (state.value === "failed" && failure.value?.retryable === true),
)
const activePreview = computed(() => {
  if (sessionId.value === null) {
    return "New conversation"
  }
  return summaries.value.find((summary) => summary.id === sessionId.value)?.preview ?? "Saved conversation"
})
const apiModeLabel = computed(() =>
  bootstrap.value?.api_mode === "chat_completions" ? "Chat Completions API" : "Responses API",
)

onMounted(refreshBootstrap)
onBeforeUnmount(chat.disconnect)

async function refreshBootstrap(): Promise<void> {
  loading.value = true
  bootstrapError.value = null
  try {
    chat.setBootstrap(await services.loadBootstrap())
  } catch (error) {
    bootstrapError.value = safeMessage(error, "CDY Agent could not load this workspace.")
  } finally {
    loading.value = false
  }
}

function newConversation(): void {
  if (active.value) {
    return
  }
  chat.setSession(null)
  appError.value = null
  sidebarOpen.value = false
}

async function selectConversation(id: string): Promise<void> {
  appError.value = null
  if (await chat.selectSession(id)) {
    sidebarOpen.value = false
  } else {
    appError.value = "The conversation could not be loaded."
  }
}

async function removeConversation(id: string): Promise<void> {
  if (active.value || !services.confirm("Delete this saved conversation? This cannot be undone.")) {
    return
  }
  appError.value = null
  try {
    await services.deleteSession(id)
    chat.setSummaries(summaries.value.filter((summary) => summary.id !== id))
    if (sessionId.value === id) {
      chat.setSession(null)
    }
  } catch (error) {
    appError.value = safeMessage(error, "The conversation could not be deleted.")
  }
}

function resolveApproval(decision: ApprovalDecision): void {
  chat.resolveApproval(decision)
}

function safeMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}
</script>

<template>
  <div class="app-shell">
    <button
      v-if="sidebarOpen"
      class="sidebar-backdrop"
      data-test="sidebar-backdrop"
      type="button"
      aria-label="Close conversations"
      @click="sidebarOpen = false"
    ></button>
    <ConversationSidebar
      :conversations="summaries"
      :active-id="sessionId"
      :workspace-name="bootstrap?.workspace_name ?? ''"
      :workspace-path="bootstrap?.workspace_path ?? ''"
      :disabled="active"
      :open="sidebarOpen"
      @new="newConversation"
      @select="selectConversation"
      @delete="removeConversation"
      @close="sidebarOpen = false"
    />

    <main class="main-panel">
      <header class="main-header">
        <button
          class="icon-button sidebar-toggle"
          data-test="sidebar-toggle"
          type="button"
          aria-label="Open conversations"
          aria-controls="conversation-sidebar"
          :aria-expanded="sidebarOpen"
          @click="sidebarOpen = !sidebarOpen"
        >
          ☰
        </button>
        <div class="conversation-heading">
          <p class="eyebrow">Conversation</p>
          <h1>{{ activePreview }}</h1>
        </div>
        <dl v-if="bootstrap" class="runtime-meta" aria-label="Read-only runtime configuration">
          <div>
            <dt>Model</dt>
            <dd>{{ bootstrap.model }}</dd>
          </div>
          <div>
            <dt>API</dt>
            <dd>{{ apiModeLabel }}</dd>
          </div>
        </dl>
      </header>

      <section class="chat-workspace" aria-label="Chat">
        <div v-if="loading" class="center-state" role="status">Loading workspace…</div>
        <div v-else-if="bootstrapError" class="center-state error-state" data-test="bootstrap-error">
          <p>{{ bootstrapError }}</p>
          <button class="button button-secondary" data-test="bootstrap-retry" type="button" @click="refreshBootstrap">Try again</button>
        </div>
        <template v-else>
          <div v-if="appError" class="notice notice-error" data-test="app-error" role="alert">{{ appError }}</div>
          <div v-if="protocolError" class="notice notice-error" role="alert">{{ protocolError }}</div>
          <div v-if="bootstrap?.busy" class="notice" role="status">Another turn was active when this page loaded.</div>
          <ChatTimeline :messages="messages" />
          <div class="status-area" aria-live="polite">
            <ToolStatus v-if="toolStatus && active" :status="toolStatus" />
            <div v-if="failure" class="turn-error" data-test="turn-error" role="alert">{{ failure.message }}</div>
          </div>
          <ChatComposer
            :disabled="composerDisabled"
            :active="active"
            :can-retry="canRetry"
            @send="chat.startTurn"
            @stop="chat.cancelTurn"
            @retry="chat.retry"
          />
        </template>
      </section>
    </main>

    <ConfirmationDialog v-if="approval" :request="approval" @resolve="resolveApproval" />
  </div>
</template>
