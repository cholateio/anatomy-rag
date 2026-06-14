import { DefaultChatTransport } from "ai";
export function makeChatTransport(conversationId: string) {
  return new DefaultChatTransport({
    api: "/chat",
    body: { conversation_id: conversationId },
  });
}
