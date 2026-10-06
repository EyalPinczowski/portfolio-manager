/* Mock analyze / search-history / watchlist responses (NEXT_PUBLIC_API_MOCK=1). Shapes follow backend/openapi.json. */
import type {
  AnalyzeOut, AskOut, ChartReport, ExposureCheck, Explanation, Horizon, PortfolioFit, RiskFilter, ScoutReport, SecurityHit,
  SearchHistoryItem, SignalLine, WatchlistItem,
} from "./api";
import { ApiError } from "./errors";
import { mockExitLevels } from "./mock-exit";

const FX = 3.7;
const AS_OF = "2026-10-03T08:55:00Z";
const STALE_AS_OF = "2026-09-30T14:00:00Z";
const r2 = (n: number) => Math.round(n * 100) / 100;
const toIls = (n: number, cur: string) => (cur === "USD" ? n * FX : n);
const LIMITED_NOTICE = "Scores are not yet validated by a backtest and paper trading, so this page shows neutral analysis for you to research, not an instruction to trade. Not financial advice.";

interface CatalogItem {
  symbol: string; en: string; he: string; market: "US" | "TASE" | "CRYPTO"; type: string; sector: string; country: string;
  cur: "USD" | "ILS"; price: number; stale?: boolean; thin?: boolean;
}
export const CATALOG: CatalogItem[] = [
  { symbol: "AMD", en: "Advanced Micro Devices", he: "איי אם דיי", market: "US", type: "stock", sector: "Technology", country: "United States", cur: "USD", price: 160 },
  { symbol: "XOM", en: "Exxon Mobil", he: "אקסון מוביל", market: "US", type: "stock", sector: "Energy", country: "United States", cur: "USD", price: 112 },
  { symbol: "NVDA", en: "NVIDIA", he: "אנבידיה", market: "US", type: "stock", sector: "Technology", country: "United States", cur: "USD", price: 142.3 },
  { symbol: "TEVA.TA", en: "Teva", he: "טבע", market: "TASE", type: "stock", sector: "Healthcare", country: "Israel", cur: "ILS", price: 62.4 },
  { symbol: "LUMI.TA", en: "Bank Leumi", he: "בנק לאומי", market: "TASE", type: "stock", sector: "Financials", country: "Israel", cur: "ILS", price: 47.9 },
  { symbol: "ARNA.TA", en: "Arena Fund", he: "קרן ארנה", market: "TASE", type: "fund", sector: "Diversified", country: "Israel", cur: "ILS", price: 31.4, stale: true, thin: true },
];
/** Listings the symbol search finds beyond the known list (remote=1), like Yahoo does for a name that is not seeded. */
const REMOTE_ONLY = [
  { symbol: "RDDT", en: "Reddit, Inc.", market: "US" as const, cur: "USD" as const, exchange: "NYSE" },
  { symbol: "GTLB", en: "GitLab Inc.", market: "US" as const, cur: "USD" as const, exchange: "NASDAQ" },
  { symbol: "ZZQ.TA", en: "Zed Q Industries", market: "TASE" as const, cur: "ILS" as const, exchange: "TASE" },
];
export const mockSearchHits = (q: string, remote = false): SecurityHit[] => {
  const s = q.trim().toLowerCase();
  if (!s) return [];
  const known: SecurityHit[] = CATALOG.filter((c) => c.symbol.toLowerCase().includes(s) || c.en.toLowerCase().includes(s) || c.he.includes(q.trim())).map((c) => ({ symbol: c.symbol, name_en: c.en, name_he: c.he, market: c.market, source: "known" as const, currency: c.cur as "USD" | "ILS" }));
  if (!remote || s.length < 2) return known;
  const added: SecurityHit[] = REMOTE_ONLY.filter((c) => c.symbol.toLowerCase().includes(s) || c.en.toLowerCase().includes(s)).map((c) => ({ symbol: c.symbol, name_en: c.en, name_he: "", market: c.market, source: "new" as const, currency: c.cur, exchange: c.exchange }));
  return [...known, ...added];
};

export interface AnalyzeHeld { symbol: string; sector: string; country: string; valueIls: number; quantity: number; weightOf: number; cost: number | null; horizon: Horizon | null; id: number; cur: string }
export interface AnalyzeCtx { portfolioIds: number[]; holdings: AnalyzeHeld[]; riskFilter: RiskFilter | null }
export interface AnalyzeQ { portfolio_id: number | null; amount: number | null; currency: string | null; horizon: Horizon | null; risk: string | null }

