/** @type {import('next').NextConfig} */
const backend = process.env.BACKEND_ORIGIN ?? "http://localhost:8000";
const nextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      { source: "/chat", destination: `${backend}/chat` },
      { source: "/feedback", destination: `${backend}/feedback` },
      { source: "/warmup", destination: `${backend}/warmup` },
    ];
  },
};
export default nextConfig;
