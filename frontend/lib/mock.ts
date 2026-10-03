/* Fixture data + in-memory handler used when NEXT_PUBLIC_API_MOCK=1. */
import type {
  Holding, Summary, Portfolio, RiskFilter, RiskPreset, ScoreCardDetail, XrayRaw, HeatmapItem,
  ImportDraft, ImportRow, PriceAlert, Me, Horizon, SignalBreakdown, ProposedChange,
} from "./api";
import type { LaunchGate, SessionInfo } from "./api";
import { ApiError } from "./errors";

const FX = 3.7; // USD/ILS
const AS_OF = "2026-10-03T08:55:00Z";

const PRESETS: RiskPreset[] = [
  ["very_conservative", 5, 15, 30, 4, 0.5, 4, 3, "fixed", 5],
  ["conservative", 8, 20, 40, 6, 0.75, 7, 2.5, "fixed", 8],
  ["balanced", 10, 25, 50, 8, 1, 10, 2, "both", 10],
  ["balanced_aggressive", 12, 30, 60, 12, 1.5, 12, 2, "both", 15],
  ["aggressive", 15, 35, 70, 15, 2, 15, 1.5, "trailing", 20],
  ["very_aggressive", 20, 45, 80, 20, 3, 20, 1.5, "trailing", 25],
].map((r) => ({
  name: r[0] as string,
  preset: r[0] as string,
  max_position_pct: r[1] as number,
  max_sector_pct: r[2] as number,
  max_country_pct: r[3] as number,
  max_loss_per_position_pct: r[4] as number,
  max_portfolio_risk_per_trade_pct: r[5] as number,
  max_total_portfolio_risk_pct: r[6] as number,
  min_rr: r[7] as number,
  stop_type: r[8] as RiskFilter["stop_type"],
  drawdown_defensive_pct: r[9] as number,
})) as unknown as RiskPreset[];

interface Seed {
  id: number; pid: number; symbol: string; en: string; he: string; type: Holding["asset_type"];
  market: Holding["market"]; qty: number; price: number; cur: string; chg: number;
  /** null = unknown cost basis (an imported row without cost): P&L is null, never 0. */
  cost: number | null;
  horizon: Horizon | null; tech: number; pat: number; conf: number; sector: string; country: string;
  /** Last price is older than the staleness limit. */
  stale?: boolean;
  /** No signal data yet (new listing): score card is unavailable, confidence 0. */
  noData?: boolean;
}
const SEEDS: Seed[] = [
  { id: 1, pid: 1, symbol: "TEVA.TA", en: "Teva", he: "טבע", type: "stock", market: "TASE", qty: 600, price: 62.4, cur: "ILS", chg: 1.8, cost: 51.2, horizon: "3m", tech: 42, pat: 25, conf: 0.7, sector: "Healthcare", country: "Israel" },
  { id: 2, pid: 1, symbol: "LUMI.TA", en: "Bank Leumi", he: "בנק לאומי", type: "stock", market: "TASE", qty: 900, price: 47.9, cur: "ILS", chg: -0.6, cost: 44.0, horizon: null, tech: 12, pat: -8, conf: 0.6, sector: "Financials", country: "Israel" },
  { id: 3, pid: 1, symbol: "NICE.TA", en: "NICE Systems", he: "נייס", type: "stock", market: "TASE", qty: 40, price: 612, cur: "ILS", chg: -1.9, cost: 701, horizon: "6m", tech: -35, pat: -20, conf: 0.55, sector: "Technology", country: "Israel" },
  { id: 4, pid: 1, symbol: "ESLT.TA", en: "Elbit Systems", he: "אלביט מערכות", type: "stock", market: "TASE", qty: 25, price: 1480, cur: "ILS", chg: 0.9, cost: 1210, horizon: null, tech: 55, pat: 40, conf: 0.75, sector: "Industrials", country: "Israel" },
  { id: 5, pid: 1, symbol: "NVDA", en: "NVIDIA", he: "אנבידיה", type: "stock", market: "US", qty: 30, price: 142.3, cur: "USD", chg: 2.4, cost: 98.5, horizon: "1m", tech: 61, pat: 33, conf: 0.8, sector: "Technology", country: "United States" },
  { id: 6, pid: 1, symbol: "AAPL", en: "Apple", he: "אפל", type: "stock", market: "US", qty: 20, price: 231.1, cur: "USD", chg: -0.3, cost: 218.7, horizon: "1y", tech: 8, pat: 5, conf: 0.8, sector: "Technology", country: "United States" },
  { id: 7, pid: 1, symbol: "CHKP", en: "Check Point", he: "צ'ק פוינט", type: "stock", market: "US", qty: 15, price: 203.5, cur: "USD", chg: 0.4, cost: 188.0, horizon: null, tech: 20, pat: 10, conf: 0.65, sector: "Technology", country: "Israel" },
  { id: 8, pid: 1, symbol: "BTC-USD", en: "Bitcoin", he: "ביטקוין", type: "crypto", market: "CRYPTO", qty: 0.12, price: 71200, cur: "USD", chg: 3.1, cost: 58000, horizon: "6m", tech: 48, pat: 30, conf: 0.6, sector: "Crypto", country: "Global" },
  { id: 9, pid: 2, symbol: "ETH-USD", en: "Ethereum", he: "איתריום", type: "crypto", market: "CRYPTO", qty: 1.5, price: 3350, cur: "USD", chg: -2.2, cost: 3600, horizon: null, tech: -22, pat: -15, conf: 0.5, sector: "Crypto", country: "Global" },
  { id: 10, pid: 2, symbol: "SPY", en: "SPDR S&P 500 ETF", he: "קרן S&P 500", type: "etf", market: "US", qty: 18, price: 585.2, cur: "USD", chg: 0.5, cost: 540, horizon: "1y", tech: 30, pat: 18, conf: 0.85, sector: "Diversified", country: "United States" },
  { id: 11, pid: 1, symbol: "ARNA.TA", en: "Arena Fund (cost unknown)", he: "קרן ארנה (עלות לא ידועה)", type: "fund", market: "TASE", qty: 120, price: 31.4, cur: "ILS", chg: 0.2, cost: null, horizon: null, tech: 0, pat: 0, conf: 0, sector: "Diversified", country: "Israel", stale: true, noData: true },
];

