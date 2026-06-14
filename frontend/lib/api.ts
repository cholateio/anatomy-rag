export async function postFeedback(input: { messageId: string; rating: 1 | -1; text?: string }): Promise<void> {
  const res = await fetch("/feedback", {
    method: "POST",
    credentials: "omit",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message_id: input.messageId, rating: input.rating, ...(input.text ? { text: input.text } : {}) }),
  });
  if (!res.ok) throw new Error(`feedback failed: ${res.status}`);
}
