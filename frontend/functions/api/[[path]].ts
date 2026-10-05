/**
 * Cloudflare Pages Function: same-origin proxy for /api/* -> the real API (`API_ORIGIN` environment binding).
 *
 * Why: the site (Pages) and the API (e.g. Render) are different sites. A proxy keeps the session cookie
 * first-party (SameSite=Lax works), removes CORS, and tells the API the real client IP for rate limiting.
 *
 * Environment bindings (Pages project settings):
 *   API_ORIGIN           required. The API base URL, e.g. https://pm-api.onrender.com
 *   PROXY_SHARED_SECRET  optional but needed for per-user rate limits. Must equal the API's PROXY_SHARED_SECRET
 *                        (16+ random characters). Sent as `X-Proxy-Auth`. When unset, no secret is sent and the API
 *                        refuses to trust `X-Client-IP` (safe, but every visitor then shares the proxy's IP).
 *
 * The logic lives in lib/proxy-handler.ts (a file next to this one would become a Pages route), shared with the
 * Worker entry point worker/index.ts, and is unit-tested in tests/proxy-headers.test.ts and tests/worker.test.ts.
 */
import { proxyApi, type ProxyEnv } from "../../lib/proxy-handler";

export const onRequest = (context: { request: Request; env: ProxyEnv }): Promise<Response> =>
  proxyApi(context.request, context.env);
