import type { NextConfig } from "next";

// The browser talks to the API through this same-origin proxy, so the API needs no CORS
// settings. API_PROXY_TARGET is read when `next dev` starts and at `next build` (rewrites are
// fixed in the build output); the default matches `make api` on :8000.
const apiTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${apiTarget}/v1/:path*` }];
  },
};

export default nextConfig;
