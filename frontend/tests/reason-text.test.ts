import { describe, expect, it } from "vitest";
import { createTranslator } from "next-intl";
import en from "@/messages/en.json";
import he from "@/messages/he.json";
import { gateProgress, matchReason } from "@/lib/reasonText";

/** One real backend sentence per mapped template: [text, key]. */
const SAMPLES: [string, string][] = [
  ["Price is 13.1% above its 20-day average.", "priceVsSma"],
  ["Price is 2.0 ATRs below its 50-day average.", "priceVsSmaAtr"],
  ["The 50-day average is above the 200-day average (long-term uptrend).", "sma50v200"],
  ["The 50-day average is below the 200-day average (long-term downtrend).", "sma50v200"],
  ["The 20-day EMA moved +1.5% over the last 10 sessions.", "emaSlope"],
  ["MACD histogram is -0.30 ATRs (below its signal line).", "macdAtr"],
  ["Momentum skipping the last month is +12%, 1.1 volatility units up.", "momentum12"],
  ["RSI is 75: overbought.", "rsiOver"],
  ["RSI is 25: oversold.", "rsiUnder"],
  ["RSI is 69: positive momentum.", "rsiPositive"],
  ["RSI is 45: weak momentum.", "rsiWeak"],
  ["MACD just crossed above its signal line (bullish).", "macdCrossUp"],
  ["MACD just crossed below its signal line (bearish).", "macdCrossDown"],
  ["MACD is above its signal line.", "macdSide"],
  ["Stochastic %K is 95: overbought zone.", "stochOver"],
  ["Stochastic %K is 10: oversold zone.", "stochUnder"],
  ["Stochastic %K (60) is below %D.", "stochKD"],
  ["Price is at or above the upper Bollinger band (stretched).", "bbUpper"],
  ["Price is at or below the lower Bollinger band (stretched down).", "bbLower"],
  ["Price sits at 55% of the Bollinger band range.", "bbPosition"],
  ["Daily range (ATR) is 4.1% of price: high volatility.", "atrHigh"],
  ["Daily range (ATR) is 1.9% of price: contained volatility.", "atrLow"],
  ["On-balance volume confirms the rise over 20 sessions.", "obvRise"],
  ["On-balance volume confirms the decline over 20 sessions.", "obvFall"],
  ["On-balance volume diverges from price over 20 sessions.", "obvDiverge"],
  ["Volume is 2.1x its 20-day average on an up day.", "volSpike"],
  ["Volume is 2.1x its 20-day average on a down day.", "volSpike"],
  ["Volume is 1.0x its 20-day average: no spike.", "volNormal"],
  ["Earnings date unknown (no free calendar for this market).", "earnUnknown"],
  ["Last earnings report was 4 day(s) ago.", "earnAgo"],
  ["Earnings today.", "earnToday"],
  ["Earnings in 1 day.", "earnIn"],
  ["Earnings in 5 days.", "earnIn"],
  ["Confidence is lowered: earnings fall inside the window and can gap the price.", "earnLowered"],
  ["No price history available for technical analysis.", "noHistoryTech"],
  ["No price history available for pattern detection.", "noHistoryPatterns"],
  ["Only 12 daily bars; at least 60 are needed for technical analysis.", "fewBarsTech"],
  ["Only 12 daily bars; at least 60 are needed for patterns.", "fewBarsPatterns"],
  ["Not enough data to compute any technical indicator.", "noIndicators"],
  ["Indicators produced no valid values.", "noValidValues"],
  ["Price is 1.2% above support at 101.50 (touched 3x).", "nearSupport"],
  ["Price is 2.0% below resistance at 110.00 (touched 2x).", "nearResistance"],
  ["No key level is close to the price.", "noNearLevel"],
  ["No key level is close to the price (support 100.00, resistance 110.00).", "noNearLevelParts"],
  ["Breakout: close 120.00 is above the 20-day high 118.00 on heavy volume.", "breakout"],
  ["Breakout: close 120.00 is above the 20-day high 118.00.", "breakout"],
  ["Breakdown: close 90.00 is below the 20-day low 95.00.", "breakdown"],
  ["Golden cross: the 50-day average crossed above the 200-day within the last 10 sessions.", "goldenCross"],
  ["Death cross: the 50-day average crossed below the 200-day within the last 10 sessions.", "deathCross"],
  ["The 50-day average is above the 200-day (no recent cross).", "sma50v200NoCross"],
  ["Double top near 120.00 confirmed: price broke below the neckline 110.00.", "doubleTopConfirmed"],
  ["Possible double top near 120.00; neckline 110.00 not yet broken.", "doubleTopForming"],
  ["Double bottom near 90.00 confirmed: price broke above the neckline 100.00.", "doubleBottomConfirmed"],
  ["Possible double bottom near 90.00; neckline 100.00 not yet broken.", "doubleBottomForming"],
  ["A close below the 50-day average (98.20) would reverse the trend reading.", "invalidSma"],
  ["A close below support at 95.00 would turn this level into resistance.", "invalidSupport"],
  ["A close above resistance at 110.00 would invalidate it.", "invalidResistance"],
  ["Technical score +24 (trend +38, momentum -12).", "techSummary"],
  ["Pattern score +10 from 3 rule(s); 1 pattern(s) detected.", "patternSummary"],
  ["Total score +24 from 2 signal(s) with data; weights of signals without data (fundamentals, analysts) were redistributed.", "totalSummary"],
  ["No signal has data for this security yet.", "noSignalData"],
  ["Support (touched 1x)", "annSupport"],
  ["Resistance (touched 2x)", "annResistance"],
  ["SMA200", "annSma"],
  ["breakout above N-day high", "annBreakout"],
  ["breakdown below N-day low", "annBreakdown"],
  ["golden cross", "annGolden"],
  ["death cross", "annDeath"],
  ["double top confirmed", "annDoubleTopC"],
  ["double top forming", "annDoubleTopF"],
  ["double bottom confirmed", "annDoubleBottomC"],
  ["double bottom forming", "annDoubleBottomF"],
  ["No backtest has been run for the active weights configuration.", "gateNoBacktest"],
  ["The latest backtest was run with different weights than the active ones.", "gateOldBacktest"],
  ["The backtest for the active weights configuration did not pass.", "gateBacktestFailed"],
  ["Paper trading has not started: it needs 4 weeks without critical errors and 50 calls resolved at 1 month.", "gatePaperNotStarted"],
  ["Paper trading has run 0.0 of 4 required weeks (0 call(s) recorded).", "gateWeeks"],
  ["Paper trading had 2 critical error(s); none are allowed.", "gateErrors"],
  ["Only 0 of 50 required calls are resolved at 1 month (0 recorded).", "gateCalls"],
  ["Paper trading has no result against ^GSPC yet.", "gateNoBench"],
  ["Paper trading does not beat ^GSPC (-1.5%).", "gateNotBeat"],
];

