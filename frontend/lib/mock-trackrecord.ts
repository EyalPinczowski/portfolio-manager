/* Mock track record and X-ray rules. Schema-valid: tests/mock-contract.test.ts checks them with Ajv. */
import type { Explanation, TrackRecord, XrayRuleName, XrayRuleOut, XrayRuleResult, XrayRulesPatch } from "./api";
import { ApiError } from "./errors";

export type TrackState = TrackRecord["state"];
let trackState: TrackState = "ready";
/** Tests and the mock server pick which state the next GET /track-record returns. */
export const setMockTrackState = (s: TrackState): void => { trackState = s; };

const AS_OF = "2026-10-03T08:55:00Z";
const METHODOLOGY = [
  "Only calls made by the app itself (global calls) are shown. Nothing here comes from any member's portfolio.",
  "A call appears only after its horizon has ended: 1w = 7 days, 1m = 30 days, 3m = 91 days.",
  "Calls are recorded when they are made and are never edited afterwards; the outcome is added once.",
  "Return is the asset's own move from the price at the call to the price at the outcome. The benchmark return is the index move over the same span.",
  "Excess return is measured in the direction the call was made, in percentage points. A call is a hit against a benchmark when its excess return is above zero.",
  "Calls that ended in an operational error, and calls still waiting for a resolution, are counted separately and never dropped silently.",
  "Prices come from free data sources and can be delayed. This is a record of past calls, not financial advice and not a promise of future results.",
];

export function mockTrackRecord(): TrackRecord {
  const base = { as_of: AS_OF, methodology: METHODOLOGY, truncated: false };
  if (trackState === "not_started") {
    return {
      ...base, state: "not_started", count: 0, resolved_at_1m: 0, awaiting_resolution: 0, excluded_errors: 0, benchmarks: [], rows: [],
      message: "Paper trading has not started yet: no calls have been recorded, so there is no track record to show. This page fills in as calls are made and their horizons end.",
      gate: { open: false, reasons: ["Paper trading has run for 0 of 4 weeks."], weeks_running: 0, weeks_required: 4, resolved_at_1m: 0, resolved_required: 50, critical_errors: 0, recorded: 0 },
    };
  }
  if (trackState === "none_ended") {
    return {
      ...base, state: "none_ended", count: 0, resolved_at_1m: 0, awaiting_resolution: 0, excluded_errors: 0, benchmarks: [], rows: [],
      message: "6 call(s) recorded, but none has reached the end of its horizon with a result yet. Nothing is shown until a horizon has ended.",
      gate: { open: false, reasons: ["Paper trading has run for 1.5 of 4 weeks.", "0 of 50 calls resolved at 1 month."], weeks_running: 1.5, weeks_required: 4, resolved_at_1m: 0, resolved_required: 50, critical_errors: 0, recorded: 6 },
    };
  }
  return {
    ...base, state: "ready", count: 3, resolved_at_1m: 2, awaiting_resolution: 2, excluded_errors: 1,
    message: "3 call(s) whose horizon has ended.",
    benchmarks: [
      { name: "S&P 500", count: 3, hit_rate_pct: 66.7, avg_excess_pct: 1.4 },
      { name: "TA-125", count: 2, hit_rate_pct: 50, avg_excess_pct: -0.8 },
    ],
    gate: { open: false, reasons: ["Paper trading has run for 5.2 of 4 weeks.", "2 of 50 calls resolved at 1 month.", "Not yet beating the benchmarks over enough resolved calls."], weeks_running: 5.2, weeks_required: 4, resolved_at_1m: 2, resolved_required: 50, critical_errors: 0, recorded: 8 },
    rows: [
      { symbol: "AAPL", made_on: "2026-08-20", horizon: "1m", horizon_ended_on: "2026-09-19", resolved_on: "2026-09-19", outcome: "reached_goal", return_pct: 6.2, active_weights: true,
        benchmarks: [{ name: "S&P 500", return_pct: 2.1, excess_pct: 4.1 }, { name: "TA-125", return_pct: 1.2, excess_pct: 5 }] },
      { symbol: "TEVA.TA", made_on: "2026-08-10", horizon: "1m", horizon_ended_on: "2026-09-09", resolved_on: "2026-09-10", outcome: "reached_limit", return_pct: -3.5, active_weights: true,
        benchmarks: [{ name: "S&P 500", return_pct: 1.5, excess_pct: -5 }, { name: "TA-125", return_pct: 0.4, excess_pct: -3.9 }] },
      { symbol: "NVDA", made_on: "2026-07-01", horizon: "3m", horizon_ended_on: "2026-09-30", resolved_on: "2026-10-01", outcome: "horizon_ended", return_pct: 4, active_weights: false,
        benchmarks: [{ name: "S&P 500", return_pct: 3.8, excess_pct: 0.2 }] },
    ],
  };
}

