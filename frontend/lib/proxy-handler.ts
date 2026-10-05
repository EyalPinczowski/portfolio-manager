/**
 * The /api/* proxy shared by the Cloudflare Pages Function (functions/api/[[path]].ts) and the Worker
 * (worker/index.ts). Forwards to `API_ORIGIN`, sends `X-Client-IP` and `X-Proxy-Auth` (see proxy-headers.ts) and
 * never caches. Only Web-standard APIs, so it is unit-tested in Node.
 */
import { buildUpstreamHeaders, buildClientHeaders, upstreamUrl } from "./proxy-headers";

export interface ProxyEnv { API_ORIGIN?: string; PROXY_SHARED_SECRET?: string }

const NO_STORE = { "Cache-Control": "no-store" };

export async function proxyApi(request: Request, env: ProxyEnv): Promise<Response> {
  if (!env.API_ORIGIN) return new Response("API_ORIGIN is not configured", { status: 502, headers: NO_STORE });
  const target = upstreamUrl(request.url, env.API_ORIGIN);
  if (!target) return new Response("Bad API_ORIGIN", { status: 502, headers: NO_STORE });

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
    return new Response("Upstream unavailable", { status: 502, headers: NO_STORE });
  }
  // Response(body, init) keeps multiple Set-Cookie headers when built from a Headers object.
  return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers: buildClientHeaders(upstream.headers) });
}
