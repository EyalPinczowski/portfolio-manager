import { afterEach, describe, expect, it } from "vitest";
import { mockRequest } from "@/lib/mock";
import { resetMockSettings } from "@/lib/mock-settings";
import { ApiError } from "@/lib/errors";
import type { BuyIdeasOut, Settings, SkippedItem } from "@/lib/api";

const status = (fn: () => unknown): number => { try { fn(); } catch (e) { return (e as ApiError).status; } return 0; };
const ideas = (over: object = {}) => mockRequest("POST", "/portfolios/1/buy-ideas", { amount: 5000, currency: "USD", horizon: "3m", risk: "balanced", markets: ["US", "TASE", "CRYPTO"], asset_types: ["stock", "etf", "crypto"], ...over }) as BuyIdeasOut;
const FILTER = { min_confidence: 0.6, horizon: "3m", risk_preset: "balanced", markets: ["US"], asset_types: ["stock"], max_per_day: 3, quiet_hours: null };

describe("mock settings, telegram, admin and buy-ideas", () => {
  afterEach(() => { window.localStorage.removeItem("pm.mock"); resetMockSettings(); });

  it("the Buy-alerts filter is 'not_set' with no defaults until saved, and null clears it", () => {
    const s = () => mockRequest("GET", "/settings") as Settings;
    expect(s().idea_alerts).toEqual({ state: "not_set", filter: null });
    expect(s().quiet_hours).toBeNull();
    mockRequest("PATCH", "/settings", { idea_alerts: FILTER });
    expect(s().idea_alerts.state).toBe("set");
    mockRequest("PATCH", "/settings", { idea_alerts: null });
    expect(s().idea_alerts).toEqual({ state: "not_set", filter: null });
  });

  it("rejects an incomplete filter, a null on a non-clearable field and unknown keys with 422", () => {
    expect(status(() => mockRequest("PATCH", "/settings", { idea_alerts: { ...FILTER, max_per_day: undefined } }))).toBe(422);
    expect(status(() => mockRequest("PATCH", "/settings", { idea_alerts: { ...FILTER, markets: [] } }))).toBe(422);
    expect(status(() => mockRequest("PATCH", "/settings", { theme: null }))).toBe(422);
    expect(status(() => mockRequest("PATCH", "/settings", { surprise: 1 }))).toBe(422);
    expect(status(() => mockRequest("PATCH", "/settings", { quiet_hours: { start: "22:00", end: "22:00" } }))).toBe(422);
  });

  it("admin routes answer 403 to a member and work for an admin; an admin cannot be deactivated", () => {
    expect((mockRequest("GET", "/admin/users") as unknown[]).length).toBeGreaterThan(0);
    window.localStorage.setItem("pm.mock", "member");
    for (const [m, p] of [["GET", "/admin/users"], ["GET", "/admin/invites"], ["POST", "/admin/invites"], ["POST", "/admin/users/2/disable"]] as const) expect(status(() => mockRequest(m, p, {}))).toBe(403);
    window.localStorage.removeItem("pm.mock");
    expect(status(() => mockRequest("POST", "/admin/users/1/disable"))).toBe(400);
  });

  it("telegram link code is single-use shaped; no bot gives 503", () => {
    const c = mockRequest("POST", "/telegram/link-code") as { command: string; deep_link: string; ttl_minutes: number };
    expect(c.command).toMatch(/^\/start /);
    expect(c.ttl_minutes).toBe(15);
    window.localStorage.setItem("pm.mock", "no-bot");
    expect(status(() => mockRequest("POST", "/telegram/link-code"))).toBe(503);
    expect((mockRequest("GET", "/telegram/status") as { configured: boolean }).configured).toBe(false);
  });

  it("buy-ideas: every input is required, candidates are neutral, skipped carries codes incl. country_cap and stale_price", () => {
    for (const k of ["amount", "currency", "horizon", "risk", "markets", "asset_types"]) expect(status(() => ideas({ [k]: undefined })), k).toBe(422);
    const r = ideas();
    expect(r.launch_gate_open).toBe(false);
    expect(r.candidates.length).toBeGreaterThan(0);
    const codes = r.skipped.map((x: SkippedItem) => x.code);
    expect(codes).toEqual(expect.arrayContaining(["country_cap", "stale_price"]));
    expect(r.candidates.every((c) => c.explanation.summary && c.take_profits.length > 0)).toBe(true);
    expect(ideas({ markets: ["US"], asset_types: ["stock"], exclude_symbols: ["XOM"] }).skipped.some((x) => x.code === "excluded_by_user")).toBe(true);
    expect(status(() => mockRequest("POST", "/portfolios/99/buy-ideas", { amount: 1, currency: "ILS", horizon: "1m", risk: "balanced", markets: ["US"], asset_types: ["stock"] }))).toBe(404);
  });
});