// ---------- X-ray rules ----------
const LABEL: Record<XrayRuleName, string> = { concentration: "Largest position", currency: "Currency exposure", country_home: "Home-country exposure", sector: "Sector exposure" };
const DEFAULTS: Record<XrayRuleName, { thr: number; src: XrayRuleOut["threshold_source"]; min: number; max: number }> = {
  concentration: { thr: 12, src: "risk_filter", min: 1, max: 50 },
  currency: { thr: 70, src: "config_default", min: 10, max: 100 },
  country_home: { thr: 60, src: "config_default", min: 10, max: 100 },
  sector: { thr: 30, src: "risk_filter", min: 5, max: 100 },
};
const ORDER: XrayRuleName[] = ["concentration", "currency", "country_home", "sector"];
const cfg: Record<string, { enabled: boolean; override: number | null }> = {};
const get = (r: XrayRuleName) => (cfg[r] ??= { enabled: true, override: null });
export const resetMockXrayRules = (): void => { for (const k of Object.keys(cfg)) delete cfg[k]; };

export function mockXrayRulesOut(): { rules: XrayRuleOut[] } {
  return {
    rules: ORDER.map((rule) => {
      const d = DEFAULTS[rule], c = get(rule);
      return { rule, enabled: c.enabled, threshold_pct: c.override ?? d.thr, threshold_source: c.override !== null ? "override" : d.src, default_threshold_pct: d.thr, override_pct: c.override, min_pct: d.min, max_pct: d.max };
    }),
  };
}

export function mockPatchXrayRules(body: XrayRulesPatch): { rules: XrayRuleOut[] } {
  for (const ch of body.rules) {
    const d = DEFAULTS[ch.rule];
    if (typeof ch.threshold_pct === "number" && (ch.threshold_pct < d.min || ch.threshold_pct > d.max)) {
      throw new ApiError(422, `The ${ch.rule} threshold must be between ${d.min}% and ${d.max}%.`, undefined,
        { detail: `The ${ch.rule} threshold must be between ${d.min}% and ${d.max}%.`, code: "threshold_out_of_bounds", rule: ch.rule, min_pct: d.min, max_pct: d.max });
    }
  }
  for (const ch of body.rules) {
    const c = get(ch.rule);
    if (typeof ch.enabled === "boolean") c.enabled = ch.enabled;
    if (ch.threshold_pct !== undefined) c.override = ch.threshold_pct;
  }
  return mockXrayRulesOut();
}

/** Rule results for the X-ray, computed from the same exposures the mock X-ray shows. */
export function mockXrayRuleResults(m: { positions: { name: string; pct: number }[]; currency: { name: string; pct: number }[]; israel: number; sectors: { name: string; pct: number }[] }): XrayRuleResult[] {
  const out = mockXrayRulesOut().rules;
  return out.map((o) => {
    const pool = o.rule === "concentration" ? m.positions : o.rule === "currency" ? m.currency : o.rule === "sector" ? m.sectors : [{ name: "Israel", pct: m.israel }];
    const items = o.enabled ? pool.filter((i) => i.pct > o.threshold_pct).map((i) => ({ name: i.name, value_pct: Math.round(i.pct * 100) / 100 })) : [];
    const top = [...pool].sort((a, b) => b.pct - a.pct)[0];
    const state: XrayRuleResult["state"] = !o.enabled ? "off" : items.length ? "breach" : "ok";
    const src = o.threshold_source === "override" ? "your override" : o.threshold_source === "risk_filter" ? "your risk filter" : "the default";
    const summary = state === "off" ? `${LABEL[o.rule]} is switched off for this portfolio, so it is not checked.`
      : state === "breach" ? `${LABEL[o.rule]}: ${items.map((i) => i.name).join(", ")} is above the ${o.threshold_pct}% threshold from ${src}.`
        : `${LABEL[o.rule]}: the largest is ${top?.name ?? "n/a"} at ${(top?.pct ?? 0).toFixed(1)}%, within the ${o.threshold_pct}% threshold from ${src}.`;
    const explanation: Explanation = {
      version: 1, summary, inputs: { threshold_pct: o.threshold_pct, largest_pct: Math.round((top?.pct ?? 0) * 10) / 10 },
      rules_applied: [`Threshold ${o.threshold_pct}% from ${src}`], as_of: AS_OF,
      risk_rules_applied: [], invalidation_risks: ["Based on the last known prices and the sector and country labels of each holding; a stale price or a wrong label changes the result."],
      sources: [{ name: "Portfolio holdings", detail: "Latest imported holdings and quotes", as_of: AS_OF }],
    };
    return { rule: o.rule, label: LABEL[o.rule], state, enabled: o.enabled, threshold_pct: o.threshold_pct, threshold_source: o.threshold_source, value_pct: top ? Math.round(top.pct * 100) / 100 : null, items, explanation };
  });
}
