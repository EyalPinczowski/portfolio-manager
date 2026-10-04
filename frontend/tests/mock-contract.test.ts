/**
 * Mock fixtures vs the real API contract.
 *
 * Why Ajv against backend/openapi.json (and not compile-time types alone): the mock handler is routed by string
 * and returns `unknown`, so TypeScript cannot see when a fixture drifts from the backend (a renamed field, a
 * missing nullable, an object where the schema says array). OpenAPI 3.1 schemas are JSON Schema 2020-12, so Ajv
 * checks every mock response, at run time, against the exact schema FastAPI published: types, required keys,
 * nullability, enums, date formats. The static types in lib/api.ts are derived from the same file, so the two
 * layers agree.
 */
import { readFileSync } from "node:fs";
import path from "node:path";
import Ajv2020 from "ajv/dist/2020";
import addFormats from "ajv-formats";
import { describe, expect, it } from "vitest";
import { setMockTrackState, resetMockXrayRules } from "@/lib/mock-trackrecord";
import { setMockDividendsState } from "@/lib/mock-funds";
import { mockRequest, MOCK_TURNSTILE_TOKEN, WRONG_PASSWORD } from "@/lib/mock";
import { ApiError } from "@/lib/errors";

type Json = Record<string, unknown>;
const openapi = JSON.parse(readFileSync(path.resolve(__dirname, "../../backend/openapi.json"), "utf8")) as {
  paths: Record<string, Record<string, { responses?: Record<string, { content?: Record<string, { schema?: Json }> }> }>>;
  components: { schemas: Record<string, Json> };
};

const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats(ajv);
ajv.addSchema(openapi as unknown as Json, "openapi");

/** Response schema (as a $ref into the OpenAPI doc) for the first 2xx JSON response of an operation. */
function responseSchema(method: string, apiPath: string): Json | null {
  const op = openapi.paths[`/api${apiPath}`]?.[method.toLowerCase()];
  if (!op?.responses) return null;
  const code = Object.keys(op.responses).find((c) => c.startsWith("2"));
  const schema = code ? op.responses[code]?.content?.["application/json"]?.schema : undefined;
  if (!schema) return null;
  const json = JSON.stringify(schema).replaceAll('"#/', '"openapi#/');
  return JSON.parse(json) as Json;
}

const validate = (schema: Json, data: unknown, label: string) => {
  const fn = ajv.compile(schema);
  const ok = fn(data);
  expect(ok ? null : ajv.errorsText(fn.errors, { separator: "\n  " }), label).toBeNull();
};

const png = () => new Blob([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], { type: "image/png" });

