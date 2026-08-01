<script setup lang="ts">
import { ref } from "vue"

const props = defineProps<{
  disabled: boolean
  active: boolean
  canRetry: boolean
}>()
const emit = defineEmits<{
  send: [prompt: string]
  stop: []
  retry: []
}>()
const prompt = ref("")

function submit(): void {
  if (props.disabled || !prompt.value.trim()) {
    return
  }
  emit("send", prompt.value)
  prompt.value = ""
}
</script>

<template>
  <div class="composer-wrap">
    <div v-if="canRetry" class="retry-row">
      <span>The last response was not saved.</span>
      <button class="text-button" data-test="retry" type="button" @click="emit('retry')">Retry</button>
    </div>
    <form class="composer" data-test="composer-form" @submit.prevent="submit">
      <label class="sr-only" for="chat-prompt">Message CDY Agent</label>
      <textarea
        id="chat-prompt"
        v-model="prompt"
        :disabled="disabled"
        rows="2"
        placeholder="Ask CDY Agent…"
      ></textarea>
      <button
        v-if="active"
        class="button button-stop"
        data-test="stop"
        type="button"
        @click="emit('stop')"
      >
        Stop
      </button>
      <button v-else class="button button-primary send-button" type="submit" :disabled="disabled || !prompt.trim()">
        Send
      </button>
    </form>
    <p class="composer-hint">Enter a message. Tool actions may ask for confirmation.</p>
  </div>
</template>
