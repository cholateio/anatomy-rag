/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  // H3: backend proxy is handled by app/chat/route.ts, app/feedback/route.ts,
  // and app/warmup/route.ts so that BACKEND_ORIGIN is read at REQUEST time
  // (not baked in at build time).  Remove rewrites entirely.
};
export default nextConfig;
