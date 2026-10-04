/* Mock buy-ideas response (NEXT_PUBLIC_API_MOCK=1): neutral candidates plus skipped symbols with codes. Shapes follow backend/openapi.json. */
import type { BuyCandidate, BuyIdeasIn, BuyIdeasOut, ExitLevel, SkippedItem } from "./api";
import { ApiError } from "./errors";

const FX = 3.7;
const AS_OF = "2026-10-03T08:55:00Z";
const r2 = (n: number) => Math.round(n * 100) / 100;
const HORIZONS = ["1w", "1m", "3m", "6m", "1y"];
const PRESETS = ["very_conservative", "conservative", "balanced", "balanced_aggressive", "aggressive", "very_aggressive"];

interface Pool { symbol: string; en: string; he: string; market: "US" | "TASE" | "CRYPTO"; type: "stock" | "etf" | "crypto"; cur: "USD" | "ILS"; sector: string; country: string; price: number; score: number; conf: number }
const POOL: Pool[] = [
  { symbol: "XOM", en: "Exxon Mobil", he: "אקסון מוביל", market: "US", type: "stock", cur: "USD", sector: "Energy", country: "US", price: 112, score: 41, conf: 0.72 },
  { symbol: "JNJ", en: "Johnson & Johnson", he: "ג'ונסון אנד ג'ונסון", market: "US", type: "stock", cur: "USD", sector: "Healthcare", country: "US", price: 158, score: 33, conf: 0.66 },
  { symbol: "VOO", en: "Vanguard S&P 500 ETF", he: "ואנגארד S&P 500", market: "US", type: "etf", cur: "USD", sector: "Broad market", country: "US", price: 505, score: 27, conf: 0.8 },
  { symbol: "ICL.TA", en: "ICL Group", he: "איי.סי.אל", market: "TASE", type: "stock", cur: "ILS", sector: "Materials", country: "Israel", price: 21.4, score: 36, conf: 0.58 },
  { symbol: "BTC-USD", en: "Bitcoin", he: "ביטקוין", market: "CRYPTO", type: "crypto", cur: "USD", sector: "Crypto", country: "Global", price: 64000, score: 22, conf: 0.5 },
];
const SKIPPED: (SkippedItem & { market: Pool["market"]; type: Pool["type"] })[] = [
  { symbol: "MSFT", name_en: "Microsoft", code: "country_cap", reason: "Adding it would take US exposure above the country cap.", market: "US", type: "stock" },
  { symbol: "AMD", name_en: "AMD", code: "sector_cap", reason: "Technology is already at the sector cap.", market: "US", type: "stock" },
  { symbol: "LUMI.TA", name_en: "Bank Leumi", code: "stale_price", reason: "The price is the last close, not a live quote.", market: "TASE", type: "stock" },
  { symbol: "ESLT.TA", name_en: "Elbit Systems", code: "low_confidence", reason: "Confidence is below the minimum.", market: "TASE", type: "stock" },
  { symbol: "ETH-USD", name_en: "Ethereum", code: "volatility_cap", reason: "Volatility is above the risk preset's cap.", market: "CRYPTO", type: "crypto" },
];

function lvl(kind: ExitLevel["kind"], label: string, price: number, entry: number, rr: number | null, source: string): ExitLevel {
  const d = ((price - entry) / entry) * 100;
  return {
    kind, label, price: r2(price), distance_pct: r2(d), vs_price_ils: 0, vs_price_usd: 0, rr, reached: false, source, reason: `${label} from ${source}.`,
    explanation: { version: 1, as_of: AS_OF, summary: `${label} from ${source}.`, sources: [{ name: "Price history (Yahoo Finance)", as_of: AS_OF, detail: "Daily bars" }] },
  };
}

