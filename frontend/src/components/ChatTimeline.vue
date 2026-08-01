<script setup lang="ts">
import { nextTick, onUpdated, ref } from "vue"

import type { ChatMessage } from "../composables/useChat"
import MessageContent from "./MessageContent.vue"

defineProps<{ messages: ChatMessage[] }>()
const end = ref<HTMLElement | null>(null)

onUpdated(async () => {
  await nextTick()
  if (typeof end.value?.scrollIntoView === "function") {
    end.value.scrollIntoView({ block: "nearest" })
  }
})
</script>

<template>
  <div class="timeline-scroll">
    <div v-if="messages.length === 0" class="empty-state">
      <span class="empty-mark" aria-hidden="true">C</span>
      <p class="eyebrow">Local workspace assistant</p>
      <h2>Start a conversation</h2>
      <p>Ask a question, work with local tools, or resume a saved conversation.</p>
    </div>
    <ol v-else class="timeline" aria-label="Conversation messages">
      <li
        v-for="(message, index) in messages"
        :key="`${index}-${message.role}`"
        class="message-row"
        :class="[`message-${message.role}`, message.status ? `message-${message.status}` : '']"
        data-test="message"
      >
        <article class="message-card">
          <p class="message-label">{{ message.role === "user" ? "You" : "CDY Agent" }}</p>
          <p v-if="message.role === 'user'" class="user-content">{{ message.content }}</p>
          <MessageContent v-else-if="message.content" :content="message.content" />
          <p v-else class="thinking">Thinking<span aria-hidden="true">…</span></p>
          <p v-if="message.status === 'running'" class="message-state" role="status">Streaming…</p>
          <p v-else-if="message.status === 'failed'" class="message-state">Failed · not saved</p>
          <p v-else-if="message.status === 'cancelled'" class="message-state">Cancelled · not saved</p>
        </article>
      </li>
    </ol>
    <span ref="end" aria-hidden="true"></span>
  </div>
</template>
