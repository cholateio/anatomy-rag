/**
 * H3 — Runtime SSE proxy for the backend /chat endpoint.
 *
 * Uses a Next.js Route Handler (not next.config rewrites) so that
 * BACKEND_ORIGIN is read at REQUEST time, not at build time.  This lets the
 * Docker standalone image pick up the compose env var correctly.
 *
 * C2 alignment: credentials are NOT forwarded to the backend.
 *
 * Fix 2: wraps the upstream fetch in try/catch — if the backend is unreachable
 * (ECONNREFUSED / DNS / timeout) the handler returns 502 instead of throwing.
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request): Promise<Response> {
  const backend = process.env.BACKEND_ORIGIN ?? "http://localhost:8000";

  try {
    const upstream = await fetch(`${backend}/chat`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      // Body piped as-is; no cookies / auth headers forwarded
      body: await req.text(),
    });

    // Return the ReadableStream body directly — no buffering — to preserve SSE streaming.
    return new Response(upstream.body, {
      status: upstream.status,
      headers: {
        "content-type":
          upstream.headers.get("content-type") ?? "text/event-stream",
        "x-vercel-ai-ui-message-stream":
          upstream.headers.get("x-vercel-ai-ui-message-stream") ?? "v1",
        "cache-control": "no-cache, no-transform",
      },
    });
  } catch (_err) {
    return new Response("上游服務暫時無法使用", {
      status: 502,
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
  }
}
