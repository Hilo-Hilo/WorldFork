/** @type {import('next').NextConfig} */
// Honor the repo's canonical env vars (WORLD_FORK_API_BASE / BACKEND_API_BASE)
// alongside the Next.js-style ones so deployments don't fall through to
// localhost when configured the standard way.
const BACKEND =
  process.env.API_BASE_URL ||
  process.env.NEXT_PUBLIC_API_URL ||
  process.env.WORLD_FORK_API_BASE ||
  process.env.BACKEND_API_BASE ||
  "http://127.0.0.1:8003";

const nextConfig = {
  reactStrictMode: true,
  // 5-minute proxy timeout — the initializer-agent and run-until-complete
  // mutations can both run multi-minute LLM chains; the default 30s drops
  // the connection mid-flight and surfaces a generic "Internal Server Error".
  experimental: { proxyTimeout: 300_000 },
  async rewrites() {
    // Proxy /backend/* from the browser to the real backend so client components
    // can fetch with same-origin and we sidestep CORS + WSL/Windows host weirdness.
    return [{ source: "/backend/:path*", destination: `${BACKEND}/:path*` }];
  },
};

export default nextConfig;
