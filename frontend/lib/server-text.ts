/* Maps stable server codes and known fixed server sentences to message keys, so the UI can show them in the
 * user's language. Anything unknown stays as server text (the caller wraps it in <bdi dir="auto">). */
import type { Horizon } from "./api";

const HORIZON_BY_LABEL: Record<string, Horizon> = { "1 week": "1w", "1 month": "1m", "3 months": "3m", "6 months": "6m", "1 year+": "1y" };

/** "3 months" -> "3m" (the backend's HorizonSpec.label); null when unknown. */
export function horizonFromLabel(label: string | null | undefined): Horizon | null {
  return label ? HORIZON_BY_LABEL[label.trim().toLowerCase()] ?? null : null;
}

const SOURCES = new Set([
  "atr", "structure", "moving_average", "max_loss", "trailing", "cost", "saved_stop", "resistance", "long_term_resistance",
  "analyst", "bollinger", "fibonacci", "r_multiple", "trailing_stop", "weekly_swing_low", "multi_month_support",
]);

/** Message key under exit.sources for a stop/target source code, or null. sma_200 -> {key:"sma", n:"200"}. */
export function sourceKey(source: string): { key: string; n?: string } | null {
  const m = /^sma_(\d+)$/.exec(source);
  if (m) return { key: "sma", n: m[1] };
  return SOURCES.has(source) ? { key: source } : null;
}

const PLAN_NOTE_RE = /^(a plan|this is a suggestion|a suggestion) to review and change, not an instruction/i;
/** True for the fixed "plan to review, not an instruction" sentence (the UI already shows its own translation). */
export const isPlanNote = (s: string | null | undefined): boolean => !!s && PLAN_NOTE_RE.test(s.trim());

const FIT_INCOMPLETE_RE = /^fit cannot be checked until the missing inputs are given/i;
export const isFitIncompleteSummary = (s: string | null | undefined): boolean => !!s && FIT_INCOMPLETE_RE.test(s.trim());

export type ReasonMatch = { key: string; values: Record<string, string | number> };
/** Known stop-reason sentence shapes -> message key + values. Unknown text returns null. */
export function matchStopReason(text: string): ReasonMatch | null {
  const atr = /^(?:Stop at )?(\d+(?:\.\d+)?) x ATR\((\d+)(?:, [^)]*)?\) below the price(?: for a [^.]*? holding period| \((-?\d+(?:\.\d+)?)%\))?\.?$/i.exec(text.trim());
  if (atr) return { key: "atr", values: { mult: atr[1], period: atr[2] } };
  if (/^Follows the highest high; it only moves up\.?$/i.test(text.trim())) return { key: "trailing", values: {} };
  return null;
}

/** A stable machine code plus numbers, sent by the backend beside its English sentence (`TextCode`). */
export type TextCodeIn = { code: string; params?: Record<string, string | number> | null };

/** Codes with a message under `serverText`. A code the UI does not know falls back to the English text. */
export const SERVER_TEXT_CODES = [
  "stop_atr", "stop_beyond_atr", "stop_chart_no_atr", "stop_max_loss_only", "stop_saved", "trailing_moved", "trailing_start",
  "breakeven", "take_profit", "size_fits", "size_reduce", "size_rule_max_loss", "size_rule_portfolio_risk",
  "exposure_now", "exposure_breaks", "exposure_fits", "max_size_binding", "max_size_no_limit", "max_size_empty_book",
] as const;
const CODE_SET = new Set<string>(SERVER_TEXT_CODES);
export const isKnownTextCode = (tc: TextCodeIn | null | undefined): tc is TextCodeIn => !!tc && CODE_SET.has(tc.code);