const PORTFOLIOS: Portfolio[] = [
  { id: 1, name: "התיק הראשי", base_currency: "ILS", risk_filter: { ...PRESETS[3] }, tracking_started_at: "2026-07-06", created_at: "2026-07-06T09:00:00Z" },
  { id: 2, name: "תיק ארה״ב וקריפטו", base_currency: "USD", risk_filter: { ...PRESETS[2] }, tracking_started_at: null, created_at: "2026-08-01T09:00:00Z" },
];

const valueIls = (s: Seed) => s.qty * s.price * (s.cur === "USD" ? FX : 1);
const costIls = (s: Seed) => (s.cost === null ? null : s.qty * s.cost * (s.cur === "USD" ? FX : 1));

function holdingsFor(pid: number): Holding[] {
  const seeds = SEEDS.filter((s) => s.pid === pid);
  const total = seeds.reduce((a, s) => a + valueIls(s), 0);
  return seeds.map((s) => {
    const v = valueIls(s);
    const cost = costIls(s);
    return {
      id: s.id, symbol: s.symbol, name_en: s.en, name_he: s.he, asset_type: s.type, market: s.market,
      quantity: s.qty, price: s.price, currency: s.cur, day_change_pct: s.chg, value_ils: v,
      pnl: cost === null ? null : { ils: v - cost, usd: (v - cost) / FX, pct: ((v - cost) / cost) * 100 },
      price_stale: !!s.stale,
      weight_pct: (v / total) * 100, horizon: s.horizon,
      stop_tp_status: s.horizon ? "missing" : "needs_horizon",
      score_card: {
        total: Math.round(s.tech * 0.67 + s.pat * 0.33), technical: s.tech, patterns: s.pat, confidence: s.conf,
      },
    };
  });
}

function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

/** 2026-10-03 is a Saturday; the week (Sunday to Saturday, Asia/Jerusalem) started on Sunday 2026-09-27. */
const WEEK_START = "2026-09-27";
const FIRST_SUNDAY = "2026-07-12";

