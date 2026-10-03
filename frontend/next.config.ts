import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const withNextIntl = createNextIntlPlugin("./i18n/request.ts");

// In dev, proxy /api to the FastAPI backend. In production Caddy routes /api;
// set API_PROXY_TARGET at build time to also proxy from Next.
const apiTarget =
  process.env.API_PROXY_TARGET ??
  (process.env.NODE_ENV !== "production" ? "http://localhost:8000" : "");

const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  async rewrites() {
    if (!apiTarget || process.env.NEXT_PUBLIC_API_MOCK === "1") return [];
    return [{ source: "/api/:path*", destination: `${apiTarget}/api/:path*` }];
  },
};

export default withNextIntl(nextConfig);
