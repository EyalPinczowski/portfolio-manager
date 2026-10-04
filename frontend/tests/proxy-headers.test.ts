import { describe, expect, it } from "vitest";
import { buildClientHeaders, buildUpstreamHeaders, upstreamUrl } from "@/lib/proxy-headers";

describe("Pages Function header handling", () => {
  const incoming = () =>
    new Headers({
      host: "app.example.pages.dev",
      cookie: "session=abc; csrf=1",
      "content-type": "application/json",
      "x-csrf-token": "tok",
      connection: "keep-alive, X-Custom-Hop",
      "x-custom-hop": "secret",
      "keep-alive": "timeout=5",
      "transfer-encoding": "chunked",
      upgrade: "websocket",
      "cf-connecting-ip": "203.0.113.7",
      "cf-ray": "abc123-FRA",
      "cf-ipcountry": "IL",
      "x-forwarded-for": "6.6.6.6",
      "x-real-ip": "6.6.6.6",
      "true-client-ip": "6.6.6.6",
      forwarded: "for=6.6.6.6",
    });

  it("forwards cookies and request headers, sets X-Client-IP and X-Proxy-Auth, strips hop-by-hop headers", () => {
    const h = buildUpstreamHeaders(incoming(), "s3cret-0123456789abc");
    expect(h.get("cookie")).toBe("session=abc; csrf=1");
    expect(h.get("content-type")).toBe("application/json");
    expect(h.get("x-csrf-token")).toBe("tok");
    expect(h.get("x-client-ip")).toBe("203.0.113.7");
    expect(h.get("x-proxy-auth")).toBe("s3cret-0123456789abc");
    expect(h.has("cf-connecting-ip")).toBe(false);
    for (const gone of ["host", "connection", "keep-alive", "transfer-encoding", "upgrade", "x-custom-hop"]) {
      expect(h.has(gone), gone).toBe(false);
    }
  });

  it("removes client-supplied forwarding headers so the client IP cannot be spoofed", () => {
    const h = buildUpstreamHeaders(incoming());
    for (const gone of ["x-forwarded-for", "x-real-ip", "true-client-ip", "forwarded", "cf-ray", "cf-ipcountry"]) {
      expect(h.has(gone), gone).toBe(false);
    }
  });

  it("does not invent X-Client-IP when the incoming request has none", () => {
    const h = buildUpstreamHeaders(new Headers({ cookie: "a=b", "x-forwarded-for": "6.6.6.6" }), "s3cret-0123456789abc");
    expect(h.has("x-client-ip")).toBe(false);
    expect(h.has("x-forwarded-for")).toBe(false);
  });

  it("sends no secret when PROXY_SHARED_SECRET is unset (the API then ignores X-Client-IP)", () => {
    for (const secret of [undefined, ""]) {
      const h = buildUpstreamHeaders(incoming(), secret);
      expect(h.has("x-proxy-auth")).toBe(false);
      expect(h.get("x-client-ip")).toBe("203.0.113.7");
    }
  });

  it("drops a client-supplied X-Client-IP / X-Proxy-Auth and never forwards them", () => {
    const evil = incoming();
    evil.set("x-client-ip", "6.6.6.6");
    evil.set("x-proxy-auth", "guess");
    const withSecret = buildUpstreamHeaders(evil, "real-secret-0123456789");
    expect(withSecret.get("x-client-ip")).toBe("203.0.113.7");
    expect(withSecret.get("x-proxy-auth")).toBe("real-secret-0123456789");
    const noSecret = buildUpstreamHeaders(evil);
    expect(noSecret.has("x-proxy-auth")).toBe(false);
    expect(noSecret.get("x-client-ip")).toBe("203.0.113.7");
  });

  it("never caches the response, keeps every Set-Cookie, drops hop-by-hop headers", () => {
    const up = new Headers({ "content-type": "application/json", "cache-control": "public, max-age=600", etag: "x", connection: "close", "keep-alive": "x", "content-length": "10" });
    up.append("set-cookie", "session=1; HttpOnly; Secure; SameSite=Lax");
    up.append("set-cookie", "csrf=2; Secure");
    const h = buildClientHeaders(up);
    expect(h.get("cache-control")).toBe("no-store");
    expect(h.has("etag")).toBe(false);
    expect(h.has("connection")).toBe(false);
    expect(h.has("keep-alive")).toBe(false);
    expect(h.has("content-length")).toBe(false);
    expect(h.getSetCookie()).toEqual(["session=1; HttpOnly; Secure; SameSite=Lax", "csrf=2; Secure"]);
    expect(h.get("content-type")).toBe("application/json");
  });

  it("builds the upstream URL from API_ORIGIN, path and query", () => {
    expect(upstreamUrl("https://app.pages.dev/api/portfolios/1/summary?x=1&y=2", "https://api.onrender.com")).toBe("https://api.onrender.com/api/portfolios/1/summary?x=1&y=2");
    expect(upstreamUrl("https://app.pages.dev/api/health", "https://api.example.com/")).toBe("https://api.example.com/api/health");
    expect(upstreamUrl("https://app.pages.dev/api/health", "not a url")).toBeNull();
    expect(upstreamUrl("https://app.pages.dev/api/health", "ftp://example.com")).toBeNull();
  });
});