interface Case { method: string; path: string; api: string; body?: unknown }
const CASES: Case[] = [
  { method: "GET", path: "/auth/me", api: "/auth/me" },
  { method: "GET", path: "/portfolios", api: "/portfolios" },
  { method: "GET", path: "/portfolios/combined/summary", api: "/portfolios/combined/summary" },
  { method: "GET", path: "/portfolios/1/summary", api: "/portfolios/{portfolio_id}/summary" },
  { method: "GET", path: "/portfolios/2/summary", api: "/portfolios/{portfolio_id}/summary" },
  { method: "GET", path: "/portfolios/1/holdings", api: "/portfolios/{portfolio_id}/holdings" },
  { method: "GET", path: "/portfolios/2/holdings", api: "/portfolios/{portfolio_id}/holdings" },
  { method: "PATCH", path: "/portfolios/1/holdings/1", api: "/portfolios/{portfolio_id}/holdings/{holding_id}", body: { horizon: "3m" } },
  { method: "PATCH", path: "/portfolios/1", api: "/portfolios/{portfolio_id}", body: { name: "x" } },
  { method: "GET", path: "/portfolios/1/post-mortem", api: "/portfolios/{portfolio_id}/post-mortem" }, // no expectation set
  { method: "PATCH", path: "/portfolios/1", api: "/portfolios/{portfolio_id}", body: { expected_return_pct: 12, expected_return_horizon_months: 12 } },
  { method: "GET", path: "/portfolios/1/post-mortem", api: "/portfolios/{portfolio_id}/post-mortem" }, // ok, reconciliation, not_recorded findings
  { method: "GET", path: "/portfolios/1/post-mortem?start=2026-09-01&end=2026-10-03", api: "/portfolios/{portfolio_id}/post-mortem" },
  { method: "GET", path: "/portfolios/2/post-mortem", api: "/portfolios/{portfolio_id}/post-mortem" }, // not_enough_history
  { method: "PATCH", path: "/portfolios/1", api: "/portfolios/{portfolio_id}", body: { expected_return_pct: null, expected_return_horizon_months: null } },
  { method: "GET", path: "/portfolios/1/xray", api: "/portfolios/{portfolio_id}/xray" },
  { method: "GET", path: "/portfolios/1/xray-rules", api: "/portfolios/{portfolio_id}/xray-rules" },
  { method: "PATCH", path: "/portfolios/1/xray-rules", api: "/portfolios/{portfolio_id}/xray-rules", body: { rules: [{ rule: "sector", enabled: false }, { rule: "concentration", threshold_pct: 20 }] } },
  { method: "GET", path: "/portfolios/1/xray", api: "/portfolios/{portfolio_id}/xray" }, // rule off, override
  { method: "PATCH", path: "/portfolios/1/xray-rules", api: "/portfolios/{portfolio_id}/xray-rules", body: { rules: [{ rule: "sector", enabled: true, threshold_pct: null }, { rule: "concentration", threshold_pct: null }] } },
  { method: "GET", path: "/track-record", api: "/track-record" },
  { method: "GET", path: "/portfolios/1/heatmap", api: "/portfolios/{portfolio_id}/heatmap" },
  { method: "GET", path: "/risk/presets", api: "/risk/presets" },
  { method: "GET", path: "/securities/search?q=teva", api: "/securities/search" },
  { method: "GET", path: "/holdings/1/scorecard", api: "/holdings/{holding_id}/scorecard" },
  { method: "GET", path: "/holdings/11/scorecard", api: "/holdings/{holding_id}/scorecard" },
  { method: "GET", path: "/holdings/1/exit-levels", api: "/holdings/{holding_id}/exit-levels" }, // levels
  { method: "GET", path: "/holdings/2/exit-levels", api: "/holdings/{holding_id}/exit-levels" }, // needs_horizon
  { method: "GET", path: "/holdings/2/exit-levels?horizon=1m&risk=balanced", api: "/holdings/{holding_id}/exit-levels" }, // what-if
  { method: "GET", path: "/holdings/1/exit-levels?risk=conservative", api: "/holdings/{holding_id}/exit-levels" }, // plan: conservative
  { method: "GET", path: "/holdings/1/exit-levels?risk=aggressive", api: "/holdings/{holding_id}/exit-levels" }, // plan: aggressive
  { method: "GET", path: "/holdings/10/exit-levels", api: "/holdings/{holding_id}/exit-levels" }, // levels, USD
  { method: "GET", path: "/holdings/11/exit-levels?horizon=3m", api: "/holdings/{holding_id}/exit-levels" }, // no_levels (stale)
  { method: "POST", path: "/portfolios/1/exit-review", api: "/portfolios/{portfolio_id}/exit-review", body: {} },
  { method: "POST", path: "/portfolios/2/exit-review", api: "/portfolios/{portfolio_id}/exit-review", body: { horizon: "1w", risk: "balanced" } },
  { method: "GET", path: "/analyze/AMD", api: "/analyze/{symbol}" }, // incomplete: needs everything
  { method: "GET", path: "/analyze/XOM?portfolio_id=1&amount=2000&currency=USD&horizon=3m", api: "/analyze/{symbol}" }, // fits
  { method: "GET", path: "/analyze/XOM?portfolio_id=1&amount=60000&currency=USD&horizon=3m", api: "/analyze/{symbol}" }, // fits smaller
  { method: "GET", path: "/analyze/AMD?portfolio_id=1&amount=2000&currency=USD&horizon=3m", api: "/analyze/{symbol}" }, // does not fit (tech sector cap)
  { method: "GET", path: "/analyze/NVDA?portfolio_id=1&amount=5000&currency=ILS", api: "/analyze/{symbol}" }, // held, horizon from the holding
  { method: "GET", path: "/analyze/TEVA.TA?portfolio_id=1&amount=500&currency=ILS", api: "/analyze/{symbol}" }, // held
  { method: "GET", path: "/analyze/ARNA.TA?portfolio_id=1&amount=500&currency=ILS&horizon=1m", api: "/analyze/{symbol}" }, // stale, no levels
  { method: "POST", path: "/analyze/AMD/ask", api: "/analyze/{symbol}/ask", body: { question: "What is the trend?" } },
  { method: "POST", path: "/analyze/AMD/ask", api: "/analyze/{symbol}/ask", body: { question: "Should I buy it?", notes: "n" } },
  { method: "GET", path: "/search-history", api: "/search-history" },
  { method: "GET", path: "/watchlist", api: "/watchlist" },
  { method: "POST", path: "/watchlist", api: "/watchlist", body: { symbol: "LUMI.TA" } },
  { method: "GET", path: "/alerts", api: "/alerts" },
  { method: "POST", path: "/alerts", api: "/alerts", body: { symbol: "TEVA.TA", op: "above", price: 70 } },
  { method: "GET", path: "/notifications", api: "/notifications" },
  { method: "POST", path: "/portfolios/1/imports", api: "/portfolios/{portfolio_id}/imports", body: png() },
  { method: "POST", path: "/portfolios/1/imports/rows", api: "/portfolios/{portfolio_id}/imports/rows", body: { rows: [{ index: 0, name: "טבע", symbol: "TEVA.TA", quantity: 1, price: 2, value: 2, currency: "ILS", unit: "ILS", flags: [] }] } },
  { method: "POST", path: "/portfolios/1/imports/rows", api: "/portfolios/{portfolio_id}/imports/rows", body: { scope: "full", rows: [
    { index: 0, name: "ACME", symbol: "ACME", quantity: null, price: 1, value: 1, currency: "USD", unit: "USD", flags: ["quantity_uncertain", "quantity_fractional", "cost_inferred", "duplicate_removed", "conflict"], exchange: "NASDAQ", conflict: { price: 1, value: 2, quantity: null } },
  ] } },
  { method: "PATCH", path: "/imports/1", api: "/imports/{draft_id}", body: { scope: "partial" } },
  { method: "PATCH", path: "/imports/1", api: "/imports/{draft_id}", body: { scope: "full" } },
  { method: "POST", path: "/portfolios/1/holdings", api: "/portfolios/{portfolio_id}/holdings", body: { symbol: "ZZCONTRACT.TA", quantity: 3, avg_cost: 10, cost_currency: "ILS", horizon: null } },
  { method: "POST", path: "/imports/1/confirm", api: "/imports/{draft_id}/confirm" },
  { method: "GET", path: "/imports/1", api: "/imports/{draft_id}" },
  { method: "PATCH", path: "/imports/1", api: "/imports/{draft_id}", body: {} },
  { method: "GET", path: "/settings", api: "/settings" },
  { method: "PATCH", path: "/settings", api: "/settings", body: { theme: "dark", week_start_day: "monday", weekly_review: { time: "19:30" } } },
  { method: "PATCH", path: "/settings", api: "/settings", body: { quiet_hours: { start: "22:00", end: "07:00" } } },
  { method: "PATCH", path: "/settings", api: "/settings", body: { idea_alerts: { min_confidence: 0.6, horizon: "3m", risk_preset: "balanced", markets: ["US"], asset_types: ["stock"], max_per_day: 3, quiet_hours: null } } },
  { method: "PATCH", path: "/settings", api: "/settings", body: { idea_alerts: null, quiet_hours: null } },
  { method: "GET", path: "/telegram/status", api: "/telegram/status" },
  { method: "POST", path: "/telegram/link-code", api: "/telegram/link-code" },
  { method: "GET", path: "/admin/users", api: "/admin/users" },
  { method: "POST", path: "/admin/users/2/disable", api: "/admin/users/{user_id}/disable" },
  { method: "POST", path: "/admin/users/2/enable", api: "/admin/users/{user_id}/enable" },
  { method: "GET", path: "/admin/invites", api: "/admin/invites" },
  { method: "POST", path: "/admin/invites", api: "/admin/invites", body: { days: 7 } },
  { method: "POST", path: "/portfolios/1/buy-ideas", api: "/portfolios/{portfolio_id}/buy-ideas", body: { amount: 5000, currency: "USD", horizon: "3m", risk: "balanced", markets: ["US", "TASE", "CRYPTO"], asset_types: ["stock", "etf", "crypto"] } },
  { method: "POST", path: "/portfolios/1/buy-ideas", api: "/portfolios/{portfolio_id}/buy-ideas", body: { amount: 5000, currency: "ILS", horizon: "1m", risk: "aggressive", markets: ["US"], asset_types: ["stock"], exclude_symbols: ["XOM"] } },
  { method: "GET", path: "/funds/search?q=gemel", api: "/funds/search" }, // ok
  { method: "GET", path: "/funds/search?q=qqqq", api: "/funds/search" }, // no_data
  { method: "GET", path: "/funds/search?q=unavailable", api: "/funds/search" },
  { method: "GET", path: "/funds/search?q=ratelimit", api: "/funds/search" },
  { method: "GET", path: "/funds/1001", api: "/funds/{fund_id}" }, // complete, category average
  { method: "GET", path: "/funds/1002", api: "/funds/{fund_id}" }, // stale, gap, not enough months
  { method: "GET", path: "/funds/1003", api: "/funds/{fund_id}" }, // young fund
  { method: "GET", path: "/portfolios/1/dividends", api: "/portfolios/{portfolio_id}/dividends" }, // every symbol status, estimate
  { method: "GET", path: "/portfolios/2/dividends", api: "/portfolios/{portfolio_id}/dividends" }, // no data, total null
  { method: "POST", path: "/portfolios/2/holdings", api: "/portfolios/{portfolio_id}/holdings", body: { symbol: "GEMEL-1001", quantity: 1, manual_value_ils: 50000, manual_value_as_of: "2026-09-30", fund_name: "Kupat Gemel Equities Track", track: "Equities" } },
  { method: "POST", path: "/portfolios/2/holdings", api: "/portfolios/{portfolio_id}/holdings", body: { symbol: "GEMEL-1003", quantity: 1, avg_cost: 1000, cost_currency: "ILS" } }, // cost only
  { method: "POST", path: "/portfolios/2/holdings", api: "/portfolios/{portfolio_id}/holdings", body: { symbol: "GEMEL-1002", quantity: 1 } }, // no value
  { method: "GET", path: "/auth/sessions", api: "/auth/sessions" },
  { method: "GET", path: "/launch-gate", api: "/launch-gate" },
  { method: "GET", path: "/health", api: "/health" },
  { method: "POST", path: "/auth/login", api: "/auth/login", body: { email: "demo@example.com", password: "x" } },
];

