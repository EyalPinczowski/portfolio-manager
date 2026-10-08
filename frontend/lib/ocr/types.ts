import type { ImportRow } from "../api";
import type { LayoutId } from "../config";

/**
 * Facts about a parsed row that the on-device reader noted (the server now carries the same names in
 * `ImportRowModel.flags`, which is the source of truth; this adds detail such as the P&L %). They stay on
 * the device and drive the review table. `rows[i]` and `meta[i]` always describe the same row.
 */
export interface RowMeta {
  /** Quantity could not be inferred (tiny rounded value, or price/value missing): the user must enter it. */
  quantity_uncertain?: boolean;
  /** Value / price is not a whole number (fractional share, or a wrong price was picked): worth a look. */
  quantity_fractional?: boolean;
  /** `cost` was derived from the broker's total P&L %: cost = price / (1 + pnl / 100). */
  cost_inferred?: boolean;
  /** The broker's total P&L % that the cost was derived from. */
  pnl_pct?: number | null;
  /** The same card was in more than one screenshot and was counted once. */
  duplicate_removed?: boolean;
  /** The same security was in several screenshots with different price/value: one row kept, the other's numbers here. */
  conflict?: { price: number | null; value: number | null } | null;
}

export type RowFlag = "quantity_uncertain" | "quantity_fractional" | "cost_inferred" | "duplicate_removed" | "conflict";

export interface ParsedRows {
  layout: LayoutId;
  rows: ImportRow[];
  meta: RowMeta[];
  /** The broker's own portfolio total (ILS) from the summary header, a number only; null when not on the screen. */
  broker_total?: number | null;
}

/** Portable flag names for a row (the same names the fixtures use and the server will use in block 2.0-E). */
export function flagsOf(m: RowMeta): RowFlag[] {
  const out: RowFlag[] = [];
  if (m.quantity_uncertain) out.push("quantity_uncertain");
  if (m.quantity_fractional) out.push("quantity_fractional");
  if (m.cost_inferred) out.push("cost_inferred");
  if (m.duplicate_removed) out.push("duplicate_removed");
  if (m.conflict) out.push("conflict");
  return out;
}