// ---- in-memory lists (symbols only, like the backend) ----
let history: SearchHistoryItem[] = [];
let watch: WatchlistItem[] = [];
export function resetMockLists(): void {
  history = [
    { symbol: "AMD", searched_at: "2026-10-03T07:10:00Z" },
    { symbol: "TEVA.TA", searched_at: "2026-10-02T12:00:00Z" },
  ];
  watch = [
    { symbol: "NVDA", market: "US", name: "NVIDIA", added_at: "2026-09-20T10:00:00Z", quote: { price: 142.3, currency: "USD", as_of: AS_OF, source: "yahoo", basis: "live", is_fresh: true } },
    { symbol: "ARNA.TA", market: "TASE", name: "Arena Fund", added_at: "2026-09-21T10:00:00Z", quote: { price: 31.4, currency: "ILS", as_of: STALE_AS_OF, source: "yahoo", basis: "last_close", is_fresh: false } },
    { symbol: "XOM", market: "US", name: "Exxon Mobil", added_at: "2026-09-22T10:00:00Z", quote: null },
  ];
}
resetMockLists();

const norm = (s: string) => decodeURIComponent(s).trim().toUpperCase();
export const mockSearchHistory = (): SearchHistoryItem[] => history;
export function mockHistoryDelete(symbol?: string): undefined {
  history = symbol === undefined ? [] : history.filter((h) => h.symbol !== norm(symbol));
  return undefined;
}
export const mockWatchlist = (): WatchlistItem[] => watch;
export function mockWatchAdd(symbol: string): WatchlistItem {
  const sym = norm(symbol);
  const c = CATALOG.find((x) => x.symbol === sym);
  if (!c) throw new ApiError(404, "Unknown symbol");
  const existing = watch.find((w) => w.symbol === sym);
  if (existing) return existing;
  const item: WatchlistItem = {
    symbol: sym, market: c.market, name: c.en, added_at: new Date().toISOString(),
    quote: { price: c.price, currency: c.cur, as_of: c.stale ? STALE_AS_OF : AS_OF, source: "yahoo", basis: c.stale ? "last_close" : "live", is_fresh: !c.stale },
  };
  watch = [...watch, item];
  return item;
}
export function mockWatchDelete(symbol: string): undefined { watch = watch.filter((w) => w.symbol !== norm(symbol)); return undefined; }

const explain = (summary: string, extra: Partial<Explanation> = {}): Explanation => ({ version: 1, summary, as_of: AS_OF, ...extra });

/** 120 deterministic daily candles ending on the AS_OF day (weekends skipped). */
function mockCandles(price: number): NonNullable<ChartReport["candles"]> {
  const out: NonNullable<ChartReport["candles"]> = [];
  const d = new Date(AS_OF.slice(0, 10) + "T00:00:00Z");
  const days: string[] = [];
  while (days.length < 120) { if (d.getUTCDay() % 6 !== 0) days.unshift(d.toISOString().slice(0, 10)); d.setUTCDate(d.getUTCDate() - 1); }
  days.forEach((time, i) => {
    const c = price * (0.9 + 0.1 * (i / 119) + 0.03 * Math.sin(i / 7));
    const o = c * (1 + 0.004 * Math.sin(i));
    out.push({ time, open: r2(o), high: r2(Math.max(o, c) * 1.01), low: r2(Math.min(o, c) * 0.99), close: r2(c) });
  });
  return out;
}

function signalLines(thin: boolean): SignalLine[] {
  const line = (name: string, score: number, conf: number, weight: number, reasons: string[]): SignalLine => ({
    name, available: conf > 0, score, confidence: conf, weight: conf > 0 ? weight : 0, nominal_weight: weight, reasons, data_as_of: AS_OF,
  });
  return [
    line("trend", thin ? 0 : 38, thin ? 0 : 0.8, 35, thin ? [] : ["Price is 3.2% above its 50-day average.", "Price is 8.1% above its 200-day average.", "The 50-day average is above the 200-day average (long-term uptrend)."]),
    line("momentum", thin ? 0 : -12, thin ? 0 : 0.7, 30, thin ? [] : ["RSI is 58: positive momentum.", "MACD is below its signal line."]),
    line("volatility", thin ? 0 : 5, thin ? 0 : 0.6, 20, thin ? [] : ["Daily range (ATR) is 2.5% of price: contained volatility."]),
    line("volume", thin ? 0 : 10, thin ? 0 : 0.5, 15, thin ? [] : ["Volume is 1.0x its 20-day average: no spike."]),
  ];
}

