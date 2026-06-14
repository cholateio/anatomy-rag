import type { SourcesData } from "@/lib/types";

export const FIXED_TURN = "00000000-0000-0000-0000-0000000000aa";

const sources: SourcesData = {
  sources: [
    {
      book_title: "Gray",
      edition: "42",
      page: 812,
      figure: "Fig.7-23",
      image_url: "/p/1.webp",
      snippet: "肱二頭肌起於喙突",
      score: 0.9,
    },
  ],
};

function makeSSEBody(frames: unknown[]): string {
  return (
    frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join("") +
    "data: [DONE]\n\n"
  );
}

const UI_MSG_STREAM_HEADERS = {
  "content-type": "text/event-stream",
  "x-vercel-ai-ui-message-stream": "v1",
};

/**
 * A successful full-stream fixture:
 *   start → data-sources → text-delta → data-verification → finish
 *
 * The `start.messageId` is FIXED_TURN, so `message.id` should equal FIXED_TURN
 * after the stream (Pin-verify A).
 */
export function uiMessageStreamResponse(): Response {
  const frames = [
    { type: "start", messageId: FIXED_TURN },
    {
      type: "data-sources",
      data: sources,
    },
    { type: "text-start", id: "t0" },
    {
      type: "text-delta",
      id: "t0",
      delta: "起於喙突 [Gray, p.812, Fig.7-23]。",
    },
    { type: "text-end", id: "t0" },
    {
      type: "data-verification",
      data: { verified: true, has_citations: true, unverified: [] },
    },
    { type: "finish" },
  ];
  return new Response(makeSSEBody(frames), {
    status: 200,
    headers: UI_MSG_STREAM_HEADERS,
  });
}

/**
 * An error stream fixture — used for Pin-verify B.
 * The SDK should surface `status==='error'` after processing this.
 */
export function errorStreamResponse(): Response {
  const body =
    `data: ${JSON.stringify({ type: "start", messageId: FIXED_TURN })}\n\n` +
    `data: ${JSON.stringify({ type: "error", errorText: "服務暫時無法使用" })}\n\n` +
    "data: [DONE]\n\n";
  return new Response(body, {
    status: 200,
    headers: UI_MSG_STREAM_HEADERS,
  });
}
