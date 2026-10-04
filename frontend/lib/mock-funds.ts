/* Mock Israeli funds (GemelNet) and dividend calendar. Schema-valid: tests/mock-contract.test.ts checks them with Ajv. */
import type { Dividends, FundDetail, FundReturn, FundSearchItem, FundSearchOut, SymbolDividendStatus, UpcomingDividend } from "./api";
import { ApiError } from "./errors";

const AS_OF = "2026-10-03T08:55:00Z";
const CREDIT = "Data: GemelNet (Ministry of Finance), personal non-commercial use.";
const SOURCE = "GemelNet (data.gov.il)";
const DISCLAIMER = "Not financial advice.";

interface FundSeed { id: string; name: string; classification: string; corp: string }
export const FUNDS: FundSeed[] = [
  { id: "1001", name: "Kupat Gemel Equities Track", classification: "Equities", corp: "Alpha Provident" },
  { id: "1002", name: "Kupat Gemel S&P 500 Track", classification: "Index-linked", corp: "Beta Investments" },
  { id: "1003", name: "Kranot Hishtalmut General", classification: "General", corp: "Gamma Funds" },
];
const item = (f: FundSeed): FundSearchItem => ({ fund_id: f.id, symbol: `GEMEL-${f.id}`, name: f.name, classification: f.classification, managing_corporation: f.corp });

/** Search words that pick a data status (the real API answers these when the dataset is down or rate limited). */
export const FUND_QUERY_UNAVAILABLE = "unavailable";
export const FUND_QUERY_RATE_LIMITED = "ratelimit";

export function mockFundSearch(q: string): FundSearchOut {
  const base = { query: q, source: SOURCE, credit: CREDIT, disclaimer: DISCLAIMER };
  const needle = q.trim().toLowerCase();
  if (needle === FUND_QUERY_UNAVAILABLE) return { ...base, data_status: "unavailable", results: [] };
  if (needle === FUND_QUERY_RATE_LIMITED) return { ...base, data_status: "rate_limited", results: [] };
  const hits = FUNDS.filter((f) => `${f.name} ${f.id} ${f.corp}`.toLowerCase().includes(needle));
  return { ...base, data_status: hits.length === 0 ? "no_data" : "ok", results: hits.map(item) };
}

const ret = (horizon: FundReturn["horizon"], months: number, r: Partial<FundReturn> = {}): FundReturn => ({ horizon, months, ...r });

export function mockFund(id: string): FundDetail {
  const f = FUNDS.find((x) => x.id === id);
  if (!f) throw new ApiError(404, "Unknown fund");
  const series = (n: number) => Array.from({ length: n }, (_, i) => {
    const m = 9 - i; const y = 2026;
    return { period: `${y}-${String(m).padStart(2, "0")}`, monthly_return_pct: +(0.4 + (i % 3) * 0.3).toFixed(2), total_assets: 1200 - i * 3, management_fee_pct: 0.55 };
  }).reverse();
  const base = {
    fund_id: f.id, symbol: `GEMEL-${f.id}`, name: f.name, classification: f.classification, managing_corporation: f.corp,
    total_assets: 1200, management_fee_pct: 0.55, price_available: false,
    value_note: "The dataset has monthly returns only. Enter the value from your fund statement as the holding's manual value.",
    source: SOURCE, credit: CREDIT, disclaimer: DISCLAIMER,
  };
  if (id === "1001") {
    // Complete: all horizons, category average on 1 month (enough peers), fresh data.
    return {
      ...base, latest_period: "2026-09", data_as_of: "2026-10-02T00:00:00Z", data_stale: false, category_peer_count: 24, monthly_series: series(9),
      returns: [
        ret("1m", 1, { return_pct: 1.2, annualised_pct: null, category_avg_pct: 0.8, vs_category_pts: 0.4 }),
        ret("3m", 3, { return_pct: 3.4, annualised_pct: null }),
        ret("1y", 12, { return_pct: 9.1, annualised_pct: 9.1 }),
        ret("3y", 36, { return_pct: 28.4, annualised_pct: 8.7 }),
      ],
    };
  }
  if (id === "1002") {
    // Stale dataset, a gap in the 1-year series, too few months for 3 years, no category average (few peers).
    return {
      ...base, latest_period: "2026-06", data_as_of: "2026-07-15T00:00:00Z", data_stale: true, category_peer_count: 3, monthly_series: series(5),
      returns: [
        ret("1m", 1, { return_pct: -0.6, category_avg_pct: null, vs_category_pts: null }),
        ret("3m", 3, { return_pct: 1.9 }),
        ret("1y", 12, { missing_reason: "gap_in_series" }),
        ret("3y", 36, { missing_reason: "not_enough_months" }),
      ],
    };
  }
  // Young fund: only one month of data.
  return {
    ...base, latest_period: "2026-09", data_as_of: "2026-10-02T00:00:00Z", data_stale: false, category_peer_count: 0, monthly_series: series(1),
    total_assets: null, management_fee_pct: null,
    returns: [ret("1m", 1, { return_pct: 0.3 }), ret("3m", 3, { missing_reason: "not_enough_months" }), ret("1y", 12, { missing_reason: "not_enough_months" }), ret("3y", 36, { missing_reason: "no_value" })],
  };
}

