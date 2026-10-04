/* Mock exit-levels / exit-review responses (NEXT_PUBLIC_API_MOCK=1). Shapes follow backend/openapi.json. */
import type { ExitLevel, ExitLevelsResult, ExitReviewOut, Horizon, ReviewRow, StopCandidate } from "./api";

export interface ExitSeed {
  id: number; symbol: string; en?: string; he?: string; cur: string; qty: number; price: number; cost: number | null;
  horizon: Horizon | null; stale?: boolean; noData?: boolean;
}

const FX = 3.7;
const AS_OF = "2026-10-03T08:55:00Z";
const ATR_MULT: Record<Horizon, number> = { "1w": 1.5, "1m": 2, "3m": 2.5, "6m": 3, "1y": 3.5 };
const LABEL: Record<Horizon, string> = { "1w": "1 week", "1m": "1 month", "3m": "3 months", "6m": "6 months", "1y": "1 year+" };
const r2 = (n: number) => Math.round(n * 100) / 100;
const toIls = (n: number, cur: string) => (cur === "USD" ? n * FX : n);

export interface ExitQuery { horizon?: Horizon | null; risk?: string | null }

function level(s: ExitSeed, kind: ExitLevel["kind"], label: string, price: number, rr: number | null, source: string, reason: string, stopPrice: number): ExitLevel {
  const move = (price - s.price) * s.qty;
  const pnl = s.cost === null ? null : (price - s.cost) * s.qty;
  return {
    kind, label, price: r2(price), distance_pct: r2(((price - s.price) / s.price) * 100),
    vs_price_ils: r2(toIls(move, s.cur)), vs_price_usd: r2(s.cur === "USD" ? move : move / FX),
    pnl_native: pnl === null ? null : r2(pnl),
    pnl_ils: pnl === null ? null : r2(toIls(pnl, s.cur)),
    pnl_usd: pnl === null ? null : r2(s.cur === "USD" ? pnl : pnl / FX),
    rr, reached: kind === "take_profit" ? s.price >= price : s.price <= price, source, reason,
    explanation: {
      version: 1, as_of: AS_OF, summary: reason,
      inputs: { price: s.price, "stop price": r2(stopPrice), source },
      rules_applied: ["Stop distance = ATR multiple for the chosen holding period", "A trailing stop only moves up"],
      risk_rules_applied: ["The stop was not tightened to fit the risk filter; a smaller size is suggested instead"],
      invalidation_risks: ["A gap through the level can fill worse than the level itself."],
      annotations: [{ kind: "support", label: "Recent swing low", price: r2(s.price * 0.93), as_of: AS_OF }],
      sources: [{ name: "Price history (Yahoo Finance)", as_of: AS_OF, detail: "Daily bars" }],
    },
  };
}

export function mockExitLevels(s: ExitSeed, q: ExitQuery = {}, portfolioValueIls = 100000): ExitLevelsResult {
  const horizon = q.horizon ?? s.horizon;
  const base = { symbol: s.symbol, currency: s.cur, disclaimer: "Not financial advice." };
  const explain = (summary: string) => ({ version: 1, summary, as_of: AS_OF });
  if (horizon === null || horizon === undefined) {
    return {
      ...base, status: "needs_horizon", reason_code: "needs_horizon", horizon: null, take_profits: [], scale_out: [], candidates: [], skipped: [],
      reason: "This holding has no holding period yet. Choose one to see levels.", explanation: explain("A holding period is needed before levels can be computed."),
    };
  }
  if (s.stale || s.noData) {
    return {
      ...base, status: "no_levels", reason_code: "stale_price", horizon, horizon_label: LABEL[horizon], price: s.price, price_as_of: "2026-09-30T14:00:00Z",
      take_profits: [], scale_out: [], candidates: [], skipped: [{ source: "atr", reason: "Price is the last close, not a live quote." }],
      reason: "The price is stale (last close, not live), so no levels are computed.", explanation: explain("Levels are never computed from a stale or cost price."),
    };
  }
  const risk = q.risk ?? "balanced_aggressive";
  const atr = r2(s.price * 0.025);
  const stopP = s.price - ATR_MULT[horizon] * atr;
  const risk1 = s.price - stopP;
  const stop = level(s, "stop", "Stop", stopP, null, "atr", `${ATR_MULT[horizon]} x ATR(14) below the price for a ${LABEL[horizon]} holding period.`, stopP);
  const trail = level(s, "trailing_stop", "Trailing stop", stopP * 1.01, null, "trailing", "Follows the highest high; it only moves up.", stopP);
  const tp1 = level(s, "take_profit", "Take-profit 1", s.price + 1.5 * risk1, 1.5, "resistance", "Nearest resistance from the chart.", stopP);
  const tp2 = level(s, "take_profit", "Take-profit 2", s.price + 2.5 * risk1, 2.5, "analyst", "Analyst mean target.", stopP);
  const riskNative = risk1 * s.qty;
  const riskIls = toIls(riskNative, s.cur);
  const pctPortfolio = (riskIls / portfolioValueIls) * 100;
  const needed = pctPortfolio > 0.75;
  const keep = needed ? 0.75 / pctPortfolio : 1;
  const cands: StopCandidate[] = [
    { source: "atr", price: r2(stopP), distance_pct: stop.distance_pct, chosen: true, note: "ATR-based distance." },
    { source: "structure", price: r2(s.price * 0.93), distance_pct: -7, chosen: false, note: "Below the recent swing low." },
  ];
  return {
    ...base, status: "levels", reason_code: null, reason: "Levels computed.", horizon, horizon_label: LABEL[horizon], risk_preset: risk,
    price: s.price, price_as_of: AS_OF, atr, atr_timeframe: "daily",
    stop, trailing_stop: trail, breakeven: s.cost !== null && s.cost > s.price ? level(s, "breakeven", "Break-even", s.cost, null, "cost", "Your average cost.", stopP) : null,
    take_profits: [tp1, tp2],
    scale_out: [
      { step: "take_profit", label: "Sell the first part at take-profit 1", price: tp1.price, fraction: 0.5, quantity: r2(s.qty * 0.5) },
      { step: "trail_rest", label: "Trail the rest", price: null, fraction: 0.5, quantity: r2(s.qty * 0.5) },
    ],
    size_guidance: {
      needed, keep_fraction: r2(keep), current_quantity: s.qty, suggested_quantity: r2(s.qty * keep),
      rules: needed ? [`Max risk per trade is 0.75% of the portfolio; this position risks ${r2(pctPortfolio)}%.`] : [],
      reason: needed ? "A smaller position keeps the risk within your limit. The stop stays where the chart puts it." : "The position size fits your risk limit.",
    },
    risk_to_stop: { native: r2(riskNative), ils: r2(riskIls), usd: r2(riskIls / FX), pct_of_position: r2((risk1 / s.price) * 100), pct_of_portfolio: r2(pctPortfolio) },
    stop_fit: horizon === "1w" && s.id % 2 === 0 ? "too_tight" : s.id % 5 === 0 ? "too_wide" : "ok",
    candidates: cands, skipped: [{ source: "moving_average", reason: "Not enough history for the 200-day average." }],
    state: { highest_high: r2(s.price * 1.02), stop: r2(stopP) },
    explanation: {
      version: 1, as_of: AS_OF,
      summary: `Stop ${r2(stopP)} (${stop.distance_pct}%), two take-profits and a scale-out plan for a ${LABEL[horizon]} holding period.`,
      inputs: { horizon: LABEL[horizon], ATR: atr, "risk preset": risk },
      rules_applied: ["ATR multiple by holding period", "Take-profits from resistance and analyst targets"],
      risk_rules_applied: ["Max loss per position and max portfolio risk checked"], invalidation_risks: ["A volatility spike widens the ATR and the stop."],
      sources: [{ name: "Price history (Yahoo Finance)", as_of: AS_OF, detail: "Daily bars" }],
    },
  };
}

