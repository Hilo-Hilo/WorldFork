/** @type {import('next').NextConfig} */
const BACKEND = process.env.API_BASE_URL || process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8003";

const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    // Proxy /backend/* from the browser to the real backend so client components
    // can fetch with same-origin and we sidestep CORS + WSL/Windows host weirdness.
    return [{ source: "/backend/:path*", destination: `${BACKEND}/:path*` }];
  },
};

export default nextConfig;