function summaryFor(pid: number | "combined"): Summary {
  // Portfolio 2 is brand new: no tracking start yet, so no since-start date and no history.
  const fresh = pid === 2;
  const start = "2026-07-06";
  const series = fresh ? [] : Array.from({ length: 88 }, (_, i) => {
    const t = i + 1;
    const lag = i >= 86; // benchmark data lags: the latest two days have no benchmark point yet
    return {
      date: addDays(start, i),
      pct: +(t * 0.18 + Math.sin(t / 4) * 2.2 + Math.cos(t / 9) * 1.1 - 1).toFixed(2),
      sp500_pct: lag ? null : +(t * 0.1 + Math.sin(t / 5 + 1) * 1.5).toFixed(2),
      ta125_pct: lag ? null : +(t * 0.12 + Math.cos(t / 6) * 1.7).toFixed(2),
    };
  });
  const valueI = SEEDS.filter((s) => pid === "combined" || s.pid === pid).reduce((a, s) => a + valueIls(s), 0);
  const mk = (pct: number) => ({ ils: (valueI * pct) / 100, usd: (valueI * pct) / 100 / FX, pct });
  const weekly = fresh ? [] : [1.2, -0.8, 2.1, 0.4, -1.6, 0.9, 1.7, -0.3, 2.6, -2.1, 0.8, 1.4];
  const monthly = fresh ? [] : [3.2, -1.4, 4.6, 2.2, -2.8, 3.9];
  return {
    value: { ils: valueI, usd: valueI / FX },
    day_pnl: mk(0.84),
    week_pnl: mk(fresh ? 0 : 1.4),
    month_pnl: mk(fresh ? 0 : 3.9),
    since_start_pnl: mk(fresh ? 0 : (series[series.length - 1]?.pct ?? 0)),
    since_start_date: fresh ? null : start,
    week_start: WEEK_START,
    fx_stale: false,
    weekly_bars: weekly.map((pct, i) => ({ week_start: addDays(FIRST_SUNDAY, i * 7), pnl_ils: Math.round((valueI * pct) / 100), pct })),
    monthly_bars: monthly.map((pct, i) => ({ month: `2026-${String(i + 5).padStart(2, "0")}`, pnl_ils: Math.round((valueI * pct) / 100), pct })),
    since_start_series: series,
    as_of: AS_OF,
    markets: { US: { open: false }, TASE: { open: true }, CRYPTO: { open: true } },
  };
}

function signals(h: Seed): SignalBreakdown[] {
  const asOf = AS_OF;
  return [
    {
      name: "technical", score: h.tech, confidence: h.conf, weight: h.noData ? 0 : 67, nominal_weight: 67, reasons: [
        h.tech >= 0 ? `Price is above its 50-day average` : `Price is below its 50-day average`,
        `RSI(14) is ${h.tech >= 0 ? 58 : 36}`,
      ], data_as_of: asOf,
      explanation: {
        summary: h.tech >= 0 ? "Trend and momentum lean positive." : "Trend and momentum lean negative.",
        inputs: { "SMA 50": +(h.price * (h.tech >= 0 ? 0.96 : 1.05)).toFixed(2), "SMA 200": +(h.price * 0.9).toFixed(2), "RSI(14)": h.tech >= 0 ? 58 : 36, "MACD hist": h.tech >= 0 ? 0.42 : -0.37 },
        rules_applied: ["Trend: price vs SMA 20/50/200", "Momentum: RSI, MACD, Stochastic", "Volatility: Bollinger position", "Volume: OBV slope"],
      },
    },
    {
      name: "patterns", score: h.pat, confidence: h.noData ? 0 : Math.max(0.3, h.conf - 0.2), weight: h.noData ? 0 : 33, nominal_weight: 33, reasons: [
        h.pat >= 0 ? "Held support 3% below the price" : "Failed to break resistance twice",
      ], data_as_of: asOf,
      explanation: {
        summary: h.pat >= 0 ? "Chart structure is constructive." : "Chart structure is weak.",
        inputs: { support: +(h.price * 0.97).toFixed(2), resistance: +(h.price * 1.06).toFixed(2), "last cross": h.pat >= 0 ? "golden cross" : "none" },
        rules_applied: ["Support/resistance proximity", "Higher highs / lower lows", "Golden / death cross"],
      },
    },
  ];
}

