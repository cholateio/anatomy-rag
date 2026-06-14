import { describe, it, expect } from "vitest";
import { DefaultChatTransport } from "ai";
import { makeChatTransport } from "@/lib/transport";

describe("transport", () => {
  it("is a DefaultChatTransport (UI-message-stream mode) to /chat", () => {
    expect(makeChatTransport("conv-1")).toBeInstanceOf(DefaultChatTransport);
  });

  it("includes conversation_id in body (L8 — request body shape)", () => {
    // HttpChatTransport stores body as a protected field; cast to access it
    const t = makeChatTransport("conv-xyz") as unknown as { body: { conversation_id: string } };
    expect(t.body).toEqual({ conversation_id: "conv-xyz" });
  });

  it("passes credentials:'omit' to prevent credential leakage (C2)", () => {
    // HttpChatTransport stores credentials as a protected field
    const t = makeChatTransport("conv-1") as unknown as { credentials: RequestCredentials };
    expect(t.credentials).toBe("omit");
  });
});
