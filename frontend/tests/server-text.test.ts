import { describe, expect, it } from "vitest";
import en from "../messages/en.json";
import he from "../messages/he.json";
import { horizonFromLabel, isFitIncompleteSummary, isPlanNote, matchStopReason, sourceKey } from "@/lib/server-text";

describe("server-text mapping", () => {
  it("maps backend horizon labels", () => {
    expect(horizonFromLabel("3 months")).toBe("3m");
    expect(horizonFromLabel("1 year+")).toBe("1y");
    expect(horizonFromLabel("fortnight")).toBeNull();
  });
  it("maps source codes, incl. sma_N, and every code has en + he text", () => {
    expect(sourceKey("sma_200")).toEqual({ key: "sma", n: "200" });
    expect(sourceKey("unknown_thing")).toBeNull();
    for (const k of Object.keys(en.exit.sources)) expect(Object.keys(he.exit.sources)).toContain(k);
    for (const c of ["atr", "structure", "moving_average", "max_loss", "trailing", "cost", "saved_stop", "resistance", "analyst"]) {
      expect(sourceKey(c)).not.toBeNull();
      expect((en.exit.sources as Record<string, string>)[c]).toBeTruthy();
      expect((he.exit.sources as Record<string, string>)[c]).toBeTruthy();
    }
  });
  it("recognises the fixed plan note and fit-incomplete sentences", () => {
    expect(isPlanNote("This is a suggestion to review and change, not an instruction.")).toBe(true);
    expect(isPlanNote("A plan to review and change, not an instruction; the numbers come from your risk profile.")).toBe(true);
    expect(isPlanNote("Something else")).toBe(false);
    expect(isFitIncompleteSummary("Fit cannot be checked until the missing inputs are given. Nothing is assumed for you.")).toBe(true);
    expect(isFitIncompleteSummary("Fits.")).toBe(false);
  });
  it("matches known ATR stop reasons only", () => {
    expect(matchStopReason("2.5 x ATR(14) below the price for a 3 months holding period.")).toEqual({ key: "atr", values: { mult: "2.5", period: "14" } });
    expect(matchStopReason("Stop at 2.00 x ATR(14, 1d) below the price (5.0%).")?.key).toBe("atr");
    expect(matchStopReason("Follows the highest high; it only moves up.")?.key).toBe("trailing");
    expect(matchStopReason("just under the swing low")).toBeNull();
  });
});
