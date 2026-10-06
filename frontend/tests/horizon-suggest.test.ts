import { describe, expect, it } from "vitest";
import { suggestHorizon } from "@/lib/horizonSuggest";

describe("suggestHorizon", () => {
  it("funds, ETFs and bonds suggest 1y", () => {
    for (const t of ["etf", "fund", "bond"]) expect(suggestHorizon(t, "aggressive")).toEqual({ horizon: "1y", reason: "fund" });
  });
  it("crypto suggests 3m whatever the risk level", () => {
    expect(suggestHorizon("crypto", "very_conservative")).toEqual({ horizon: "3m", reason: "crypto" });
  });
  it("stocks follow the portfolio's risk preset", () => {
    expect(suggestHorizon("stock", "conservative")).toEqual({ horizon: "1y", reason: "stockLong" });
    expect(suggestHorizon("stock", "very_conservative")?.horizon).toBe("1y");
    expect(suggestHorizon("stock", "balanced")).toEqual({ horizon: "6m", reason: "stockMid" });
    expect(suggestHorizon("stock", "balanced_aggressive")?.horizon).toBe("6m");
    expect(suggestHorizon("stock", "aggressive")).toEqual({ horizon: "3m", reason: "stockShort" });
    expect(suggestHorizon("stock", "very_aggressive")?.horizon).toBe("3m");
  });
  it("unknown asset type, unknown preset or cash gives no suggestion", () => {
    expect(suggestHorizon("cash", "balanced")).toBeNull();
    expect(suggestHorizon("weird", "balanced")).toBeNull();
    expect(suggestHorizon("stock", "bogus")).toBeNull();
    expect(suggestHorizon("stock", null)).toBeNull();
    expect(suggestHorizon(undefined, "balanced")).toBeNull();
  });
});
