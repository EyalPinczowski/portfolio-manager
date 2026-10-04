/* Mock post-mortem (descriptive only). Schema-valid: tests/mock-contract.test.ts checks it with Ajv. */
import type { Explanation, GapBreakdown, GapItem, Postmortem, PostmortemFinding } from "./api";

export interface PmPortfolio {
  id: number;
  tracking_started_at?: string | null;
  expected_return_pct?: number | null;
  expected_return_horizon_months?: number | null;
}

const AS_OF = "2026-10-03T08:55:00Z";
const ex = (summary: string, inputs: Explanation["inputs"] = {}): Explanation => ({
  version: 1,
  summary,
  inputs,
  rules_applied: ["Time-weighted return, deposits and withdrawals excluded"],
  as_of: AS_OF,
  sources: [{ name: "Portfolio snapshots", detail: "Daily value, cash and flows", as_of: AS_OF }],
});

const finding = (f: PostmortemFinding): PostmortemFinding => f;

function findings(): PostmortemFinding[] {
  return [
    finding({
      kind: "performance", status: "ok", headline: "The portfolio returned +1.90% (+₪1,900) over the period.",
      amount_ils: 1900, amount_pct: 1.9, gap_contribution_pp: null,
      evidence: [{ label: "Time-weighted return", value: 1.9, unit: "%" }, { label: "Profit", value: 1900, unit: "ILS" }],
      explanation: ex("Return computed from daily snapshots, deposits and withdrawals excluded."),
    }),
    finding({
      kind: "contribution", status: "ok", headline: "NICE detracted ₪1,100 (-1.10 pp); ESLT added ₪800 (+0.80 pp).",
      amount_ils: -300, amount_pct: -0.3, gap_contribution_pp: -0.3,
      evidence: [
        { label: "NICE.TA", value: -1100, unit: "ILS", note: "unrealized" },
        { label: "ESLT.TA", value: 800, unit: "ILS", note: "unrealized" },
        { label: "Share of capital held in NICE", value: 11.2, unit: "%" },
      ],
      explanation: ex("Each holding's profit or loss divided by the capital at the start of the period.", { "NICE.TA": -1100, "ESLT.TA": 800 }),
    }),
    finding({
      kind: "timing", status: "ok", headline: "Two purchases were made after a rise of more than 8% in the prior month.",
      amount_ils: -250, amount_pct: -0.25, gap_contribution_pp: -0.25,
      evidence: [{ label: "Purchases after a rise", value: 2 }, { label: "Cost versus the month-start price", value: -250, unit: "ILS" }],
      explanation: ex("Entry prices compared with the price one month earlier."),
    }),
    finding({
      kind: "concentration", status: "ok", headline: "Technology was 46% of the portfolio; the largest position was 18%.",
      amount_ils: null, amount_pct: null, gap_contribution_pp: -0.2,
      evidence: [{ label: "Technology weight", value: 46, unit: "%" }, { label: "Largest position", value: "NVDA", note: "18%" }],
      explanation: ex("Weights at the end of the period, from the latest holdings."),
    }),
    finding({
      kind: "fx", status: "ok", headline: "The dollar moved -2.1% against the shekel; US holdings lost ₪300 to currency.",
      amount_ils: -300, amount_pct: -0.3, gap_contribution_pp: -0.3,
      evidence: [{ label: "USD/ILS change", value: -2.1, unit: "%" }, { label: "Currency effect", value: -300, unit: "ILS" }],
      explanation: ex("Return in local currency compared with the return in shekels."),
    }),
    finding({
      kind: "flows", status: "ok", headline: "One deposit of ₪10,000 was recorded; it is not counted as profit.",
      amount_ils: 10000, amount_pct: null, gap_contribution_pp: null,
      evidence: [{ label: "Deposits", value: 10000, unit: "ILS" }, { label: "Withdrawals", value: 0, unit: "ILS" }],
      explanation: ex("Flows recorded at import review."),
    }),
    finding({
      kind: "cash", status: "not_recorded", headline: "Cash balance history", amount_ils: null, amount_pct: null, gap_contribution_pp: null,
      evidence: [], explanation: ex("Daily cash balances were not captured in the screenshots imported so far."),
    }),
    finding({
      kind: "costs", status: "not_recorded", headline: "Fees and commissions", amount_ils: null, amount_pct: null, gap_contribution_pp: null,
      evidence: [], explanation: ex("Broker screenshots do not include fees, so none are recorded."),
    }),
    finding({
      kind: "stops", status: "not_recorded", headline: "Stop levels", amount_ils: null, amount_pct: null, gap_contribution_pp: null,
      evidence: [], explanation: ex("No stop levels were saved during the period."),
    }),
  ];
}

