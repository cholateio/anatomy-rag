import { DefaultChatTransport } from "ai";

/**
 * C2 / DL-016 — No auth leakage: credentials:"omit" ensures that browser
 * cookies and the Authorization header are never sent to the chat proxy.
 */
export function makeChatTransport(conversationId: string) {
  return new DefaultChatTransport({
    api: "/chat",
    body: { conversation_id: conversationId },
    credentials: "omit",
  });
}
