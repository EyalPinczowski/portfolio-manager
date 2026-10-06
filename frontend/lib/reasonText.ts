/* Maps the backend's fixed English reason sentences (signals, chart annotations, launch gate) to message keys
 * under the "reason" namespace, with the numbers pulled out as params. Unknown text returns null, and the caller
 * shows the original. Display only: no score or rule depends on this. */

export type ReasonMatch = { key: string; values: Record<string, string> };

const N = String.raw`[-+]?\d+(?:\.\d+)?`;
const AB = String.raw`above|below`;

/** [pattern with named groups, message key]. Anchored; order does not matter (patterns are disjoint). */
const TABLE: ReadonlyArray<readonly [RegExp, string]> = [
  // technical: trend
  [new RegExp(`^Price is (?<pct>${N})% (?<dir>${AB}) its (?<n>\\d+)-day average\\.$`), "priceVsSma"],
  [new RegExp(`^Price is (?<z>${N}) ATRs (?<dir>${AB}) its (?<n>\\d+)-day average\\.$`), "priceVsSmaAtr"],
  [new RegExp(`^The 50-day average is (?<dir>${AB}) the 200-day average \\((?:long-term uptrend|long-term downtrend)\\)\\.$`), "sma50v200"],
  [new RegExp(`^The 20-day EMA moved (?<pct>${N})% over the last 10 sessions\\.$`), "emaSlope"],
  // momentum
  [new RegExp(`^MACD histogram is (?<z>${N}) ATRs \\((?<dir>${AB}) its signal line\\)\\.$`), "macdAtr"],
  [new RegExp(`^Momentum skipping the last month is (?<ret>${N})%, (?<z>\\d+(?:\\.\\d+)?) volatility units (?<dir>up|down)\\.$`), "momentum12"],
  [new RegExp(`^RSI is (?<n>\\d+): overbought\\.$`), "rsiOver"],
  [new RegExp(`^RSI is (?<n>\\d+): oversold\\.$`), "rsiUnder"],
  [new RegExp(`^RSI is (?<n>\\d+): positive momentum\\.$`), "rsiPositive"],
  [new RegExp(`^RSI is (?<n>\\d+): weak momentum\\.$`), "rsiWeak"],
  [/^MACD just crossed above its signal line \(bullish\)\.$/, "macdCrossUp"],
  [/^MACD just crossed below its signal line \(bearish\)\.$/, "macdCrossDown"],
  [new RegExp(`^MACD is (?<dir>${AB}) its signal line\\.$`), "macdSide"],
  [new RegExp(`^Stochastic %K is (?<n>\\d+): overbought zone\\.$`), "stochOver"],
  [new RegExp(`^Stochastic %K is (?<n>\\d+): oversold zone\\.$`), "stochUnder"],
  [new RegExp(`^Stochastic %K \\((?<n>\\d+)\\) is (?<dir>${AB}) %D\\.$`), "stochKD"],
  // volatility
  [/^Price is at or above the upper Bollinger band \(stretched\)\.$/, "bbUpper"],
  [/^Price is at or below the lower Bollinger band \(stretched down\)\.$/, "bbLower"],
  [/^Price sits at (?<n>\d+)% of the Bollinger band range\.$/, "bbPosition"],
  [new RegExp(`^Daily range \\(ATR\\) is (?<n>${N})% of price: high volatility\\.$`), "atrHigh"],
  [new RegExp(`^Daily range \\(ATR\\) is (?<n>${N})% of price: contained volatility\\.$`), "atrLow"],
  // volume
  [/^On-balance volume confirms the rise over 20 sessions\.$/, "obvRise"],
  [/^On-balance volume confirms the decline over 20 sessions\.$/, "obvFall"],
  [/^On-balance volume diverges from price over 20 sessions\.$/, "obvDiverge"],
  [/^Volume is (?<n>\d+(?:\.\d+)?)x its 20-day average on (?<dir>an up|a down) day\.$/, "volSpike"],
  [/^Volume is (?<n>\d+(?:\.\d+)?)x its 20-day average: no spike\.$/, "volNormal"],
  // earnings
  [/^Earnings date unknown \(no free calendar for this market\)\.$/, "earnUnknown"],
  [/^Last earnings report was (?<n>\d+) day\(s\) ago\.$/, "earnAgo"],
  [/^Earnings today\.$/, "earnToday"],
  [/^Earnings in (?<n>\d+) days?\.$/, "earnIn"],
  [/^Confidence is lowered: earnings fall inside the window and can gap the price\.$/, "earnLowered"],
  // missing data
  [/^No price history available for technical analysis\.$/, "noHistoryTech"],
  [/^No price history available for pattern detection\.$/, "noHistoryPatterns"],
  [/^Only (?<n>\d+) daily bars; at least (?<m>\d+) are needed for technical analysis\.$/, "fewBarsTech"],
  [/^Only (?<n>\d+) daily bars; at least (?<m>\d+) are needed for patterns\.$/, "fewBarsPatterns"],
  [/^Not enough data to compute any technical indicator\.$/, "noIndicators"],
  [/^Indicators produced no valid values\.$/, "noValidValues"],
  // patterns
  [new RegExp(`^Price is (?<pct>${N})% above support at (?<p>${N}) \\(touched (?<k>\\d+)x\\)\\.$`), "nearSupport"],
  [new RegExp(`^Price is (?<pct>${N})% below resistance at (?<p>${N}) \\(touched (?<k>\\d+)x\\)\\.$`), "nearResistance"],
  [/^No key level is close to the price\.$/, "noNearLevel"],
  [new RegExp(`^No key level is close to the price \\((?<parts>[^)]*)\\)\\.$`), "noNearLevelParts"],
  [new RegExp(`^Breakout: close (?<p>${N}) is above the (?<n>\\d+)-day high (?<h>${N})(?<vol> on heavy volume)?\\.$`), "breakout"],
  [new RegExp(`^Breakdown: close (?<p>${N}) is below the (?<n>\\d+)-day low (?<h>${N})(?<vol> on heavy volume)?\\.$`), "breakdown"],
  [/^Golden cross: the 50-day average crossed above the 200-day within the last (?<n>\d+) sessions\.$/, "goldenCross"],
  [/^Death cross: the 50-day average crossed below the 200-day within the last (?<n>\d+) sessions\.$/, "deathCross"],
  [new RegExp(`^The 50-day average is (?<dir>${AB}) the 200-day \\(no recent cross\\)\\.$`), "sma50v200NoCross"],
  [new RegExp(`^Double top near (?<p>${N}) confirmed: price broke below the neckline (?<neck>${N})\\.$`), "doubleTopConfirmed"],
  [new RegExp(`^Possible double top near (?<p>${N}); neckline (?<neck>${N}) not yet broken\\.$`), "doubleTopForming"],
  [new RegExp(`^Double bottom near (?<p>${N}) confirmed: price broke above the neckline (?<neck>${N})\\.$`), "doubleBottomConfirmed"],
  [new RegExp(`^Possible double bottom near (?<p>${N}); neckline (?<neck>${N}) not yet broken\\.$`), "doubleBottomForming"],
  // invalidation
  [new RegExp(`^A close (?<dir>${AB}) the (?<n>\\d+)-day average \\((?<p>${N})\\) would reverse the trend reading\\.$`), "invalidSma"],
  [new RegExp(`^A close below support at (?<p>${N}) would turn this level into resistance\\.$`), "invalidSupport"],
  [new RegExp(`^A close above resistance at (?<p>${N}) would invalidate it\\.$`), "invalidResistance"],
  // summaries
  [new RegExp(`^Technical score (?<score>${N}) \\((?<cats>[^)]*)\\)\\.$`), "techSummary"],
  [/^Pattern score (?<score>[-+]?\d+) from (?<n>\d+) rule\(s\); (?<k>\d+) pattern\(s\) detected\.$/, "patternSummary"],
  [/^Total score (?<score>[-+]?\d+) from (?<n>\d+) signal\(s\) with data; weights of signals without data \((?<names>[^)]*)\) were redistributed\.$/, "totalSummary"],
  [/^No signal has data for this security yet\.$/, "noSignalData"],
  // annotation labels
  [/^Support \(touched (?<k>\d+)x\)$/, "annSupport"],
  [/^Resistance \(touched (?<k>\d+)x\)$/, "annResistance"],
  [/^SMA(?<n>\d+)$/, "annSma"],
  [/^breakout above N-day high$/, "annBreakout"],
  [/^breakdown below N-day low$/, "annBreakdown"],
  [/^golden cross$/, "annGolden"],
  [/^death cross$/, "annDeath"],
  [/^double top confirmed$/, "annDoubleTopC"],
  [/^double top forming$/, "annDoubleTopF"],
  [/^double bottom confirmed$/, "annDoubleBottomC"],
  [/^double bottom forming$/, "annDoubleBottomF"],
  // launch gate
  [/^No backtest has been run for the active weights configuration\.$/, "gateNoBacktest"],
  [/^The latest backtest was run with different weights than the active ones\.$/, "gateOldBacktest"],
  [/^The backtest for the active weights configuration did not pass\.$/, "gateBacktestFailed"],
  [/^Paper trading has not started: it needs (?<weeks>\d+) weeks without critical errors and (?<calls>\d+) calls resolved at 1 month\.$/, "gatePaperNotStarted"],
  [/^Paper trading has run (?<done>\d+(?:\.\d+)?) of (?<need>\d+) required weeks \((?<rec>\d+) call\(s\) recorded\)\.$/, "gateWeeks"],
  [/^Paper trading had (?<n>\d+) critical error\(s\); none are allowed\.$/, "gateErrors"],
  [/^Only (?<done>\d+) of (?<need>\d+) required calls are resolved at 1 month \((?<rec>\d+) recorded\)\.$/, "gateCalls"],
  [/^Paper trading has no result against (?<bench>\S+) yet\.$/, "gateNoBench"],
  [new RegExp(`^Paper trading does not beat (?<bench>\\S+) \\((?<edge>${N})%\\)\\.$`), "gateNotBeat"],
];

