/**
 * H3 — Runtime passthrough proxy for the backend /warmup endpoint.
 *
 * Reads BACKEND_ORIGIN at request time (not build time) so Docker images work
 * correctly.  Credentials are not forwarded (C2 alignment).
 *
 * Fix 2: wraps the upstream fetch in try/catch — if the backend is unreachable
 * (ECONNREFUSED / DNS / timeout) the handler returns 502 instead of throwing.
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request): Promise<Response> {
  const backend = process.env.BACKEND_ORIGIN ?? "http://localhost:8000";

  try {
    const upstream = await fetch(`${backend}/warmup`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: await req.text(),
    });

    return new Response(await upstream.text(), {
      status: upstream.status,
      headers: {
        "content-type":
          upstream.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (_err) {
    return new Response("上游服務暫時無法使用", {
      status: 502,
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
  }
}
