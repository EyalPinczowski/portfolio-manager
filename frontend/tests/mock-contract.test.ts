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
import { mockRequest, WRONG_PASSWORD } from "@/lib/mock";
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
  { method: "GET", path: "/portfolios/1/xray", api: "/portfolios/{portfolio_id}/xray" },
  { method: "GET", path: "/portfolios/1/heatmap", api: "/portfolios/{portfolio_id}/heatmap" },
  { method: "GET", path: "/risk/presets", api: "/risk/presets" },
  { method: "GET", path: "/securities/search?q=teva", api: "/securities/search" },
  { method: "GET", path: "/holdings/1/scorecard", api: "/holdings/{holding_id}/scorecard" },
  { method: "GET", path: "/holdings/11/scorecard", api: "/holdings/{holding_id}/scorecard" },
  { method: "GET", path: "/alerts", api: "/alerts" },
  { method: "POST", path: "/alerts", api: "/alerts", body: { symbol: "TEVA.TA", op: "above", price: 70 } },
  { method: "GET", path: "/notifications", api: "/notifications" },
  { method: "POST", path: "/portfolios/1/imports", api: "/portfolios/{portfolio_id}/imports", body: png() },
  { method: "POST", path: "/portfolios/1/imports/rows", api: "/portfolios/{portfolio_id}/imports/rows", body: { rows: [{ index: 0, name: "טבע", symbol: "TEVA.TA", quantity: 1, price: 2, value: 2, currency: "ILS", unit: "ILS", flags: [] }] } },
  { method: "GET", path: "/imports/1", api: "/imports/{draft_id}" },
  { method: "PATCH", path: "/imports/1", api: "/imports/{draft_id}", body: {} },
  { method: "GET", path: "/auth/sessions", api: "/auth/sessions" },
  { method: "GET", path: "/launch-gate", api: "/launch-gate" },
];

describe("mock fixtures match backend/openapi.json", () => {
  it.each(CASES)("$method $path", ({ method, path: p, api, body }) => {
    const data = JSON.parse(JSON.stringify(mockRequest(method, p, body) ?? null));
    const real = responseSchema(method, api);
    expect(real, `no schema for ${method} ${api}`).toBeTruthy();
    validate(real!, data, `${method} ${p}`);
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

  it("rejects a non-image upload with 415 like the server", () => {
    expect(() => mockRequest("POST", "/portfolios/1/imports", new Blob(["x"], { type: "application/pdf" }))).toThrow(/Unsupported/);
  });
});
