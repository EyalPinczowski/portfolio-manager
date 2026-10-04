import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, setCsrfToken, type ImportRow } from "@/lib/api";
import { rowProblems, sanitizeRow } from "@/lib/import-rows";

const row = (over: Partial<ImportRow> = {}): ImportRow => ({
  index: 0, name: "Teva", symbol: null, tase_number: null, quantity: 10, price: 5, value: 50, cost: null, currency: "ILS", unit: "ILS", flags: [], ...over,
});

describe("sanitizeRow", () => {
  it("derives currency from unit exactly like the server (agorot -> ILS, USD -> USD, ILS -> ILS)", () => {
    expect(sanitizeRow(row({ currency: "USD", unit: "ILS" })).currency).toBe("ILS"); // the row that used to be rejected
    expect(sanitizeRow(row({ currency: "USD", unit: "agorot" })).currency).toBe("ILS");
    expect(sanitizeRow(row({ currency: "ILS", unit: "USD" })).currency).toBe("USD");
    expect(sanitizeRow(row({ currency: "USD", unit: "USD" })).currency).toBe("USD");
  });
  it("drops NaN/Infinity and negatives, clamps to the bounds", () => {
    const r = sanitizeRow(row({ quantity: NaN, price: Infinity, value: -3, cost: 5e20 }));
    expect(r.quantity).toBeNull();
    expect(r.price).toBeNull();
    expect(r.value).toBeNull();
    expect(r.cost).toBe(1e15);
    expect(sanitizeRow(row({ quantity: 5e12 })).quantity).toBe(1e12);
    expect(sanitizeRow(row({ quantity: 0 })).quantity).toBe(0);
  });
  it("cleans symbol, tase number, name, index, candidates and flags", () => {
    const r = sanitizeRow(row({
      symbol: " teva.ta ", tase_number: "12", name: `${"x".repeat(250)} 1234567`, index: 99999,
      candidates: Array.from({ length: 25 }, (_, i) => ({ symbol: `S${i}`, name: "n", score: i === 0 ? 150 : 50 })),
      flags: Array.from({ length: 14 }, () => "unmatched" as const),
    }));
    expect(r.symbol).toBe("TEVA.TA");
    expect(r.tase_number).toBeNull();
    expect(r.name.length).toBeLessThanOrEqual(200);
    expect(r.index).toBe(10000);
    expect(r.candidates).toHaveLength(20);
    expect(r.candidates![0].score).toBe(100);
    expect(r.flags).toHaveLength(12);
    expect(sanitizeRow(row({ symbol: "" })).symbol).toBeNull();
    expect(sanitizeRow(row({ symbol: "bad symbol!" })).symbol).toBeNull();
    expect(sanitizeRow(row({ tase_number: "629014" })).tase_number).toBe("629014");
    expect(sanitizeRow(row({ name: "acct 123456789 x" })).name).toBe("acct *** x");
  });
});

describe("rowProblems", () => {
  it("maps 422 loc paths to row position and field", () => {
    const e = new ApiError(422, "Validation error", undefined, { detail: [
      { type: "value_error", loc: ["body", "rows", 2, "unit"], msg: "bad unit" },
      { type: "x", loc: ["body", "other"], msg: "ignored" },
    ] });
    expect(rowProblems(e)).toEqual([{ position: 2, field: "unit", msg: "bad unit" }]);
    expect(rowProblems(new Error("x"))).toEqual([]);
  });
});

describe("importRows sends consistent rows", () => {
  const fetchMock = vi.fn();
  beforeEach(() => { vi.stubEnv("NEXT_PUBLIC_API_MOCK", ""); vi.stubGlobal("fetch", fetchMock); fetchMock.mockReset(); setCsrfToken("t"); });
  afterEach(() => { vi.unstubAllEnvs(); vi.unstubAllGlobals(); });

  it("posts currency ILS for a row with unit ILS that was parsed as USD, and no NaN", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ id: 1 }), { status: 200, headers: { "content-type": "application/json" } }));
    await api.importRows(1, [row({ currency: "USD", unit: "ILS", price: NaN })]);
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.rows[0]).toMatchObject({ currency: "ILS", unit: "ILS", price: null });
  });

  it("patchImport sanitises rows too", async () => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ id: 1 }), { status: 200, headers: { "content-type": "application/json" } }));
    await api.patchImport(1, { rows: [row({ currency: "USD", unit: "agorot" })] });
    expect(JSON.parse(fetchMock.mock.calls[0][1].body as string).rows[0].currency).toBe("ILS");
  });

  it("keeps the 422 body on the ApiError", async () => {
    const detail = [{ type: "value_error", loc: ["body", "rows", 0, "quantity"], msg: "too big" }];
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail }), { status: 422, headers: { "content-type": "application/json" } }));
    const err = (await api.importRows(1, [row()]).catch((e) => e)) as ApiError;
    expect(err.status).toBe(422);
    expect(err.validation).toEqual(detail);
  });
});
