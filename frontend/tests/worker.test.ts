import { afterEach, describe, expect, it, vi } from "vitest";
import worker from "../worker/index";

const assets = { fetch: vi.fn(async () => new Response("static page")) };
const env = { API_ORIGIN: "https://api.example.com", PROXY_SHARED_SECRET: "s".repeat(20), ASSETS: assets };

afterEach(() => { vi.unstubAllGlobals(); assets.fetch.mockClear(); });

describe("worker routing", () => {
  it("serves non-API paths from the static assets", async () => {
    const res = await worker.fetch(new Request("https://site.example/en/portfolio/"), env);
    expect(await res.text()).toBe("static page");
    expect(assets.fetch).toHaveBeenCalledOnce();
  });

  it("proxies /api/* to API_ORIGIN with the shared secret and client IP", async () => {
    const upstream = vi.fn<(url: string, init: RequestInit) => Promise<Response>>(async () => new Response("{}", { status: 200 }));
    vi.stubGlobal("fetch", upstream);
    const req = new Request("https://site.example/api/health", { headers: { "cf-connecting-ip": "1.2.3.4" } });
    const res = await worker.fetch(req, env);
    expect(res.status).toBe(200);
    expect(assets.fetch).not.toHaveBeenCalled();
    const [url, init] = upstream.mock.calls[0];
    expect(url).toBe("https://api.example.com/api/health");
    const h = new Headers(init.headers);
    expect(h.get("x-proxy-auth")).toBe(env.PROXY_SHARED_SECRET);
    expect(h.get("x-client-ip")).toBe("1.2.3.4");
  });

  it("answers 502 when API_ORIGIN is missing", async () => {
    const res = await worker.fetch(new Request("https://site.example/api/x"), { ...env, API_ORIGIN: undefined });
    expect(res.status).toBe(502);
  });

  it("answers 502 when the upstream is down", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("down"); }));
    const res = await worker.fetch(new Request("https://site.example/api/x"), env);
    expect(res.status).toBe(502);
  });
});
