/**
 * Grouping and totals for the "update from screenshots" review. Pure functions (no I/O), so they are easy to test.
 * The server draft does not say whether a row is a new holding, so the caller passes the symbols already held.
 */
import { RECONCILE_WARN_PCT } from "./config";
import type { Holding, ImportRow, ImportScope, ProposedChange } from "./api";

export type RowGroup = "new" | "changed" | "unchanged";

const isChange = (c: ProposedChange | undefined): c is ProposedChange => !!c && c.type !== "keep";

/**
 * New holdings, rows whose quantity changed, and everything else (price-only or identical: the draft carries no
 * baseline, so those two cannot be told apart here). `held` null = holdings not loaded yet.
 */
export function groupRows(rows: ImportRow[], changes: ProposedChange[], held: ReadonlySet<string> | null): Record<RowGroup, ImportRow[]> {
  const out: Record<RowGroup, ImportRow[]> = { new: [], changed: [], unchanged: [] };
  for (const r of rows) {
    const ch = changes.find((c) => c.row_index === r.index);
    const isHeld = held !== null && typeof r.symbol === "string" && held.has(r.symbol);
    if (held !== null && !isHeld) out.new.push(r);
    else if (isChange(ch)) out.changed.push(r);
    else out.unchanged.push(r);
  }
  return out;
}

/** Holdings that are not in the screenshots (the server's `row_index -1` entries): only a full update has them. */
export const notInScreenshots = (changes: ProposedChange[]): ProposedChange[] => changes.filter((c) => c.row_index < 0);

/** Value of a screenshot row in ILS (agorot prices are divided by 100); null when it cannot be worked out. */
export function rowValueIls(r: ImportRow, fx: number | null): number | null {
  let v = typeof r.value === "number" ? r.value : null;
  if (v === null && typeof r.quantity === "number" && typeof r.price === "number") v = (r.quantity * r.price) / (r.unit === "agorot" ? 100 : 1);
  if (v === null) return null;
  if (r.currency === "USD") return fx === null ? null : v * fx;
  return v;
}

export interface Totals { before: number; after: number | null }

/**
 * Portfolio value (ILS) before and after the update. After = before, minus what the screenshot rows replace,
 * plus the screenshot values, minus holdings the user says were sold or withdrawn (kept ones stay). Null when a
 * value cannot be worked out (never a made-up number).
 */
export function updateTotals(
  rows: ImportRow[], changes: ProposedChange[], holdings: Holding[], scope: ImportScope, fx: number | null,
): Totals {
  const before = holdings.reduce((a, h) => a + h.value_ils, 0);
  let after = before;
  const heldBySymbol = new Map(holdings.map((h) => [h.symbol, h]));
  for (const r of rows) {
    const v = rowValueIls(r, fx);
    if (v === null) return { before, after: null };
    const h = r.symbol ? heldBySymbol.get(r.symbol) : undefined;
    after += v - (h?.value_ils ?? 0);
  }
  if (scope === "full") {
    const inRows = new Set(rows.map((r) => r.symbol));
    for (const h of holdings) {
      if (inRows.has(h.symbol)) continue;
      const ch = changes.find((c) => c.row_index < 0 && c.symbol === h.symbol);
      if (ch && ch.type !== "keep") after -= h.value_ils;
    }
  }
  return { before, after };
}

export interface Reconcile { rows: number; broker: number; gapPct: number; warn: boolean }

/**
 * The rows' total (ILS) against the broker's own portfolio total read from the screen header. `gapPct` is the
 * share of the broker total (0.02 = 2 %), signed (rows minus broker); a gap over RECONCILE_WARN_PCT warns. Null when
 * the broker total is unknown or a row cannot be converted (never a made-up number). It never blocks confirming.
 */
export function reconcileTotals(rows: ImportRow[], brokerTotal: number | null | undefined, fx: number | null): Reconcile | null {
  if (typeof brokerTotal !== "number" || !(brokerTotal > 0) || rows.length === 0) return null;
  let sum = 0;
  for (const r of rows) {
    const v = rowValueIls(r, fx);
    if (v === null) return null;
    sum += v;
  }
  const gapPct = (sum - brokerTotal) / brokerTotal;
  return { rows: sum, broker: brokerTotal, gapPct, warn: Math.abs(gapPct) > RECONCILE_WARN_PCT };
}