function scorecard(hid: number): ScoreCardDetail {
  const s = SEEDS.find((x) => x.id === hid) ?? SEEDS[0];
  const sig = signals(s);
  const h = holdingsFor(s.pid).find((x) => x.id === s.id)!;
  return {
    holding_id: s.id, portfolio_id: s.pid, symbol: s.symbol, name_en: s.en, name_he: s.he,
    horizon: s.horizon, total: h.score_card.total, confidence: s.conf, available: !s.noData, validated: false, signals: sig,
    disclaimer: "Not financial advice. Scores are experimental and not validated.",
    explanation: {
      summary: `Combined score ${h.score_card.total} from technical (67%) and chart-pattern (33%) signals. Analyst, geopolitical and sentiment signals are not available in Phase 1, so their weight was redistributed.`,
      inputs: { technical: s.tech, patterns: s.pat, "weight redistributed": "analyst, geopolitics, sentiment" },
      rules_applied: ["Weighted combination with confidence redistribution", "Missing signals get confidence 0, never a neutral score", "No buy/sell verdict until the backtest passes"],
    },
  };
}

function xray(): XrayRaw {
  const hs = holdingsFor(1);
  const sumMap = (key: (s: Seed) => string) => {
    const m: Record<string, number> = {};
    for (const h of hs) { const s = SEEDS.find((x) => x.id === h.id)!; m[key(s)] = (m[key(s)] ?? 0) + h.weight_pct; }
    return m;
  };
  const sum = (key: (s: Seed) => string) => sumMap(key);
  const items = (key: (s: Seed) => string) => Object.entries(sumMap(key)).map(([name, weight_pct]) => ({ name, weight_pct }));
  return {
    concentration: [...hs].sort((a, b) => b.weight_pct - a.weight_pct).slice(0, 5).map((h) => ({ symbol: h.symbol, weight_pct: h.weight_pct })),
    currency_exposure: items((s) => s.cur),
    country_exposure: items((s) => s.country),
    sector_exposure: items((s) => s.sector),
    home_bias: { israel_pct: sum((s) => s.country).Israel ?? 0 },
    breaches: [
      { rule: "max_position_pct", value: Math.max(...hs.map((h) => h.weight_pct)), limit: 12, why: "Your largest position is above your 12% limit.", symbol: [...hs].sort((a, b) => b.weight_pct - a.weight_pct)[0].symbol },
      { rule: "max_sector_pct", value: sum((s) => s.sector).Technology, limit: 30, why: "Technology exposure is above your 30% sector limit.", symbol: null },
    ],
  };
}

function heatmap(): HeatmapItem[] {
  return holdingsFor(1).map((h) => ({ symbol: h.symbol, sector: SEEDS.find((s) => s.id === h.id)!.sector, weight_pct: h.weight_pct, day_change_pct: h.day_change_pct }));
}

const EXPIRES = "2026-10-04T08:55:00Z"; // 24 h after the draft was created

let draft: ImportDraft = {
  id: 1, portfolio_id: 1, status: "draft", expires_at: EXPIRES,
  rows: [
    { index: 0, name: "טבע", symbol: "TEVA.TA", tase_number: "629014", quantity: 650, price: 62.4, value: 40560, cost: 51.2, currency: "ILS", unit: "ILS", matched_name: "Teva", flags: [] },
    { index: 1, name: "לאומי", symbol: "LUMI.TA", quantity: 900, price: 4790, value: 43110, cost: 44, currency: "ILS", unit: "agorot", matched_name: "Bank Leumi", flags: [] },
    { index: 2, name: "אנבידיה", symbol: "NVDA", quantity: 30, price: 142.3, value: 5100, cost: 98.5, currency: "USD", unit: "USD", matched_name: "NVIDIA", flags: ["value_mismatch", "currency_changed"] },
    { index: 3, name: "מניה לא מזוהה בע״מ", symbol: null, quantity: 100, price: 12.5, value: 1250, cost: null, currency: "ILS", unit: "ILS", matched_name: null, flags: ["unmatched", "low_confidence_match"], candidates: [{ symbol: "MNDY", name: "monday.com", score: 58 }, { symbol: "MGDL.TA", name: "Migdal Insurance", score: 52 }] },
  ],
  proposed_changes: [
    { row_index: 0, symbol: "TEVA.TA", type: "buy", quantity: 50, amount: 3120, currency: "ILS" },
    { row_index: 1, symbol: "LUMI.TA", type: "buy", quantity: 0, amount: 0, currency: "ILS" },
    { row_index: 2, symbol: "NVDA", type: "sell", quantity: 5, amount: 711.5, currency: "USD" },
    { row_index: 3, symbol: null, type: "deposit", quantity: null, amount: 1250, currency: "ILS" },
  ],
};

