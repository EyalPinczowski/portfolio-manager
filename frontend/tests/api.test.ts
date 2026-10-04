import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, setCsrfToken } from "@/lib/api";

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });

describe("api client", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_MOCK", "");
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    setCsrfToken(null);
  });
  afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

  it("sends cookies and no CSRF header on GET", async () => {
    fetchMock.mockResolvedValueOnce(json([]));
    await api.portfolios();
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/portfolios");
    expect(init.credentials).toBe("include");
    expect(init.headers["X-CSRF-Token"]).toBeUndefined();
  });

  it("fetches the CSRF token from /auth/me and sends it on mutations", async () => {
    fetchMock
      .mockResolvedValueOnce(json({ id: 1, email: "a@b.c", locale: "he", disclaimer_accepted: true, ocr_consent: true, csrf_token: "tok123" }))
      .mockResolvedValueOnce(json({ id: 1 }));
    await api.patchPortfolio(1, { name: "x" });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/auth/me");
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/portfolios/1");
    expect(init.method).toBe("PATCH");
    expect(init.credentials).toBe("include");
    expect(init.headers["X-CSRF-Token"]).toBe("tok123");
  });

  it("reuses a cached token and sends it on DELETE", async () => {
    setCsrfToken("cached");
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await api.deleteAlert(5);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][1].headers["X-CSRF-Token"]).toBe("cached");
  });
});

describe("api client: contract changes", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_MOCK", "");
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    setCsrfToken("tok");
  });
  afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

  it("uploads the screenshot as a raw image body, not multipart", async () => {
    fetchMock.mockResolvedValueOnce(json({ id: 1 }));
    const img = new Blob([new Uint8Array([1, 2, 3])], { type: "image/png" });
    await api.createImport(7, img);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/portfolios/7/imports");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(img);
    expect(init.body).not.toBeInstanceOf(FormData);
    expect(init.headers["Content-Type"]).toBe("image/png");
    expect(init.headers["X-CSRF-Token"]).toBe("tok");
  });

  it("refuses to upload anything but png/jpeg/webp (server answers 415)", () => {
    expect(() => api.createImport(1, new Blob(["x"], { type: "application/pdf" }))).toThrow(ApiError);
    expect(() => api.createImport(1, new Blob(["x"], { type: "image/gif" }))).toThrow(ApiError);
  });

  it("posts parsed rows as JSON to /imports/rows", async () => {
    fetchMock.mockResolvedValueOnce(json({ id: 1 }));
    await api.importRows(3, []);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/portfolios/3/imports/rows");
    expect(JSON.parse(init.body)).toEqual({ rows: [], scope: "partial" });
    expect(init.headers["Content-Type"]).toBe("application/json");
  });

  it("sends scope=full only when asked, and can switch the scope of a draft with PATCH", async () => {
    fetchMock.mockResolvedValueOnce(json({ id: 1 })).mockResolvedValueOnce(json({ id: 1 }));
    await api.importRows(3, [], "full");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ rows: [], scope: "full" });
    await api.patchImport(1, { scope: "partial" });
    expect(fetchMock.mock.calls[1][1].method).toBe("PATCH");
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ scope: "partial" });
  });

  it("export is POST with the password; delete sends the password in a JSON body", async () => {
    fetchMock.mockResolvedValueOnce(json({ ok: true })).mockResolvedValueOnce(new Response(null, { status: 204 }));
    await api.exportData("pw1");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/me/export");
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ password: "pw1" });
    await api.deleteAccount("pw2");
    expect(fetchMock.mock.calls[1][1].method).toBe("DELETE");
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({ password: "pw2" });
  });

  it("surfaces 429 with Retry-After and 403 as ApiError", async () => {
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "slow down" }), { status: 429, headers: { "content-type": "application/json", "Retry-After": "42" } }));
    await expect(api.login({ email: "a@b.c", password: "x" })).rejects.toMatchObject({ status: 429, retryAfter: 42 });
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "Wrong password" }), { status: 403, headers: { "content-type": "application/json" } }));
    await expect(api.exportData("bad")).rejects.toMatchObject({ status: 403 });
  });

  it("session and launch-gate endpoints", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(json([])));
    await api.sessions();
    await api.revokeSession(4);
    await api.revokeOtherSessions();
    await api.launchGate();
    expect(fetchMock.mock.calls.map((c) => `${c[1].method} ${c[0]}`)).toEqual([
      "GET /api/auth/sessions", "DELETE /api/auth/sessions/4", "POST /api/auth/sessions/revoke-all", "GET /api/launch-gate",
    ]);
  });

  it("honours NEXT_PUBLIC_API_URL and defaults to same-origin", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(json([])));
    await api.portfolios();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/portfolios");
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.example.com/");
    await api.portfolios();
    expect(fetchMock.mock.calls[1][0]).toBe("https://api.example.com/api/portfolios");
  });
});