describe("mock fixtures match backend/openapi.json", () => {
  it.each(CASES)("$method $path", ({ method, path: p, api, body }) => {
    const data = JSON.parse(JSON.stringify(mockRequest(method, p, body) ?? null));
    const real = responseSchema(method, api);
    expect(real, `no schema for ${method} ${api}`).toBeTruthy();
    validate(real!, data, `${method} ${p}`);
  });

  it.each(["full", "no_data", "unavailable"] as const)("GET /portfolios/1/dividends in state %s", (st) => {
    setMockDividendsState(st);
    try {
      const data = JSON.parse(JSON.stringify(mockRequest("GET", "/portfolios/1/dividends")));
      validate(responseSchema("GET", "/portfolios/{portfolio_id}/dividends")!, data, `dividends ${st}`);
      if (st !== "full") expect(data.income_estimate.total_ils).toBeNull(); // never 0 when there is no data
    } finally { setMockDividendsState("full"); }
  });

  it("a fund holding: GET holdings carries `fund`, exit levels say fund_no_levels with no numbers", () => {
    const rows = JSON.parse(JSON.stringify(mockRequest("GET", "/portfolios/2/holdings"))) as { id: number; symbol: string; fund?: { value_basis: string } }[];
    const funds = rows.filter((r) => r.symbol.startsWith("GEMEL-"));
    expect(funds.map((f) => f.fund?.value_basis).sort()).toEqual(["cost_only", "manual_value", "no_value"]);
    for (const f of funds) {
      const lv = JSON.parse(JSON.stringify(mockRequest("GET", `/holdings/${f.id}/exit-levels`)));
      validate(responseSchema("GET", "/holdings/{holding_id}/exit-levels")!, lv, `fund exit levels ${f.symbol}`);
      expect(lv.reason_code).toBe("fund_no_levels");
      expect(lv.stop ?? null).toBeNull();
      expect(lv.take_profits).toEqual([]);
    }
  });

  it.each(["not_started", "none_ended", "ready"] as const)("GET /track-record in state %s", (st) => {
    setMockTrackState(st);
    try {
      const data = JSON.parse(JSON.stringify(mockRequest("GET", "/track-record")));
      validate(responseSchema("GET", "/track-record")!, data, st);
      expect(data.state).toBe(st);
    } finally { setMockTrackState("ready"); }
  });

  it("PATCH xray-rules out of bounds is a 422 with the bounds; both X-ray states validate", () => {
    resetMockXrayRules();
    try { mockRequest("PATCH", "/portfolios/1/xray-rules", { rules: [{ rule: "concentration", threshold_pct: 99 }] }); expect.fail("no 422"); } catch (e) {
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).status).toBe(422);
      expect((e as ApiError).code).toBe("threshold_out_of_bounds");
    }
    const x = mockRequest("GET", "/portfolios/1/xray") as { rules: { state: string }[] };
    expect(new Set(x.rules.map((r) => r.state)).has("breach")).toBe(true);
    expect(x.rules.some((r) => r.state === "ok")).toBe(true);
    mockRequest("PATCH", "/portfolios/1/xray-rules", { rules: [{ rule: "sector", enabled: false }] });
    const y = mockRequest("GET", "/portfolios/1/xray") as { rules: { rule: string; state: string }[] };
    expect(y.rules.find((r) => r.rule === "sector")?.state).toBe("off");
    resetMockXrayRules();
  });

  it("covers the nullable cases the UI must survive", () => {
    const holdings = mockRequest("GET", "/portfolios/1/holdings") as { pnl: unknown; price_stale: boolean; score_card: { confidence: number } }[];
    expect(holdings.some((h) => h.pnl === null)).toBe(true);
    expect(holdings.some((h) => h.price_stale)).toBe(true);
    const fresh = mockRequest("GET", "/portfolios/2/summary") as { since_start_date: unknown };
    expect(fresh.since_start_date).toBeNull();
    const combined = mockRequest("GET", "/portfolios/1/summary") as { since_start_series: { sp500_pct: number | null; ta125_pct: number | null }[] };
    expect(combined.since_start_series.some((p) => p.sp500_pct === null && p.ta125_pct === null)).toBe(true);
    const sc = mockRequest("GET", "/holdings/11/scorecard") as { available: boolean };
    expect(sc.available).toBe(false);
  });

  it("the 'keep' choice, the scope and the screenshot-update fields are in the mock data and valid", () => {
    const full = mockRequest("POST", "/portfolios/1/imports/rows", { scope: "full", rows: [] }) as { scope: string; proposed_changes: { type: string; row_index: number }[] };
    expect(full.scope).toBe("full");
    expect(full.proposed_changes.length).toBeGreaterThan(0);
    expect(full.proposed_changes.every((c) => c.row_index === -1 && c.type === "keep")).toBe(true);
    const partial = mockRequest("POST", "/portfolios/1/imports/rows", { rows: [] }) as { scope: string; proposed_changes: unknown[] };
    expect(partial.scope).toBe("partial");
    expect(partial.proposed_changes).toEqual([]);
    const ps = mockRequest("GET", "/portfolios") as { last_screenshot_update_at: string | null; screenshot_update_stale: boolean }[];
    expect(ps.some((p) => p.last_screenshot_update_at !== null)).toBe(true);
    expect(ps.every((p) => typeof p.screenshot_update_stale === "boolean")).toBe(true);
  });

  it("what the client sends validates against the request schemas (scope, flags, conflict, manual holding)", () => {
    const body = (name: string, data: unknown) => validate({ $ref: `openapi#/components/schemas/${name}` }, data, name);
    body("ImportRowsBody", { scope: "full", rows: [{ index: 0, name: "A", symbol: "A", quantity: null, flags: ["conflict", "quantity_uncertain"], conflict: { price: 1, value: 2, quantity: null }, exchange: "NYSE" }] });
    body("ImportPatch", { scope: "partial", rows: [], proposed_changes: [{ row_index: -1, symbol: "A", type: "keep", quantity: 1, amount: null, currency: "ILS" }] });
    body("HoldingCreate", { symbol: "AAPL", quantity: 1.5, avg_cost: null, cost_currency: null, horizon: null });
    expect(() => body("ImportRowsBody", { scope: "everything", rows: [] })).toThrow(); // the validator really rejects a wrong scope
  });

  it("exit levels: the mock covers all three statuses, and the review request validates", () => {
    const st = (id: number, q = "") => (mockRequest("GET", `/holdings/${id}/exit-levels${q}`) as { status: string }).status;
    expect(st(1)).toBe("levels");
    expect(st(2)).toBe("needs_horizon");
    expect(st(11, "?horizon=3m")).toBe("no_levels");
    expect(st(2, "?horizon=1y")).toBe("levels"); // what-if override
    validate({ $ref: "openapi#/components/schemas/ExitReviewIn" }, { horizon: "1y", risk: "balanced", prior_stops: { "TEVA.TA": 50 } }, "ExitReviewIn");
  });

  it("post-mortem: every state is in the mock, and a half-set expectation is rejected", () => {
    const pm = (id: number) => mockRequest("GET", `/portfolios/${id}/post-mortem`) as { status: string; expectation: { status: string }; findings: { status: string }[]; gap_vs_benchmark: { reconciles: boolean; residual_pp: number } };
    expect(pm(1).expectation.status).toBe("needs_expectation");
    expect(pm(2).status).toBe("not_enough_history");
    mockRequest("PATCH", "/portfolios/1", { expected_return_pct: 8, expected_return_horizon_months: 12 });
    expect(pm(1).expectation.status).toBe("ok");
    expect(pm(1).findings.filter((f) => f.status === "not_recorded").length).toBeGreaterThanOrEqual(3);
    expect(pm(1).gap_vs_benchmark.reconciles).toBe(false); // residual stays visible
    expect(() => mockRequest("PATCH", "/portfolios/1", { expected_return_pct: 8, expected_return_horizon_months: null })).toThrow(ApiError);
    mockRequest("PATCH", "/portfolios/1", { expected_return_pct: null, expected_return_horizon_months: null });
    validate({ $ref: "openapi#/components/schemas/PortfolioPatch" }, { expected_return_pct: 8, expected_return_horizon_months: 12 }, "PortfolioPatch");
  });

  it("analyze: every status and the 404 / list behaviour", () => {
    const a = (q: string) => mockRequest("GET", `/analyze/${q}`) as { portfolio_fit: { status: string; needs_input: string[]; levels?: { status: string } }; needs_input: string[] };
    expect(a("AMD").portfolio_fit.status).toBe("incomplete");
    expect(a("AMD").needs_input).toEqual(["portfolio_id", "amount", "currency", "horizon"]);
    expect(a("XOM?portfolio_id=1&amount=2000&currency=USD&horizon=3m").portfolio_fit.status).toBe("fits");
    expect(a("XOM?portfolio_id=1&amount=60000&currency=USD&horizon=3m").portfolio_fit.status).toBe("fits_smaller");
    expect(a("AMD?portfolio_id=1&amount=2000&currency=USD&horizon=3m").portfolio_fit.status).toBe("does_not_fit");
    expect(a("ARNA.TA?portfolio_id=1&amount=500&currency=ILS&horizon=1m").portfolio_fit.levels?.status).toBe("no_levels");
    expect(a("NVDA?portfolio_id=1&amount=100&currency=ILS").needs_input).toEqual([]); // horizon comes from the holding
    expect(() => mockRequest("GET", "/analyze/NOPE")).toThrow(ApiError);
    try { mockRequest("GET", "/analyze/NOPE"); } catch (e) { expect((e as ApiError).status).toBe(404); }
    mockRequest("DELETE", "/search-history/AMD");
    expect((mockRequest("GET", "/search-history") as { symbol: string }[]).some((h) => h.symbol === "AMD")).toBe(false);
    mockRequest("DELETE", "/search-history");
    expect(mockRequest("GET", "/search-history")).toEqual([]);
    mockRequest("DELETE", "/watchlist/NVDA");
    expect((mockRequest("GET", "/watchlist") as { symbol: string }[]).some((h) => h.symbol === "NVDA")).toBe(false);
    validate({ $ref: "openapi#/components/schemas/AskIn" }, { question: "q", notes: null }, "AskIn");
  });

  it("manual create: duplicate -> 409, bad symbol -> 422", () => {
    expect(() => mockRequest("POST", "/portfolios/1/holdings", { symbol: "TEVA.TA", quantity: 1 })).toThrowError(/already in this portfolio/);
    expect(() => mockRequest("POST", "/portfolios/1/holdings", { symbol: "bad symbol", quantity: 1 })).toThrow(ApiError);
  });

  it("weeks start on Sunday (mock data and summary.week_start)", () => {
    const s = mockRequest("GET", "/portfolios/1/summary") as { week_start: string; weekly_bars: { week_start: string }[] };
    const dow = (d: string) => new Date(`${d}T00:00:00Z`).getUTCDay();
    expect(dow(s.week_start)).toBe(0);
    expect(s.weekly_bars.length).toBeGreaterThan(0);
    for (const b of s.weekly_bars) expect(dow(b.week_start), b.week_start).toBe(0);
  });

  it("export and delete need the password (wrong password -> 403)", () => {
    for (const [method, p] of [["POST", "/me/export"], ["DELETE", "/me"]] as const) {
      expect(() => mockRequest(method, p, { password: WRONG_PASSWORD })).toThrow(ApiError);
      try { mockRequest(method, p, { password: WRONG_PASSWORD }); } catch (e) { expect((e as ApiError).status).toBe(403); }
      expect(() => mockRequest(method, p, { password: "correct horse" })).not.toThrow();
    }
  });

  it("scorecard explanations are the typed Explanation (version, contributions, annotations, sources)", () => {
    const sc = mockRequest("GET", "/holdings/1/scorecard") as { explanation: Record<string, unknown>; signals: { explanation: Record<string, unknown> }[] };
    expect(sc.explanation.version).toBe(1);
    expect((sc.explanation.contributions as unknown[]).length).toBeGreaterThan(0);
    expect(sc.signals.some((x) => (x.explanation.annotations as unknown[] | undefined)?.length)).toBe(true);
    expect(sc.signals.every((x) => (x.explanation.sources as unknown[] | undefined)?.length)).toBe(true);
  });

  const errorBody = (fn: () => unknown): { status: number; body: unknown; retryAfter?: number } => {
    try { fn(); } catch (e) { const a = e as ApiError; return { status: a.status, body: a.body, retryAfter: a.retryAfter }; }
    throw new Error("expected an ApiError");
  };

  it("login 403 turnstile_required body matches ChallengeRequiredOut; a valid token logs in", () => {
    const e = errorBody(() => mockRequest("POST", "/auth/login", { email: "challenge@example.com", password: "x" }));
    expect(e.status).toBe(403);
    validate({ $ref: "openapi#/components/schemas/ChallengeRequiredOut" }, e.body, "403 body");
    expect((e.body as { site_key: string }).site_key).toBeTruthy();
    const bad = errorBody(() => mockRequest("POST", "/auth/login", { email: "challenge@example.com", password: "x", turnstile_token: "nope" }));
    expect(bad.status).toBe(403);
    expect(() => mockRequest("POST", "/auth/login", { email: "challenge@example.com", password: "x", turnstile_token: MOCK_TURNSTILE_TOKEN })).not.toThrow();
  });

  it("login 429 carries Retry-After", () => {
    expect(errorBody(() => mockRequest("POST", "/auth/login", { email: "ratelimit@example.com", password: "x" }))).toMatchObject({ status: 429, retryAfter: 30 });
  });

  it("422 for an inconsistent import row matches HTTPValidationError and carries only type/loc/msg", () => {
    const row = { index: 0, name: "x", symbol: "A", quantity: 1, price: 1, value: 1, currency: "USD", unit: "ILS", flags: [] };
    const e = errorBody(() => mockRequest("POST", "/portfolios/1/imports/rows", { rows: [row] }));
    expect(e.status).toBe(422);
    validate({ $ref: "openapi#/components/schemas/HTTPValidationError" }, e.body, "422 body");
    for (const d of (e.body as { detail: Record<string, unknown>[] }).detail) expect(Object.keys(d).sort()).toEqual(["loc", "msg", "type"]);
    expect(errorBody(() => mockRequest("PATCH", "/imports/1", { rows: [row] })).status).toBe(422);
  });

  it("rejects a non-image upload with 415 like the server", () => {
    expect(() => mockRequest("POST", "/portfolios/1/imports", new Blob(["x"], { type: "application/pdf" }))).toThrow(/Unsupported/);
  });
});