describe("reasonText", () => {
  it.each(SAMPLES)("maps %s", (text, key) => {
    const m = matchReason(text);
    expect(m?.[0].key).toBe(key);
  });

  it("every message key is used by a sample or is a UI string", () => {
    const used = new Set(SAMPLES.map((s) => s[1]));
    const tableKeys = Object.keys(en.reason).filter((k) => /^(priceVs|sma|ema|macd|momentum|rsi|stoch|bb|atr|obv|vol|earn|noH|few|noI|noV|near|noN|break|golden|death|double|invalid|tech|pattern|total|noS|ann|gate[A-Z])/.test(k) && !/Line$|Short$|Details$|Hide$|BacktestMissing$/.test(k));
    for (const k of tableKeys) expect(used.has(k), k).toBe(true);
  });

  it("renders every sample in he and en with no raw English words left in he and no unfilled params", () => {
    for (const [loc, msgs] of [["en", en], ["he", he]] as const) {
      const tr = createTranslator({ locale: loc, messages: msgs, namespace: "reason" });
      for (const [text] of SAMPLES) {
        for (const m of matchReason(text)!) {
          const v = { ...m.values };
          if (m.key === "techSummary") v.cats = "x";
          if (m.key === "noNearLevelParts") v.parts = "x";
          if (m.key === "totalSummary") v.names = "x";
          const out = tr(m.key as "priceVsSma", v);
          expect(out, `${loc} ${m.key}`).not.toMatch(/[{}]/);
          if (loc === "he") expect(out, m.key).not.toMatch(/[a-z]{4,}/);
        }
      }
    }
  });

  it("splits a two-sentence reason and translates both", () => {
    const m = matchReason("Price is 1.2% above support at 101.50 (touched 3x). Price is 2.0% below resistance at 110.00 (touched 2x).");
    expect(m?.map((x) => x.key)).toEqual(["nearSupport", "nearResistance"]);
  });

  it("returns null for unknown text so the caller shows the original", () => {
    expect(matchReason("Something new the backend says.")).toBeNull();
    expect(matchReason("Price is 1.2% above support at 101.50 (touched 3x). Something new.")).toBeNull();
  });

  it("reads the gate progress", () => {
    expect(gateProgress([
      "No backtest has been run for the active weights configuration.",
      "Paper trading has run 1.5 of 4 required weeks (3 call(s) recorded).",
      "Only 3 of 50 required calls are resolved at 1 month (3 recorded).",
    ])).toEqual({ backtest: "missing", weeks: { done: 1.5, need: 4 }, calls: { done: 3, need: 50 } });
    expect(gateProgress(["Paper trading has not started: it needs 4 weeks without critical errors and 50 calls resolved at 1 month."]))
      .toEqual({ backtest: null, weeks: { done: 0, need: 4 }, calls: { done: 0, need: 50 } });
  });
});
