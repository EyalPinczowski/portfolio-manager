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

import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { isKnownTextCode, SERVER_TEXT_CODES } from "@/lib/server-text";
import { ServerText } from "@/components/ServerText";

const PARAMS: Record<string, Record<string, string | number>> = {
  stop_atr: { mult: 2.5, period: 14, timeframe: "daily", pct: 5 },
  stop_beyond_atr: { source: "structure", level: 90, mult: 2 },
  stop_chart_no_atr: { source: "moving_average", level: 90 },
  stop_max_loss_only: { max_loss_pct: 8 },
  stop_saved: { level: 91.5 },
  trailing_moved: { level: 95, highest: 110, mult: 3, profile: "balanced" },
  trailing_start: { level: 95 },
  breakeven: { gain_atr: 1.5, cost: 100, profile: "balanced", from_atr: 1 },
  take_profit: { index: 1, level: 120, source: "resistance", rr: 1.8 },
  size_fits: {},
  size_reduce: { stop: 90, keep_pct: 60, suggested: 60, current: 100 },
  size_rule_max_loss: { limit_pct: 8, distance_pct: 13.3, fit_pct: 60 },
  size_rule_portfolio_risk: { limit_pct: 1, loss_ils: 5000, allowed_ils: 3000, fit_pct: 60 },
  exposure_now: { dimension: "sector", name: "Technology", before_pct: 20, limit_pct: 30 },
  exposure_breaks: { dimension: "position", name: "AAPL", before_pct: 10, after_pct: 40, limit_pct: 20 },
  exposure_fits: { dimension: "country", name: "Israel", before_pct: 10, after_pct: 15, limit_pct: 60 },
  max_size_binding: { max_additional_ils: 4000, rule: "max_position_pct" },
  max_size_no_limit: {},
  max_size_empty_book: {},
};

describe("server text codes", () => {
  it("has he and en messages and a test case for every code", () => {
    for (const c of SERVER_TEXT_CODES) {
      expect((en as { serverText: Record<string, string> }).serverText[c], c).toBeTruthy();
      expect((he as { serverText: Record<string, string> }).serverText[c], c).toBeTruthy();
      expect(PARAMS[c], c).toBeDefined();
    }
    expect(isKnownTextCode({ code: "nope" })).toBe(false);
  });
  for (const [locale, messages] of [["en", en], ["he", he]] as const) {
    it(`renders every code in ${locale} with no raw placeholders`, () => {
      for (const c of SERVER_TEXT_CODES) {
        const { container, unmount } = render(
          <NextIntlClientProvider locale={locale} messages={messages}>
            <ServerText code={{ code: c, params: PARAMS[c] }} text="ENGLISH FALLBACK" />
          </NextIntlClientProvider>,
        );
        const txt = container.textContent ?? "";
        expect(txt, c).not.toContain("ENGLISH FALLBACK");
        expect(txt, c).not.toMatch(/[{}]/);
        if (locale === "he") expect(txt, c).toMatch(/[֐-׿]/);
        unmount();
      }
    });
  }
  it("falls back to the English text for an unknown or missing code", () => {
    render(
      <NextIntlClientProvider locale="he" messages={he}>
        <ServerText code={{ code: "unknown_code" }} text="Some English" />
        <ServerText text="No code here" />
      </NextIntlClientProvider>,
    );
    expect(screen.getByText("Some English").tagName).toBe("BDI");
    expect(screen.getByText("No code here").tagName).toBe("BDI");
  });
});
