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
 * Headers sent upstream: `X-Client-IP` (from the incoming CF-Connecting-IP; Cloudflare overwrites that header on
 * subrequests to another zone, hence the separate name) and `X-Proxy-Auth`. API side: TRUSTED_PROXY_HEADER=X-Client-IP.
 *
 * The header logic lives in lib/proxy-headers.ts (a file next to this one would become a Pages route) and is
 * unit-tested in tests/proxy-headers.test.ts.
 */
import { buildUpstreamHeaders, buildClientHeaders, upstreamUrl } from "../../lib/proxy-headers";

interface Env { API_ORIGIN?: string; PROXY_SHARED_SECRET?: string }

export const onRequest = async (context: { request: Request; env: Env }): Promise<Response> => {
  const { request, env } = context;
  if (!env.API_ORIGIN) return new Response("API_ORIGIN is not configured", { status: 502, headers: { "Cache-Control": "no-store" } });
  const target = upstreamUrl(request.url, env.API_ORIGIN);
  if (!target) return new Response("Bad API_ORIGIN", { status: 502, headers: { "Cache-Control": "no-store" } });

  const method = request.method.toUpperCase();
  const init: RequestInit = {
    method,
    headers: buildUpstreamHeaders(request.headers, env.PROXY_SHARED_SECRET),
    body: method === "GET" || method === "HEAD" ? undefined : request.body,
    redirect: "manual", // pass redirects to the browser; never follow them server-side
    // Never cache API responses at the edge.
    cf: { cacheTtl: 0, cacheEverything: false },
  } as RequestInit;

  let upstream: Response;
  try {
    upstream = await fetch(target, init);
  } catch {
    return new Response("Upstream unavailable", { status: 502, headers: { "Cache-Control": "no-store" } });
  }
  // Response(body, init) keeps multiple Set-Cookie headers when built from a Headers object.
  return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: buildClientHeaders(upstream.headers) });
};
