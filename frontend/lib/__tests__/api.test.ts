import { describe, it, expect, vi, beforeEach } from "vitest";
import { postFeedback } from "@/lib/api";
const FIXED_TURN = "00000000-0000-0000-0000-0000000000aa";
beforeEach(() => vi.restoreAllMocks());
describe("postFeedback", () => {
  it("POSTs /feedback with message_id/rating/text", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await postFeedback({ messageId: FIXED_TURN, rating: -1, text: "錯" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/feedback");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ message_id: FIXED_TURN, rating: -1, text: "錯" });
  });
  it("throws on non-ok response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("", { status: 404 })));
    await expect(postFeedback({ messageId: FIXED_TURN, rating: 1 })).rejects.toThrow();
  });
});
