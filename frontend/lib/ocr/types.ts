import type { ImportRow } from "../api";
import type { LayoutId } from "../config";

/**
 * Facts about a parsed row that the server's `ImportRowModel.flags` enum cannot carry today (it only allows
 * missing_fields, value_mismatch, unmatched, low_confidence_match, currency_changed, unit_mismatch). They stay on
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