const item = (key: string, label: string, symbol: string | null, pp: number, amount_ils: number): GapItem => ({ key, label, symbol, pp, amount_ils });

export function mockPostmortem(pf: PmPortfolio, start: string | null, end: string | null): Postmortem {
  const s = start ?? pf.tracking_started_at ?? "2026-07-06";
  const e = end ?? "2026-10-03";
  const days = Math.max(1, Math.round((Date.parse(e) - Date.parse(s)) / 86_400_000));
  const hasExp = pf.expected_return_pct != null && pf.expected_return_horizon_months != null;
  const base = { annualised_is_extrapolated: false, start: s, end: e, days, benchmarks: [], holdings: [], top_contributors: [], top_detractors: [], findings: [], excluded: [], flags: [], summary: "", disclaimer: "Not financial advice." };
  const expectation: Postmortem["expectation"] = hasExp
    ? {
        status: "ok", expected_return_pct: pf.expected_return_pct, horizon_months: pf.expected_return_horizon_months,
        expected_for_period_pct: 2.8, gap_pp: -0.9,
      }
    : { status: "needs_expectation", expected_return_pct: null, horizon_months: null, expected_for_period_pct: null, gap_pp: null };
  if (pf.id === 2 || days < 14) {
    return { ...base, status: "not_enough_history", history: { days_available: 9, days_required: 28, days_remaining: 19 }, expectation, twr_pct: null, pnl_ils: null };
  }
  const vsExpectation: GapBreakdown = hasExp
    ? {
        reference: "expectation", status: "ok", reference_pct: 2.8, portfolio_pct: 1.9, gap_pp: -0.9,
        items: [item("nice", "NICE", "NICE.TA", -1.1, -1100), item("eslt", "ESLT", "ESLT.TA", 0.8, 800), item("fx", "Currency", null, -0.3, -300), item("timing", "Purchase timing", null, -0.25, -250)],
        attributed_pp: -0.85, residual_pp: -0.05, residual_ils: -50, reconciles: true,
        note: "The listed items add up to the gap within rounding.",
      }
    : { reference: "expectation", status: "needs_expectation", items: [], note: "No expectation is set." };
  const vsBenchmark: GapBreakdown = {
    reference: "benchmark", status: "ok", reference_pct: 4.1, portfolio_pct: 1.9, gap_pp: -2.2,
    items: [item("nice", "NICE", "NICE.TA", -1.1, -1100), item("fx", "Currency", null, -0.3, -300), item("timing", "Purchase timing", null, -0.25, -250), item("eslt", "ESLT", "ESLT.TA", 0.8, 800)],
    attributed_pp: -0.85, residual_pp: -1.35, residual_ils: -1350, reconciles: false,
    note: "About 60% of the gap is not explained by the items above.",
  };
  return {
    ...base,
    status: "ok", history: { days_available: days, days_required: 28, days_remaining: 0 },
    twr_pct: 1.9, pnl_ils: 1900, annualised_pct: 7.9, annualised_is_extrapolated: true, capital_ils: 100000,
    expectation,
    benchmarks: [
      { symbol: "^GSPC", weight_pct: 50, local_pct: 4.1, ils_pct: 3.2, flags: [] },
      { symbol: "^TA125.TA", weight_pct: 50, local_pct: 5.0, ils_pct: 5.0, flags: [] },
    ],
    gap_vs_expectation: vsExpectation,
    gap_vs_benchmark: vsBenchmark,
    holdings: [
      { symbol: "ESLT.TA", name: "Elbit Systems", currency: "ILS", realized_ils: 0, unrealized_ils: 800, local_ils: 800, fx_ils: 0, total_ils: 800, pct_of_capital: 0.8, still_held: true },
      { symbol: "NVDA", name: "NVIDIA", currency: "USD", realized_ils: 400, unrealized_ils: 700, local_ils: 1400, fx_ils: -300, total_ils: 1100, pct_of_capital: 1.1, still_held: true },
      { symbol: "AAPL", name: "Apple", currency: "USD", realized_ils: -120, unrealized_ils: 0, local_ils: -120, fx_ils: 0, total_ils: -120, pct_of_capital: -0.12, still_held: false },
      { symbol: "NICE.TA", name: "NICE Systems", currency: "ILS", realized_ils: 0, unrealized_ils: -1100, local_ils: -1100, fx_ils: 0, total_ils: -1100, pct_of_capital: -1.1, still_held: true },
    ],
    top_contributors: ["NVDA", "ESLT.TA"],
    top_detractors: ["NICE.TA", "AAPL"],
    findings: findings(),
    excluded: [{ symbol: "ZZNEW.TA", reason: "No price history in the period" }],
    flags: [],
    summary: "Over 89 days the portfolio returned +1.90%, compared with +2.80% implied by the expectation and +4.10% for the benchmark.",
  };
}