/** Mimics the server: keeps stock-looking rows, matches held symbols, flags weak matches, proposes changes. */
function draftFromRows(pid: number, rows: ImportRow[]): ImportDraft {
  const kept = rows.filter((r) => r.name.trim() !== "" || r.symbol).map((r, i) => {
    const flags: ImportRow["flags"] = r.flags.filter((f) => f !== "low_confidence_match" && f !== "unmatched");
    const weak = !r.symbol && !r.tase_number;
    if (weak) flags.push("unmatched", "low_confidence_match");
    return { ...r, symbol: r.symbol ?? (r.tase_number ? "TEVA.TA" : null), index: i, flags, candidates: weak ? [{ symbol: "MNDY", name: "monday.com", score: 58 }] : [] };
  });
  const held = holdingsFor(pid);
  // Like the server: only real quantity differences, plus a sale (row_index -1) for each held symbol that vanished.
  const changes: ProposedChange[] = [];
  for (const r of kept) {
    const cur = held.find((h) => h.symbol === r.symbol);
    const diff = (r.quantity ?? 0) - (cur?.quantity ?? 0);
    if (diff === 0) continue;
    changes.push({ row_index: r.index, symbol: r.symbol ?? null, type: diff < 0 ? "sell" : "buy", quantity: Math.abs(diff), amount: null, currency: r.currency });
  }
  for (const h of held) {
    if (!kept.some((r) => r.symbol === h.symbol)) changes.push({ row_index: -1, symbol: h.symbol, type: "sell", quantity: h.quantity, amount: null, currency: h.currency });
  }
  draft = { id: 1, portfolio_id: pid, status: "draft", expires_at: EXPIRES, rows: kept, proposed_changes: changes };
  return draft;
}

let alerts: PriceAlert[] = [
  { id: 1, symbol: "TEVA.TA", op: "above", price: 70, active: true, triggered_at: null },
  { id: 2, symbol: "NVDA", op: "below", price: 120, active: true, triggered_at: null },
];
let nextAlert = 3;

/** In mock mode, typing this password for export/delete simulates the server's 403. */
export const WRONG_PASSWORD = "wrong";

let sessions: SessionInfo[] = [
  { id: 1, created_at: "2026-10-03T07:10:00Z", last_seen_at: "2026-10-03T08:55:00Z", current: true },
  { id: 2, created_at: "2026-09-28T18:02:00Z", last_seen_at: "2026-10-01T21:40:00Z", current: false },
  { id: 3, created_at: "2026-09-12T06:30:00Z", last_seen_at: "2026-09-20T12:00:00Z", current: false },
];

const LAUNCH_GATE: LaunchGate = {
  open: false,
  reasons: ["No passing backtest for the active weights config", "Paper-trading gate: 2 of 4 weeks completed"],
};

const ME: Me = { id: 1, email: "demo@example.com", locale: "he", disclaimer_accepted: true, ocr_consent: false, csrf_token: "mock-csrf-token" };