export function mockAnalyze(rawSymbol: string, q: AnalyzeQ, ctx: AnalyzeCtx, record = true): AnalyzeOut {
  const sym = norm(rawSymbol);
  const c = CATALOG.find((x) => x.symbol === sym);
  if (!c) throw new ApiError(404, `Symbol ${sym} not found`);
  if (record) history = [{ symbol: sym, searched_at: new Date().toISOString() }, ...history.filter((h) => h.symbol !== sym)];

  const price: ScoutReport["price"] = c.thin
    ? { price: c.price, currency: c.cur, as_of: STALE_AS_OF, source: "yahoo", basis: "last_close", flag: "stale", change_pct: null, is_fresh: false }
    : { price: c.price, currency: c.cur, as_of: AS_OF, source: "yahoo", basis: "live", flag: null, change_pct: 1.2, is_fresh: true };
  const scout: ScoutReport = {
    symbol: sym, name_en: c.en, name_he: c.he, market: c.market, asset_type: c.type, sector: c.sector, country: c.country, currency: c.cur,
    verified: true, price, price_reason: c.thin ? "Last close only: no live quote for this symbol." : null, bars: c.thin ? 40 : 250, history_as_of: c.thin ? STALE_AS_OF : AS_OF,
    not_available: c.thin
      ? [{ name: "analyst_consensus", confidence: 0, reason: "No analyst coverage found." }, { name: "news", confidence: 0, reason: "No recent news." }]
      : c.market === "TASE" ? [{ name: "insider", confidence: 0, reason: "No insider data for this market." }] : [],
    explanation: explain(`${c.en}: ${c.sector}, ${c.country}.`, { sources: [{ name: "Yahoo Finance", as_of: price.as_of, detail: "Quote and daily bars" }] }),
  };
  const chart: ChartReport = {
    available: !c.thin, score: c.thin ? 0 : 24, confidence: c.thin ? 0 : 0.7, breakdown: signalLines(!!c.thin), indicators: c.thin ? {} : { rsi14: 58, atr14: r2(c.price * 0.025), sma50: r2(c.price * 0.96), sma200: r2(c.price * 0.9) },
    levels: c.thin ? [] : [
      { kind: "support", price: r2(c.price * 0.93), distance_pct: -7, touches: 3 },
      { kind: "resistance", price: r2(c.price * 1.08), distance_pct: 8, touches: 2 },
    ],
    annotations: c.thin ? [] : [
      { kind: "support", label: "Support (touched 3x)", price: r2(c.price * 0.93), as_of: AS_OF },
      { kind: "pattern", label: "golden cross", as_of: "2026-10-02T00:00:00Z" },
      { kind: "moving_average", label: "SMA200", price: r2(c.price * 0.9), as_of: AS_OF },
    ],
    candles: c.thin ? [] : mockCandles(c.price),
    data_as_of: c.thin ? STALE_AS_OF : AS_OF, bars: c.thin ? 40 : 250,
    explanation: explain(c.thin ? "Too little price history for chart signals." : "Technical score +24 (trend +38, momentum -12).", {
      contributions: signalLines(!!c.thin).map((s) => ({ name: s.name, score: s.score, weight: s.weight, confidence: s.confidence, reasons: s.reasons })),
      invalidation_risks: ["A close below the 200-day average would weaken the trend reading."],
      sources: [{ name: "Price history (Yahoo Finance)", as_of: AS_OF, detail: "Daily bars" }],
    }),
  };

  const held = q.portfolio_id ? ctx.holdings.find((h) => h.symbol === sym) : undefined;
  const needs: PortfolioFit["needs_input"] = [];
  if (!q.portfolio_id) needs.push("portfolio_id");
  if (q.amount === null) needs.push("amount");
  if (!q.currency) needs.push("currency");
  const horizon = q.horizon ?? held?.horizon ?? null;
  if (!horizon) needs.push("horizon");

  const base: CandidateBase = { score: chart.score, confidence: chart.confidence, scoreAvailable: chart.available };
  if (needs.length > 0) {
    const fit: PortfolioFit = {
      status: "incomplete", needs_input: needs, summary: "Fit cannot be checked until the missing inputs are given. Nothing is assumed for you.",
      portfolio_id: q.portfolio_id, held: held ? heldOut(held) : null, exposures: [], caps_broken_at_requested_amount: [], rules: [], rules_not_checked: ["position size", "sector exposure", "country exposure"],
      explanation: explain("Missing inputs: " + needs.join(", ") + "."),
    };
    return out(sym, scout, chart, fit, base, needs);
  }

  const rf = ctx.riskFilter;
  const V = ctx.holdings.reduce((a, h) => a + h.valueIls, 0) || 1;
  const amtIls = toIls(q.amount as number, q.currency as string);
  const heldVal = held?.valueIls ?? 0;
  const sectorVal = ctx.holdings.filter((h) => h.sector === c.sector).reduce((a, h) => a + h.valueIls, 0);
  const countryVal = ctx.holdings.filter((h) => h.country === c.country).reduce((a, h) => a + h.valueIls, 0);
  const caps: [ExposureCheck["dimension"], string, ExposureCheck["rule"], number, number][] = [
    ["position", sym, "max_position_pct", rf?.max_position_pct ?? 12, heldVal],
    ["sector", c.sector, "max_sector_pct", rf?.max_sector_pct ?? 30, sectorVal],
    ["country", c.country, "max_country_pct", rf?.max_country_pct ?? 60, countryVal],
  ];
  const exposures: ExposureCheck[] = caps.map(([dimension, name, rule, limit, cur]) => {
    const after = ((cur + amtIls) / (V + amtIls)) * 100;
    const L = limit / 100;
    const headroom = Math.max(0, (L * V - cur) / (1 - L));
    return {
      dimension, name, rule, applies: true, limit_pct: limit, limit_source: ctx.riskFilter?.preset ?? "balanced_aggressive", before_pct: r2((cur / V) * 100), after_pct: r2(after),
      breaks: after > limit + 1e-9, headroom_ils: r2(headroom),
      reason_text: { code: after > limit ? "exposure_breaks" : "exposure_fits", params: { dimension, name, before_pct: r2((cur / V) * 100), after_pct: r2(after), limit_pct: limit } },
      reason: after > limit ? `${name} would be ${r2(after)}%, above the ${limit}% cap.` : `${name} stays within the ${limit}% cap.`,
    };
  });
  const broken = exposures.filter((e) => e.breaks);
  const maxAdd = Math.min(amtIls, ...exposures.map((e) => e.headroom_ils ?? 0));
  const status: PortfolioFit["status"] = broken.length === 0 ? "fits" : maxAdd > price.price * 0.5 * (c.cur === "USD" ? FX : 1) ? "fits_smaller" : "does_not_fit";
  const sizeIls = status === "fits" ? amtIls : Math.max(0, maxAdd);
  const priceIls = toIls(c.price, c.cur);
  const qty = status === "does_not_fit" ? 0 : Math.floor((sizeIls / priceIls) * 1000) / 1000;
  const binding = [...exposures].sort((a, b) => (a.headroom_ils ?? 0) - (b.headroom_ils ?? 0))[0];

  const lv = mockExitLevels({ id: 0, symbol: sym, cur: c.cur, qty: Math.max(qty, 1), price: c.price, cost: held?.cost ?? null, horizon, stale: c.stale, noData: c.thin }, { horizon, risk: q.risk }, V);
  const hasLevels = lv.status === "levels";
  const costNative = qty * c.price;
  const suggested = qty > 0 ? {
    quantity: qty, cost_native: r2(costNative), currency: c.cur, cost_ils: r2(toIls(costNative, c.cur)), cost_usd: r2(c.cur === "USD" ? costNative : costNative / FX),
    pct_of_amount: r2((sizeIls / amtIls) * 100), position_pct_after: exposures[0].after_pct ?? null, sector_pct_after: exposures[1].after_pct ?? null, country_pct_after: exposures[2].after_pct ?? null,
    risk_ils: r2(lv.risk_to_stop ? lv.risk_to_stop.ils / Math.max(qty, 1) * qty : 0), risk_pct_of_portfolio: r2(lv.risk_to_stop ? ((lv.risk_to_stop.ils / Math.max(qty, 1)) * qty / V) * 100 : 0),
    limited_by: status === "fits_smaller" ? broken.map((e) => e.rule) : [],
  } : null;
  const fit: PortfolioFit = {
    status, needs_input: [], portfolio_id: q.portfolio_id, mode: held ? "increase_existing" : "new_position", held: held ? heldOut(held) : null,
    risk_preset: q.risk ?? ctx.riskFilter?.preset ?? "balanced_aggressive", risk_source: q.risk ? "request" : "portfolio", horizon, horizon_source: q.horizon ? "request" : "holding",
    amount: q.amount, currency: q.currency, amount_ils: r2(amtIls), portfolio_value_ils: r2(V),
    max_position_size: {
      position_limit_pct: exposures[0].limit_pct, limit_source: exposures[0].limit_source, max_additional_ils: r2(Math.max(0, maxAdd)), max_additional_usd: r2(Math.max(0, maxAdd) / FX),
      binding_rule: broken.length ? binding.rule : null,
      reason_text: broken.length ? { code: "max_size_binding", params: { max_additional_ils: r2(Math.max(0, maxAdd)), rule: binding.rule } } : { code: "max_size_no_limit", params: {} },
      reason: broken.length ? `${binding.name} is the tightest cap (${binding.limit_pct}%).` : "Your requested amount is inside every cap.",
    },
    exposures, caps_broken_at_requested_amount: broken.map((e) => e.rule),
    rules: [{ rule: "min_rr", status: hasLevels ? "pass" : "not_evaluated", value: hasLevels ? 1.5 : null, limit: rf?.min_rr ?? 1.5, reason: hasLevels ? "First take-profit meets the minimum reward-to-risk." : "No levels, so reward-to-risk was not evaluated." }],
    rules_not_checked: hasLevels ? [] : ["reward-to-risk", "max loss per position"],
    suggested_size: suggested, entry: hasLevels ? c.price : null, levels: lv, levels_unavailable_reason: hasLevels ? null : lv.reason,
    summary: status === "fits" ? "The requested amount fits your limits." : status === "fits_smaller" ? "A smaller amount fits your limits." : "No amount fits your limits right now.",
    explanation: explain(`${status}: ${broken.length ? broken.map((e) => e.reason).join(" ") : "all caps respected."}`, {
      risk_rules_applied: exposures.map((e) => `${e.rule} ${e.limit_pct}%`), invalidation_risks: ["Prices move: exposure is computed at the last known price."],
      sources: [{ name: "Your portfolio", as_of: AS_OF, detail: "Holdings and risk filter" }],
    }),
  };
  return out(sym, scout, chart, fit, base, []);
}

