import { describe, it, expect } from "vitest";
import { DefaultChatTransport } from "ai";
import { makeChatTransport } from "@/lib/transport";
describe("transport", () => {
  it("is a DefaultChatTransport (UI-message-stream mode) to /chat", () => {
    expect(makeChatTransport("conv-1")).toBeInstanceOf(DefaultChatTransport);
  });
});