function matchOne(text: string): ReasonMatch | null {
  const s = text.trim();
  for (const [re, key] of TABLE) {
    const m = re.exec(s);
    if (m) {
      const values: Record<string, string> = {};
      for (const [k, v] of Object.entries(m.groups ?? {})) values[k] = v === undefined ? "" : v;
      if (values.dir === "an up") values.dir = "up";
      if (values.dir === "a down") values.dir = "down";
      if ("vol" in values) values.vol = values.vol ? "yes" : "no";
      return { key, values };
    }
  }
  return null;
}

/** A reason may join several sentences ("... Price is 2% below resistance ..."). All must be known, else null. */
export function matchReason(text: string): ReasonMatch[] | null {
  const direct = matchOne(text);
  if (direct) return [direct];
  const parts = text.trim().split(/(?<=\.) (?=[A-Z])/);
  if (parts.length < 2) return null;
  const out: ReasonMatch[] = [];
  for (const p of parts) {
    const m = matchOne(p);
    if (!m) return null;
    out.push(m);
  }
  return out;
}

/** Labels of the indicator numbers (explanation inputs and the chart snapshot). */
export const INPUT_KEYS = new Set([
  "close", "bars", "sma20", "sma50", "sma200", "ema20_slope_10d_pct", "rsi14", "macd_hist", "stoch_k", "bollinger_pct_b",
  "atr14_pct", "atr14", "atr14_pct_of_price", "bollinger_lower", "bollinger_upper", "high_52w", "low_52w", "support",
  "resistance", "sma50_minus_sma200", "obv_rising_20d", "volume_ratio", "mom_12_1_z", "z_sma50_atr", "z_sma200_atr", "macd_hist_atr",
]);

export interface GateProgress {
  backtest: "missing" | "failed" | null;
  weeks: { done: number; need: number } | null;
  calls: { done: number; need: number } | null;
}

/** Numbers for the compact gate line, read from the backend's reasons. Null parts mean "not mentioned". */
export function gateProgress(reasons: string[]): GateProgress {
  const out: GateProgress = { backtest: null, weeks: null, calls: null };
  for (const r of reasons) {
    const m = matchOne(r);
    if (!m) continue;
    const v = m.values;
    if (m.key === "gateNoBacktest" || m.key === "gateOldBacktest") out.backtest = "missing";
    else if (m.key === "gateBacktestFailed") out.backtest = "failed";
    else if (m.key === "gatePaperNotStarted") {
      out.weeks = { done: 0, need: Number(v.weeks) };
      out.calls = { done: 0, need: Number(v.calls) };
    } else if (m.key === "gateWeeks") out.weeks = { done: Number(v.done), need: Number(v.need) };
    else if (m.key === "gateCalls") out.calls = { done: Number(v.done), need: Number(v.need) };
  }
  return out;
}
