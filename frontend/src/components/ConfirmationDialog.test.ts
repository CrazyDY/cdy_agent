import { flushPromises, mount } from "@vue/test-utils"
import { describe, expect, it } from "vitest"

import type { ApprovalRequired } from "../api/protocol"
import ConfirmationDialog from "./ConfirmationDialog.vue"

function approvalRequired(allowAlways: boolean): ApprovalRequired {
  return {
    type: "approval.required",
    turn_id: "7b4c48ea-ddba-4c41-a755-b5222e4d122a",
    approval_id: "3e1a2ebb-95e2-4b9a-86de-849555043088",
    description: "Run the exact prepared command: uv run pytest?",
    allow_always: allowAlways,
  }
}

describe("ConfirmationDialog", () => {
  it("shows the exact description and hides unsupported persistent approval", () => {
    const request = approvalRequired(false)
    const wrapper = mount(ConfirmationDialog, { props: { request } })

    expect(wrapper.get('[role="alertdialog"]').attributes("aria-modal")).toBe("true")
    expect(wrapper.get("[data-test=confirmation-description]").text()).toBe(
      request.description,
    )
    expect(wrapper.text()).not.toContain("Always allow")
  })

  it.each([
    ["deny", "deny"],
    ["allow-once", "allow_once"],
    ["allow-always", "allow_always"],
  ] as const)("emits %s decisions", async (testId, decision) => {
    const wrapper = mount(ConfirmationDialog, {
      attachTo: document.body,
      props: { request: approvalRequired(true) },
    })

    await wrapper.get(`[data-test="${testId}"]`).trigger("click")

    expect(wrapper.emitted("resolve")).toEqual([[decision]])
    wrapper.unmount()
  })

  it("keeps keyboard focus inside the blocking dialog", async () => {
    const wrapper = mount(ConfirmationDialog, {
      attachTo: document.body,
      props: { request: approvalRequired(true) },
    })
    const buttons = wrapper.findAll("button")

    await flushPromises()
    expect(document.activeElement).toBe(buttons[0].element)
    buttons.at(-1)!.element.focus()
    await wrapper.get('[role="alertdialog"]').trigger("keydown", {
      key: "Tab",
    })
    expect(document.activeElement).toBe(buttons[0].element)

    buttons[0].element.focus()
    await wrapper.get('[role="alertdialog"]').trigger("keydown", {
      key: "Tab",
      shiftKey: true,
    })
    expect(document.activeElement).toBe(buttons.at(-1)!.element)
    wrapper.unmount()
  })
})
