<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue"

import type { ApprovalDecision, ApprovalRequired } from "../api/protocol"

defineProps<{ request: ApprovalRequired }>()
const emit = defineEmits<{
  resolve: [decision: ApprovalDecision]
  cancel: []
}>()

const dialog = ref<HTMLElement | null>(null)
let previousFocus: HTMLElement | null = null

onMounted(async () => {
  previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
  await nextTick()
  focusableElements()[0]?.focus()
})

onBeforeUnmount(() => previousFocus?.focus())

function focusableElements(): HTMLButtonElement[] {
  return Array.from(dialog.value?.querySelectorAll<HTMLButtonElement>("button") ?? [])
}

function handleKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape") {
    event.preventDefault()
    emit("cancel")
    return
  }
  if (event.key !== "Tab") {
    return
  }
  const elements = focusableElements()
  if (elements.length === 0) {
    return
  }
  const first = elements[0]
  const last = elements[elements.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  }
}
</script>

<template>
  <div class="dialog-backdrop">
    <section
      ref="dialog"
      class="confirmation-dialog"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="confirmation-title"
      aria-describedby="confirmation-description"
      @keydown="handleKeydown"
    >
      <p class="eyebrow">Action required</p>
      <h2 id="confirmation-title">Confirm tool action</h2>
      <p id="confirmation-description" data-test="confirmation-description">
        {{ request.description }}
      </p>
      <p v-if="request.allow_always" class="approval-note">
        Always allow applies only to this exact prepared command.
      </p>
      <div class="dialog-actions">
        <button class="button button-stop" data-test="stop-turn" type="button" @click="emit('cancel')">
          Stop turn
        </button>
        <button class="button button-quiet" data-test="deny" type="button" @click="emit('resolve', 'deny')">
          Deny
        </button>
        <button class="button button-secondary" data-test="allow-once" type="button" @click="emit('resolve', 'allow_once')">
          Allow once
        </button>
        <button
          v-if="request.allow_always"
          class="button button-primary"
          data-test="allow-always"
          type="button"
          @click="emit('resolve', 'allow_always')"
        >
          Always allow
        </button>
      </div>
    </section>
  </div>
</template>
