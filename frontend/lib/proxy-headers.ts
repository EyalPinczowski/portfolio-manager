/**
 * Header handling for the Cloudflare Pages Function in functions/api/[[path]].ts. Pure functions, no Workers APIs,
 * so they can be unit-tested in Node.
 */

/** RFC 9110 hop-by-hop headers (never forwarded) plus `host`, which must match the upstream. */
const HOP_BY_HOP = new Set([
  "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection", "te", "trailer",
  "transfer-encoding", "upgrade", "host",
]);

/** Headers a client must not be able to use to impersonate the proxy or choose its own client IP. */
const IP_HEADERS = ["cf-connecting-ip", "x-forwarded-for", "x-real-ip", "true-client-ip", "forwarded", "x-forwarded-host", "x-forwarded-proto"];

/** Cloudflare adds these; the API does not need them. */
const CF_INTERNAL = /^cf-(?!connecting-ip$)|^x-forwarded-|^cdn-loop$/i;

function connectionTokens(h: Headers): Set<string> {
  return new Set((h.get("connection") ?? "").split(",").map((t) => t.trim().toLowerCase()).filter(Boolean));
}

function copyWithoutHopByHop(src: Headers): Headers {
  const extra = connectionTokens(src);
  const out = new Headers();
  src.forEach((value, name) => {
    const n = name.toLowerCase();
    if (HOP_BY_HOP.has(n) || extra.has(n)) return;
    out.append(name, value);
  });
  return out;
}

/**
 * Request headers for the upstream API: method/body are handled by the caller; this keeps cookies, content type,
 * CSRF header etc., drops hop-by-hop headers, and sets CF-Connecting-IP from the incoming CF-Connecting-IP
 * (the real client IP as Cloudflare saw it). Client-supplied forwarding headers are removed so they cannot be spoofed.
 */
export function buildUpstreamHeaders(incoming: Headers): Headers {
  const clientIp = incoming.get("cf-connecting-ip");
  const out = copyWithoutHopByHop(incoming);
  for (const h of [...out.keys()]) if (CF_INTERNAL.test(h)) out.delete(h);
  for (const h of IP_HEADERS) out.delete(h);
  if (clientIp) out.set("CF-Connecting-IP", clientIp);
  out.delete("accept-encoding"); // let the runtime negotiate; avoids double-compressed bodies
  return out;
}

/** Response headers for the browser: drop hop-by-hop, keep (multiple) Set-Cookie, and forbid caching. */
export function buildClientHeaders(upstream: Headers): Headers {
  const out = copyWithoutHopByHop(upstream);
  out.delete("content-length"); // length may change if the runtime re-encodes the body
  out.set("Cache-Control", "no-store");
  out.delete("etag");
  out.delete("last-modified");
  out.delete("expires");
  return out;
}

/** Upstream URL = API_ORIGIN + the incoming path and query. Returns null if API_ORIGIN is not an http(s) URL. */
export function upstreamUrl(requestUrl: string, apiOrigin: string): string | null {
  let origin: URL;
  try { origin = new URL(apiOrigin); } catch { return null; }
  if (origin.protocol !== "https:" && origin.protocol !== "http:") return null;
  const incoming = new URL(requestUrl);
  const base = origin.pathname.replace(/\/+$/, "");
  return `${origin.origin}${base}${incoming.pathname}${incoming.search}`;
}
