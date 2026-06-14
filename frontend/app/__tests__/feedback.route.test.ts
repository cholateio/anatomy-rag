/**
 * Unit tests for app/feedback/route.ts — the feedback passthrough proxy (H3).
 *
 * Verifies:
 *  - Routes to BACKEND_ORIGIN at request time
 *  - Proxies body and returns upstream status
 *  - Returns 502 when upstream is unreachable
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

const TEST_BACKEND = "http://test-backend-9001.internal";

describe("POST /feedback route handler (H3)", () => {
  beforeEach(() => {
    process.env.BACKEND_ORIGIN = TEST_BACKEND;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.BACKEND_ORIGIN;
    vi.resetModules();
  });

  it("proxies to BACKEND_ORIGIN/feedback and returns the upstream response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ ok: true }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    const { POST } = await import("@/app/feedback/route");
    const req = new Request("http://localhost/feedback", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ rating: 1, conversation_id: "c1" }),
    });

    const res = await POST(req);

    expect(fetch).toHaveBeenCalledWith(
      `${TEST_BACKEND}/feedback`,
      expect.objectContaining({ method: "POST" }),
    );
    expect(res.status).toBe(200);
  });

  it("returns 502 when upstream fetch rejects (ECONNREFUSED / unreachable)", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("ECONNREFUSED")));

    const { POST } = await import("@/app/feedback/route");
    const req = new Request("http://localhost/feedback", {
      method: "POST",
      body: "{}",
    });

    const res = await POST(req);

    expect(res.status).toBe(502);
    expect(await res.text()).toContain("上游服務暫時無法使用");
  });
});
