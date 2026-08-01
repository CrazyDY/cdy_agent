import { mount } from "@vue/test-utils"
import { describe, expect, it } from "vitest"

import MessageContent from "./MessageContent.vue"

describe("MessageContent", () => {
  it("renders Markdown code without enabling raw HTML", () => {
    const wrapper = mount(MessageContent, {
      props: {
        content: "Use `uv run` safely.\n\n```python\nprint('hello')\n```\n\n<img src=x onerror=alert(1)>",
      },
    })

    expect(wrapper.get("code").text()).toBe("uv run")
    expect(wrapper.get("pre code").text()).toBe("print('hello')")
    expect(wrapper.find("img").exists()).toBe(false)
  })

  it("removes active content and unsafe URLs", () => {
    const wrapper = mount(MessageContent, {
      props: {
        content:
          "<script>alert(1)</script>\n\n[unsafe](javascript:alert(2))\n\n[safe](https://example.com/docs)",
      },
    })

    expect(wrapper.find("script").exists()).toBe(false)
    expect(wrapper.find('a[href^="javascript:"]').exists()).toBe(false)
    const safe = wrapper.get('a[href="https://example.com/docs"]')
    expect(safe.attributes("target")).toBe("_blank")
    expect(safe.attributes("rel")).toBe("noreferrer noopener")
  })
})
