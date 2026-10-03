import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, setCsrfToken } from "@/lib/api";

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
