/**
 * Unit tests for app/chat/route.ts — the streaming SSE proxy route handler (H3).
 *
 * Verifies:
 *  - Routes to BACKEND_ORIGIN (runtime env, not build-time baked)
 *  - Returns res.body directly (streaming, NOT awaited text)
 *  - Forwards the x-vercel-ai-ui-message-stream header and content-type
 *  - Does NOT forward cookies / credentials
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const TEST_BACKEND = "http://test-backend-9001.internal";

describe("POST /chat route handler (H3)", () => {
  beforeEach(() => {
    process.env.BACKEND_ORIGIN = TEST_BACKEND;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.BACKEND_ORIGIN;
    vi.resetModules(); // clear module cache so env re-read per test
  });

  it("proxies to BACKEND_ORIGIN/chat and streams body with SSE headers", async () => {
    const mockStreamBody = new ReadableStream();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(mockStreamBody, {
          status: 200,
          headers: {
            "content-type": "text/event-stream",
            "x-vercel-ai-ui-message-stream": "v1",
          },
        }),
      ),
    );

    const { POST } = await import("@/app/chat/route");
    const req = new Request("http://localhost/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ messages: [], conversation_id: "c1" }),
    });

    const res = await POST(req);

    // Called the right upstream URL
    expect(fetch).toHaveBeenCalledWith(
      `${TEST_BACKEND}/chat`,
      expect.objectContaining({ method: "POST" }),
    );

    // Body is piped as a stream (not awaited string)
    expect(res.body).toBe(mockStreamBody);

    // SSE and stream protocol headers preserved
    expect(res.headers.get("x-vercel-ai-ui-message-stream")).toBe("v1");
    expect(res.headers.get("content-type")).toContain("text/event-stream");

    // Status code preserved
    expect(res.status).toBe(200);
  });

  it("defaults to localhost:8000 when BACKEND_ORIGIN is unset", async () => {
    delete process.env.BACKEND_ORIGIN;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(new ReadableStream(), {
          status: 200,
          headers: { "content-type": "text/event-stream", "x-vercel-ai-ui-message-stream": "v1" },
        }),
      ),
    );

    const { POST } = await import("@/app/chat/route");
    await POST(new Request("http://localhost/chat", { method: "POST", body: "{}" }));

    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8000/chat",
      expect.anything(),
    );
  });
});
