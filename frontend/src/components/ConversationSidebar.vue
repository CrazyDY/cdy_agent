<script setup lang="ts">
import { computed } from "vue"

import type { ConversationSummary } from "../api/protocol"

const props = defineProps<{
  conversations: ConversationSummary[]
  activeId: string | null
  workspaceName: string
  workspacePath: string
  disabled: boolean
  open: boolean
}>()
const emit = defineEmits<{
  new: []
  select: [id: string]
  delete: [id: string]
  close: []
}>()

const groupedConversations = computed(() => {
  const groups = new Map<string, ConversationSummary[]>()
  for (const conversation of props.conversations) {
    const date = new Date(conversation.updated_at)
    const label = Number.isNaN(date.getTime())
      ? "Saved"
      : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date)
    groups.set(label, [...(groups.get(label) ?? []), conversation])
  }
  return Array.from(groups, ([label, conversations]) => ({ label, conversations }))
})
</script>

<template>
  <aside
    id="conversation-sidebar"
    class="sidebar"
    :class="{ 'is-open': open }"
    aria-label="Saved conversations"
  >
    <div class="brand-row">
      <div class="brand-mark" aria-hidden="true">C</div>
      <div>
        <p class="brand-name">CDY Agent</p>
        <p class="brand-subtitle">Local assistant</p>
      </div>
      <button class="icon-button sidebar-close" type="button" aria-label="Close conversations" @click="emit('close')">×</button>
    </div>

    <button class="new-conversation" data-test="new-conversation" type="button" :disabled="disabled" @click="emit('new')">
      <span aria-hidden="true">＋</span>
      New conversation
    </button>

    <nav class="conversation-nav" aria-label="Conversation history">
      <p v-if="conversations.length === 0" class="sidebar-empty">Completed conversations will appear here.</p>
      <section v-for="group in groupedConversations" :key="group.label" class="conversation-group">
        <h2>{{ group.label }}</h2>
        <ul>
          <li v-for="conversation in group.conversations" :key="conversation.id" class="conversation-item" :class="{ active: activeId === conversation.id }">
            <button
              class="conversation-select"
              type="button"
              :data-session-id="conversation.id"
              :disabled="disabled"
              :aria-current="activeId === conversation.id ? 'page' : undefined"
              @click="emit('select', conversation.id)"
            >
              <span>{{ conversation.preview }}</span>
              <small>{{ conversation.message_count }} messages</small>
            </button>
            <button
              class="conversation-delete"
              type="button"
              :data-delete-session="conversation.id"
              :disabled="disabled"
              :aria-label="`Delete ${conversation.preview}`"
              @click="emit('delete', conversation.id)"
            >
              ×
            </button>
          </li>
        </ul>
      </section>
    </nav>

    <div class="workspace-card">
      <span class="workspace-dot" aria-hidden="true"></span>
      <div>
        <p>{{ workspaceName || "Workspace" }}</p>
        <small :title="workspacePath">{{ workspacePath || "Loading…" }}</small>
      </div>
    </div>
  </aside>
</template>