function candidate(p: Pool, rank: number, amount: number, amountIls: number, horizon: string): BuyCandidate {
  const stopP = p.price * 0.93;
  const tp1 = p.price * 1.1;
  const tp2 = p.price * 1.18;
  const spendIls = amountIls * 0.4;
  const spendNative = p.cur === "USD" ? spendIls / FX : spendIls;
  const qty = p.type === "crypto" ? r2(spendNative / p.price) : Math.max(1, Math.floor(spendNative / p.price));
  const costNative = r2(qty * p.price);
  const costIls = p.cur === "USD" ? r2(costNative * FX) : costNative;
  void amount;
  return {
    rank, symbol: p.symbol, name_en: p.en, name_he: p.he, market: p.market, asset_type: p.type, sector: p.sector, country: p.country, currency: p.cur,
    score: p.score, confidence: p.conf, rank_score: p.score + 3, diversification_bonus: 3, score_as_of: AS_OF, price: p.price, price_as_of: AS_OF,
    entry: p.price, stop: lvl("stop", "Stop", stopP, p.price, null, "ATR"),
    take_profits: [lvl("take_profit", "Take-profit 1", tp1, p.price, 1.5, "resistance"), lvl("take_profit", "Take-profit 2", tp2, p.price, 2.6, "analyst target")],
    best_rr: 2.6, annualised_volatility_pct: 24.5,
    size: {
      quantity: qty, cost_native: costNative, currency: p.cur, cost_ils: costIls, cost_usd: r2(costIls / FX), pct_of_amount: r2((costIls / amountIls) * 100),
      position_pct_after: 6.2, sector_pct_after: 14.1, country_pct_after: 48.3, risk_ils: r2(costIls * 0.07), risk_pct_of_portfolio: 0.6, limited_by: [],
    },
    reasons: [`Score ${p.score} with confidence ${p.conf} from cached signals.`, `Fits a ${horizon} holding period.`],
    explanation: {
      version: 1, as_of: AS_OF, summary: `${p.en}: score ${p.score}, confidence ${p.conf}. Entry, stop and take-profits come from the chart for a ${horizon} holding period.`,
      inputs: { score: p.score, confidence: p.conf, "diversification bonus": 3 },
      rules_applied: ["Cached universe score", "Diversification bonus for under-represented sectors"],
      risk_rules_applied: ["Sector and country caps checked", "Minimum reward/risk checked"],
      invalidation_risks: ["A gap through the stop can fill worse than the stop itself."],
      sources: [{ name: "Cached score card", as_of: AS_OF, detail: "Refreshed by the background job" }],
    },
  };
}

export function mockBuyIdeas(pid: number, b: Partial<BuyIdeasIn>, portfolioExists: boolean): BuyIdeasOut {
  const bad = (k: string) => new ApiError(422, "Validation error", undefined, { detail: [{ type: "missing", loc: ["body", k], msg: "Field required" }] });
  if (!portfolioExists) throw new ApiError(404, "Portfolio not found");
  if (typeof b.amount !== "number" || !(b.amount > 0)) throw bad("amount");
  if (b.currency !== "ILS" && b.currency !== "USD") throw bad("currency");
  if (!HORIZONS.includes(String(b.horizon))) throw bad("horizon");
  if (!PRESETS.includes(String(b.risk))) throw bad("risk");
  if (!Array.isArray(b.markets) || b.markets.length === 0) throw bad("markets");
  if (!Array.isArray(b.asset_types) || b.asset_types.length === 0) throw bad("asset_types");
  const markets = b.markets as string[];
  const types = b.asset_types as string[];
  const excluded = new Set(b.exclude_symbols ?? []);
  const amountIls = b.currency === "USD" ? b.amount * FX : b.amount;
  const inScope = (x: { market: string; type: string }) => markets.includes(x.market) && types.includes(x.type);
  const candidates = POOL.filter((x) => inScope(x) && !excluded.has(x.symbol)).map((x, i) => candidate(x, i + 1, b.amount as number, amountIls, String(b.horizon)));
  const skipped: SkippedItem[] = [
    ...SKIPPED.filter(inScope).map(({ symbol, name_en, code, reason }) => ({ symbol, name_en, code, reason })),
    ...POOL.filter((x) => inScope(x) && excluded.has(x.symbol)).map((x) => ({ symbol: x.symbol, name_en: x.en, code: "excluded_by_user" as const, reason: "You excluded this symbol." })),
  ];
  return {
    portfolio_id: pid, generated_at: AS_OF, amount: b.amount, currency: b.currency, amount_ils: r2(amountIls), horizon: String(b.horizon), risk_preset: String(b.risk),
    markets, asset_types: types, universe_size: POOL.length + SKIPPED.length, candidates, skipped,
    launch_gate_open: false, launch_gate_reasons: ["No passing backtest for the active weights config", "Paper-trading gate: 2 of 4 weeks completed"],
    notice: "Scores are not yet validated by a backtest and paper trading. These are neutral candidates for you to research, not instructions to trade. Not financial advice.",
    disclaimer: "Not financial advice.",
  };
}
