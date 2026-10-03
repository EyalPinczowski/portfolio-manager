/**
 * Cloudflare Pages Function: same-origin proxy for /api/* -> the real API (`API_ORIGIN` environment binding).
 *
 * Why: the site (Pages) and the API (e.g. Render) are different sites. A proxy keeps the session cookie
 * first-party (SameSite=Lax works), removes CORS, and lets the API rate-limiter trust a single header,
 * CF-Connecting-IP, which only this proxy sets.
 *
 * The header logic lives in lib/proxy-headers.ts (a file next to this one would become a Pages route) and is
 * unit-tested in tests/proxy-headers.test.ts.
 */
import { buildUpstreamHeaders, buildClientHeaders, upstreamUrl } from "../../lib/proxy-headers";

interface Env { API_ORIGIN?: string }

export const onRequest = async (context: { request: Request; env: Env }): Promise<Response> => {
  const { request, env } = context;
  if (!env.API_ORIGIN) return new Response("API_ORIGIN is not configured", { status: 502, headers: { "Cache-Control": "no-store" } });
  const target = upstreamUrl(request.url, env.API_ORIGIN);
  if (!target) return new Response("Bad API_ORIGIN", { status: 502, headers: { "Cache-Control": "no-store" } });

  const method = request.method.toUpperCase();
  const init: RequestInit = {
    method,
    headers: buildUpstreamHeaders(request.headers),
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