export function mockExitReview(pid: number, seeds: ExitSeed[], q: ExitQuery = {}): ExitReviewOut {
  const value = seeds.reduce((a, s) => a + toIls(s.qty * s.price, s.cur), 0);
  const rows: ReviewRow[] = seeds.map((s) => {
    const lv = mockExitLevels(s, q, value);
    return {
      holding_id: s.id, symbol: s.symbol, name_en: s.en ?? s.symbol, name_he: s.he ?? s.symbol, quantity: s.qty, currency: s.cur,
      avg_cost: s.cost, horizon: lv.horizon ?? null, horizon_source: q.horizon ? "override" : s.horizon ? "holding" : null,
      status: lv.status, reason_code: lv.reason_code ?? null, price: lv.price ?? null,
      stop_price: lv.stop?.price ?? null, stop_distance_pct: lv.stop?.distance_pct ?? null,
      take_profit_prices: (lv.take_profits ?? []).map((t) => t.price),
      risk_ils: lv.risk_to_stop?.ils ?? null, risk_usd: lv.risk_to_stop?.usd ?? null, risk_pct_of_portfolio: lv.risk_to_stop?.pct_of_portfolio ?? null,
      smaller_size_needed: !!lv.size_guidance?.needed, keep_fraction: lv.size_guidance?.keep_fraction ?? null, stop_fit: lv.stop_fit ?? null, levels: lv,
    };
  });
  const withStop = rows.filter((r) => r.stop_price != null);
  const totalIls = withStop.reduce((a, r) => a + (r.risk_ils ?? 0), 0);
  const limit = 12;
  return {
    portfolio_id: pid, portfolio_value_ils: r2(value), rows, disclaimer: "Not financial advice.",
    totals: {
      total_risk_ils: r2(totalIls), total_risk_usd: r2(totalIls / FX), total_risk_pct_of_portfolio: r2(value ? (totalIls / value) * 100 : 0),
      limit_pct: limit, over_limit: value ? (totalIls / value) * 100 > limit : false, positions_with_stop: withStop.length,
      top_contributors: [...withStop].sort((a, b) => (b.risk_ils ?? 0) - (a.risk_ils ?? 0)).slice(0, 3).map((r) => ({
        symbol: r.symbol, risk_ils: r.risk_ils ?? 0, risk_pct_of_portfolio: r.risk_pct_of_portfolio ?? 0,
        share_of_total_risk_pct: r2(totalIls ? ((r.risk_ils ?? 0) / totalIls) * 100 : 0),
      })),
      positions_without_stop: rows.filter((r) => r.stop_price == null).map((r) => ({ symbol: r.symbol, reason_code: r.reason_code ?? null, reason: r.levels.reason })),
      stops_too_tight: rows.filter((r) => r.stop_fit === "too_tight").map((r) => r.symbol),
      stops_too_wide: rows.filter((r) => r.stop_fit === "too_wide").map((r) => r.symbol),
      smaller_size_needed: rows.filter((r) => r.smaller_size_needed).map((r) => r.symbol),
    },
  };
}
