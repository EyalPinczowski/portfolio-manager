import { describe, expect, it } from "vitest";
import type { Holding, ImportRow, ProposedChange } from "@/lib/api";
import { groupRows, notInScreenshots, rowValueIls, updateTotals } from "@/lib/import-groups";

const row = (index: number, symbol: string | null, over: Partial<ImportRow> = {}): ImportRow => ({
  index, name: symbol ?? "x", symbol, quantity: 10, price: 10, value: 100, cost: null, currency: "ILS", unit: "ILS", flags: [], ...over,
});
const change = (row_index: number, symbol: string | null, type: ProposedChange["type"] = "buy"): ProposedChange => ({
  row_index, symbol, type, quantity: 1, amount: null, currency: "ILS",
});
const held = (symbol: string, value_ils: number) => ({ symbol, value_ils } as Holding);

describe("groupRows", () => {
  const rows = [row(0, "NEW1"), row(1, "OLD1"), row(2, "OLD2")];
  const changes = [change(0, "NEW1"), change(1, "OLD1", "sell")];

  it("splits new holdings, quantity changes and unchanged rows", () => {
    const g = groupRows(rows, changes, new Set(["OLD1", "OLD2"]));
    expect(g.new.map((r) => r.symbol)).toEqual(["NEW1"]);
    expect(g.changed.map((r) => r.symbol)).toEqual(["OLD1"]);
    expect(g.unchanged.map((r) => r.symbol)).toEqual(["OLD2"]);
  });

  it("before the holdings are known, a row is never called new", () => {
    const g = groupRows(rows, changes, null);
    expect(g.new).toEqual([]);
    expect(g.changed.map((r) => r.symbol)).toEqual(["NEW1", "OLD1"]);
  });
});

describe("notInScreenshots", () => {
  it("returns only the row_index -1 choices", () => {
    expect(notInScreenshots([change(0, "A"), change(-1, "B", "keep")]).map((c) => c.symbol)).toEqual(["B"]);
  });
});

describe("updateTotals", () => {
  const holdings = [held("A", 1000), held("B", 500)];
  const rows = [row(0, "A", { value: 1200 }), row(1, "C", { value: 300 })];

  it("partial: holdings missing from the screenshots stay in the total", () => {
    expect(updateTotals(rows, [], holdings, "partial", 3.5)).toEqual({ before: 1500, after: 1500 - 1000 + 1200 + 300 });
  });

  it("full: sold or withdrawn holdings leave the total, kept ones stay", () => {
    expect(updateTotals(rows, [change(-1, "B", "sell")], holdings, "full", 3.5).after).toBe(1200 + 300);
    expect(updateTotals(rows, [change(-1, "B", "keep")], holdings, "full", 3.5).after).toBe(1200 + 300 + 500);
  });

  it("never invents a number: an unreadable value gives null", () => {
    expect(updateTotals([row(0, "A", { value: null, price: null })], [], holdings, "partial", 3.5).after).toBeNull();
    expect(rowValueIls(row(0, "A", { currency: "USD", unit: "USD" }), null)).toBeNull();
  });

  it("agorot prices are divided by 100 when the value is missing; USD uses the FX rate", () => {
    expect(rowValueIls(row(0, "A", { value: null, quantity: 10, price: 6500, unit: "agorot" }), null)).toBe(650);
    expect(rowValueIls(row(0, "A", { value: 100, currency: "USD", unit: "USD" }), 3.5)).toBe(350);
  });
});
