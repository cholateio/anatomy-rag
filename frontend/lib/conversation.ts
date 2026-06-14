const KEY = "anatomy-rag:conversation_id";
export function getOrCreateConversationId(): string {
  let id = sessionStorage.getItem(KEY);
  if (!id) { id = crypto.randomUUID(); sessionStorage.setItem(KEY, id); }
  return id;
}
