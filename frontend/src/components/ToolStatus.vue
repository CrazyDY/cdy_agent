<script setup lang="ts">
import type { ToolDisplayStatus } from "../composables/useChat"

defineProps<{ status: ToolDisplayStatus }>()
</script>

<template>
  <div class="tool-status" data-test="tool-status" role="status" aria-live="polite">
    <span class="status-pulse" aria-hidden="true"></span>
    <span>{{ status.label }}</span>
    <span v-if="status.phase === 'finished'" class="status-complete" aria-hidden="true">✓</span>
  </div>
</template>
