import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";
import createNextIntlPlugin from "next-intl/plugin";
import { BASE_SECURITY_HEADERS, buildCsp } from "./lib/security-headers";

const withNextIntl = createNextIntlPlugin("./i18n/request.ts");

/**
 * Production = a fully static export (`out/`), served by Cloudflare Pages, Caddy, nginx, anything.
 * `/api/*` is then handled by the host: the Pages Function in functions/api/[[path]].ts, or the
 * reverse proxy in the Docker image. Static exports cannot use headers()/rewrites(), so those exist only
 * for `next dev`; production headers live in public/_headers.
 */
export default function config(phase: string): NextConfig {
  const dev = phase === PHASE_DEVELOPMENT_SERVER;
  const mock = process.env.NEXT_PUBLIC_API_MOCK === "1";
  const apiTarget = process.env.API_PROXY_TARGET ?? "http://localhost:8000";
  // trailingSlash: /he/holding/index.html, which every static host (Pages, Caddy, nginx, S3) serves for /he/holding/.
  const base: NextConfig = { reactStrictMode: true, images: { unoptimized: true }, trailingSlash: true };
  if (!dev) return withNextIntl({ ...base, output: "export" });
  return withNextIntl({
    ...base,
    async rewrites() {
      return mock ? [] : [{ source: "/api/:path*", destination: `${apiTarget}/api/:path*` }];
    },
    async headers() {
      const headers = Object.entries({ ...BASE_SECURITY_HEADERS, "Content-Security-Policy": buildCsp({ dev: true }) })
        .map(([key, value]) => ({ key, value }));
      return [{ source: "/:path*", headers }];
    },
  });
}
