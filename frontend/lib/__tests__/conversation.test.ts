import { describe, it, expect, beforeEach } from "vitest";
import { getOrCreateConversationId } from "@/lib/conversation";
beforeEach(() => sessionStorage.clear());
describe("conversation id", () => {
  it("stable within session", () => {
    const a = getOrCreateConversationId();
    expect(getOrCreateConversationId()).toBe(a);
    expect(a).toMatch(/^[0-9a-f-]{36}$/);
  });
});