export function mockRequest(method: string, path: string, body?: unknown): unknown {
  const [p] = path.split("?");
  const b = (body ?? {}) as Record<string, unknown>;
  let m: RegExpMatchArray | null;
  if (p === "/auth/me") return ME;
  if (p === "/auth/signup" || p === "/auth/login" || p === "/auth/logout") return ME;
  if (p === "/auth/consent/ocr") { ME.ocr_consent = true; return { ok: true }; }
  if (p === "/me/export" || (p === "/me" && method === "DELETE")) {
    if (typeof b.password !== "string" || b.password === "" || b.password === WRONG_PASSWORD) throw new ApiError(403, "Wrong password");
    return p === "/me" ? undefined : { user: ME, portfolios: PORTFOLIOS, holdings: SEEDS };
  }
  if (p === "/auth/sessions") return sessions;
  if (p === "/auth/sessions/revoke-all") { sessions = sessions.filter((x) => x.current); return undefined; }
  if ((m = p.match(/^\/auth\/sessions\/(\d+)$/))) {
    const target = sessions.find((x) => x.id === Number(m![1]));
    if (!target) throw new ApiError(404, "Not found");
    if (target.current) throw new ApiError(400, "Cannot revoke the current session");
    sessions = sessions.filter((x) => x.id !== target.id);
    return undefined;
  }
  if (p === "/launch-gate") return LAUNCH_GATE;
  if (p === "/portfolios") {
    if (method === "POST") { const np = { id: PORTFOLIOS.length + 1, name: String(b.name), base_currency: (b.base_currency as "ILS") ?? "ILS", risk_filter: { ...PRESETS[2] }, tracking_started_at: null, created_at: AS_OF }; PORTFOLIOS.push(np); return np; }
    return PORTFOLIOS;
  }
  if (p === "/portfolios/combined/summary") return summaryFor("combined");
  if ((m = p.match(/^\/portfolios\/(\d+)\/summary$/))) return summaryFor(Number(m[1]));
  if ((m = p.match(/^\/portfolios\/(\d+)\/holdings\/(\d+)$/)) && method === "PATCH") {
    const s = SEEDS.find((x) => x.id === Number(m![2]));
    if (s && "horizon" in b) s.horizon = (b.horizon as Horizon | null) ?? null;
    return holdingsFor(Number(m[1])).find((h) => h.id === Number(m![2]));
  }
  if ((m = p.match(/^\/portfolios\/(\d+)\/holdings$/))) return holdingsFor(Number(m[1]));
  if ((m = p.match(/^\/portfolios\/(\d+)\/xray$/))) return xray();
  if ((m = p.match(/^\/portfolios\/(\d+)\/heatmap$/))) return heatmap();
  if ((m = p.match(/^\/portfolios\/(\d+)\/imports\/rows$/))) return draftFromRows(Number(m[1]), (b.rows as ImportRow[]) ?? []);
  if ((m = p.match(/^\/portfolios\/(\d+)\/imports$/))) {
    if (typeof Blob !== "undefined" && body instanceof Blob && !/^image\/(png|jpeg|webp)$/.test(body.type)) throw new ApiError(415, "Unsupported media type");
    return draft;
  }
  if ((m = p.match(/^\/portfolios\/(\d+)$/))) {
    const pf = PORTFOLIOS.find((x) => x.id === Number(m![1]));
    if (pf && method === "PATCH") Object.assign(pf, b);
    return pf;
  }
  if (p === "/risk/presets") return PRESETS;
  if (p === "/imports/1/confirm") { draft = { ...draft, status: "confirmed" }; return draft; }
  if ((m = p.match(/^\/imports\/(\d+)$/))) {
    if (method === "PATCH") {
      const patch = b as Partial<ImportDraft>;
      // Like the server: new rows without explicit changes => re-match and recompute the changes.
      const redone = patch.rows && !patch.proposed_changes ? draftFromRows(draft.portfolio_id, patch.rows) : null;
      draft = { ...draft, ...patch, ...(redone ? { rows: redone.rows, proposed_changes: redone.proposed_changes } : {}) };
    }
    return draft;
  }
  if (p === "/securities/search") return [{ symbol: "TEVA.TA", name_en: "Teva", name_he: "טבע", market: "TASE" }];
  if ((m = p.match(/^\/holdings\/(\d+)\/scorecard$/))) return scorecard(Number(m[1]));
  if (p === "/alerts") {
    if (method === "POST") { const a: PriceAlert = { id: nextAlert++, symbol: String(b.symbol), op: b.op as "above", price: Number(b.price), active: true, triggered_at: null }; alerts = [...alerts, a]; return a; }
    return alerts;
  }
  if ((m = p.match(/^\/alerts\/(\d+)$/))) { alerts = alerts.filter((a) => a.id !== Number(m![1])); return undefined; }
  if (p === "/notifications") return [];
  return undefined;
}
