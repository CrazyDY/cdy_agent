import { mount } from "@vue/test-utils"
import { describe, expect, it } from "vitest"

import ChatComposer from "./ChatComposer.vue"

function mountComposer() {
  return mount(ChatComposer, {
    props: {
      inputDisabled: false,
      retryDisabled: false,
      active: false,
      canRetry: false,
    },
  })
}

describe("ChatComposer", () => {
  it("sends the message when Enter is pressed", async () => {
    const wrapper = mountComposer()
    const textarea = wrapper.get<HTMLTextAreaElement>("textarea")
    await textarea.setValue("Send from keyboard")

    await textarea.trigger("keydown", { key: "Enter" })

    expect(wrapper.emitted("send")).toEqual([["Send from keyboard"]])
    expect(textarea.element.value).toBe("")
  })

  it("keeps the draft when Shift+Enter is pressed", async () => {
    const wrapper = mountComposer()
    const textarea = wrapper.get<HTMLTextAreaElement>("textarea")
    await textarea.setValue("First line")

    await textarea.trigger("keydown", { key: "Enter", shiftKey: true })

    expect(wrapper.emitted("send")).toBeUndefined()
    expect(textarea.element.value).toBe("First line")
  })

  it("does not send while an input method composition is active", async () => {
    const wrapper = mountComposer()
    const textarea = wrapper.get<HTMLTextAreaElement>("textarea")
    await textarea.setValue("中文输入")

    await textarea.trigger("keydown", { key: "Enter", isComposing: true })

    expect(wrapper.emitted("send")).toBeUndefined()
    expect(textarea.element.value).toBe("中文输入")
  })
})