export type DividendsState = "full" | "no_data" | "unavailable";
let divState: DividendsState = "full";
/** Tests pick which state the next GET /portfolios/{id}/dividends returns. */
export const setMockDividendsState = (s: DividendsState): void => { divState = s; };

const up = (symbol: string, name: string, ex: string, pay: string | null, per: number, cur: string, qty: number, ils: number | null, est: boolean): UpcomingDividend => ({
  symbol, name_en: name, ex_date: ex, pay_date: pay, amount_per_share: per, currency: cur, quantity: qty, expected_amount: +(per * qty).toFixed(2),
  expected_amount_ils: ils, amount_is_estimate: est, source: "Yahoo Finance",
});
const st = (symbol: string, data_status: SymbolDividendStatus["data_status"], r: Partial<SymbolDividendStatus> = {}): SymbolDividendStatus => ({ symbol, data_status, payments_in_period: 0, ...r });

export function mockDividends(pid: number): Dividends {
  const base = { portfolio_id: pid, as_of: AS_OF, window_days: 90, source: "Yahoo Finance", credit: "Data: Yahoo Finance, personal non-commercial use.", disclaimer: DISCLAIMER };
  const note = "An estimate: the last 12 months of payments repeated. Dividends can change or stop.";
  if (divState === "no_data" || pid === 2) {
    return {
      ...base, upcoming: [],
      symbols: [st("ETH-USD", "not_applicable"), st("SPY", "no_data"), st("GEMEL-1001", "not_applicable")],
      income_estimate: { is_estimate: true, basis: "trailing_payments_repeated", months: 12, total_ils: null, lines: [], symbols_without_data: ["SPY"], note },
    };
  }
  if (divState === "unavailable") {
    return {
      ...base, upcoming: [],
      symbols: [st("AAPL", "unavailable"), st("NVDA", "rate_limited"), st("TEVA.TA", "not_checked")],
      income_estimate: { is_estimate: true, basis: "trailing_payments_repeated", months: 12, total_ils: null, lines: [], symbols_without_data: ["AAPL", "NVDA", "TEVA.TA"], note },
    };
  }
  return {
    ...base,
    upcoming: [
      up("AAPL", "Apple", "2026-10-10", "2026-10-16", 0.26, "USD", 20, 19.24, false),
      up("NVDA", "NVIDIA", "2026-10-22", null, 0.01, "USD", 30, 1.11, true),
      up("LUMI.TA", "Bank Leumi", "2026-11-04", "2026-11-12", 0.9, "ILS", 900, 810, true),
    ],
    symbols: [
      st("AAPL", "ok", { last_ex_date: "2026-08-11", last_amount_per_share: 0.25, currency: "USD", payments_in_period: 4, per_share_in_period: 1 }),
      st("NVDA", "ok", { last_ex_date: "2026-06-11", last_amount_per_share: 0.01, currency: "USD", payments_in_period: 4, per_share_in_period: 0.04 }),
      st("LUMI.TA", "ok", { last_ex_date: "2026-05-20", last_amount_per_share: 0.85, currency: "ILS", payments_in_period: 4, per_share_in_period: 3.4 }),
      st("TEVA.TA", "no_data"),
      st("NICE.TA", "stopped", { last_ex_date: "2023-03-01", last_amount_per_share: 1.1, currency: "ILS" }),
      st("BTC-USD", "not_applicable"),
      st("CHKP", "unavailable"),
      st("ESLT.TA", "rate_limited"),
      st("ARNA.TA", "not_checked"),
    ],
    income_estimate: {
      is_estimate: true, basis: "trailing_payments_repeated", months: 12, total_ils: 3290.5, note,
      lines: [
        { symbol: "AAPL", quantity: 20, per_share: 1, currency: "USD", amount: 20, amount_ils: 74, payments_counted: 4 },
        { symbol: "LUMI.TA", quantity: 900, per_share: 3.4, currency: "ILS", amount: 3060, amount_ils: 3060, payments_counted: 4 },
        { symbol: "NVDA", quantity: 30, per_share: 0.04, currency: "USD", amount: 1.2, amount_ils: 4.44, payments_counted: 4 },
      ],
      symbols_without_data: ["TEVA.TA", "CHKP"],
    },
  };
}
