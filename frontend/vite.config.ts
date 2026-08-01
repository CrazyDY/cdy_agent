import { defineConfig } from "vitest/config"
import vue from "@vitejs/plugin-vue"
import process from "node:process"

export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: process.env.CDY_AGENT_WEB_STATIC_DIRECTORY ?? "../src/cdy_agent/web/static",
    emptyOutDir: true,
    sourcemap: false,
  },
  test: {
    environment: "jsdom",
    globals: true,
  },
})