interface CandidateBase { score: number; confidence: number; scoreAvailable: boolean }
const heldOut = (h: AnalyzeHeld): NonNullable<PortfolioFit["held"]> => ({ holding_id: h.id, quantity: h.quantity, value_ils: r2(h.valueIls), weight_pct: r2(h.weightOf), horizon: h.horizon, via_dual_listing: false });

function out(sym: string, scout: ScoutReport, chart: ChartReport, fit: PortfolioFit, b: CandidateBase, needs: AnalyzeOut["needs_input"]): AnalyzeOut {
  return {
    symbol: sym, generated_at: AS_OF, cached: false, scout, chart, portfolio_fit: fit,
    candidate_info: { score: b.score, confidence: b.confidence, score_available: b.scoreAvailable, fit_passes: fit.status === "incomplete" ? null : fit.status === "fits", launch_gate_open: false, launch_gate_reasons: ["No backtest has been run for the active weights configuration.", "Paper trading has run 0.0 of 4 required weeks (0 call(s) recorded).", "Only 0 of 50 required calls are resolved at 1 month (0 recorded)."], notice: LIMITED_NOTICE },
    summary: `${scout.name_en}: neutral analysis from price history and your limits.`, llm_used: false, needs_input: needs, disclaimer: "Not financial advice.",
  } as AnalyzeOut;
}

export function mockAsk(rawSymbol: string, body: { question?: string; notes?: string | null }): AskOut {
  const sym = norm(rawSymbol);
  if (!CATALOG.some((x) => x.symbol === sym)) throw new ApiError(404, `Symbol ${sym} not found`);
  const q = String(body.question ?? "");
  if (!q.trim() || q.length > 1000) throw new ApiError(422, "Validation error", undefined, { detail: [{ type: "string_too_short", loc: ["body", "question"], msg: "Question must be 1-1000 characters" }] });
  const declined = /\b(buy|sell|should i|trade)\b/i.test(q);
  return {
    symbol: sym, llm_used: false, notes_stored: false, question_declined: declined, grounded_in: declined ? [] : ["scout.price", "chart.indicators.rsi14", "chart.levels"],
    answer: declined
      ? "I cannot tell you whether to trade. Here is what the data shows: the trend signal is positive and the nearest support is about 7% below the price."
      : "From the computed data: RSI(14) is 58 and the 200-day average sits below the price. These are computed values, not a forecast.",
    privacy_note: "Your question and notes stay on the server and are never sent to a free AI provider.", disclaimer: "Not financial advice.",
  };
}
