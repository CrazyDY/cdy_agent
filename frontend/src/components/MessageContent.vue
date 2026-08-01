<script setup lang="ts">
import DOMPurify from "dompurify"
import MarkdownIt from "markdown-it"
import { computed } from "vue"

const props = defineProps<{ content: string }>()

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

const rendered = computed(() => {
  const sanitized = DOMPurify.sanitize(markdown.render(props.content), {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["style"],
  })
  const container = document.createElement("div")
  container.innerHTML = sanitized

  for (const link of container.querySelectorAll<HTMLAnchorElement>("a")) {
    const href = link.getAttribute("href")
    if (href === null) {
      continue
    }
    try {
      const url = new URL(href, window.location.href)
      if (url.protocol !== "http:" && url.protocol !== "https:" && url.protocol !== "mailto:") {
        link.removeAttribute("href")
        continue
      }
      if (url.protocol !== "mailto:" && url.origin !== window.location.origin) {
        link.target = "_blank"
        link.rel = "noreferrer noopener"
      }
    } catch {
      link.removeAttribute("href")
    }
  }

  return container.innerHTML
})
</script>

<template>
  <div class="message-content" v-html="rendered"></div>
</template>
