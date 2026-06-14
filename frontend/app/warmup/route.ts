/**
 * H3 — Runtime passthrough proxy for the backend /warmup endpoint.
 *
 * Reads BACKEND_ORIGIN at request time (not build time) so Docker images work
 * correctly.  Credentials are not forwarded (C2 alignment).
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: Request): Promise<Response> {
  const backend = process.env.BACKEND_ORIGIN ?? "http://localhost:8000";

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
}
