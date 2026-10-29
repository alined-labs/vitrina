import type { NextConfig } from "next";

// The browser talks to the API through this same-origin proxy, so the API needs no CORS
// settings. API_PROXY_TARGET is read when `next dev` starts and at `next build` (rewrites are
// fixed in the build output); the default matches `make api` on :8000.
const apiTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";

// On every response. The dashboard is never framed: its buttons send campaigns and erase
// contacts, so another site must not be able to place them under a visitor's click. The CSP
// holds only what cannot break a page; limiting scripts needs a nonce per request for Next's
// inline scripts, which would make every page dynamic.
const securityHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  {
    key: "Content-Security-Policy",
    value: "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'",
  },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()" },
  { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains" },
];

const nextConfig: NextConfig = {
  poweredByHeader: false,
  async headers() {
    return [{ source: "/:path*", headers: securityHeaders }];
  },
  async rewrites() {
    return [{ source: "/api/v1/:path*", destination: `${apiTarget}/v1/:path*` }];
  },
};

export default nextConfig;
