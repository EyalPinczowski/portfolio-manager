/**
 * Layout dispatch for on-device OCR text, and the merge of several screenshots of one list.
 * A layout is detected from the text itself; anything unknown goes to the generic table parser.
 */
import { OCR_LAYOUTS, type LayoutId } from "../config";
import type { ImportRow } from "../api";
import { namesMatch } from "./hebrew";
import { detectMeitav, parseMeitavText } from "./meitav";
import { parseOcrText } from "./parse";
import type { ParsedRows, RowMeta } from "./types";

export type LayoutChoice = LayoutId | "auto";

export const headerFractionFor = (choice: LayoutChoice): number => OCR_LAYOUTS[choice === "auto" ? "generic" : choice].headerFraction;

export function detectLayout(text: string): LayoutId {
  return detectMeitav(text) ? "meitav_trade" : "generic";
}

/** OCR text of one screenshot to rows plus the client-only facts about each row. */
export function parseScreenshotText(text: string, choice: LayoutChoice = "auto"): ParsedRows {
  const layout = choice === "auto" ? detectLayout(text) : choice;
  if (layout === "meitav_trade") {
    const parsed = parseMeitavText(text);
    if (parsed && parsed.rows.length > 0) return parsed;
  }
  const rows = parseOcrText(text);
  return { layout: "generic", rows, meta: rows.map(() => ({})) };
}

const close = (a: number | null, b: number | null): boolean =>
  a === null || b === null ? a === b : Math.abs(a - b) <= 1e-9 + 1e-6 * Math.max(Math.abs(a), Math.abs(b));
const complete = (r: ImportRow): boolean => r.value !== null && r.value !== undefined && r.price !== null && r.price !== undefined;

function same(a: ImportRow, b: ImportRow): boolean {
  if (a.symbol && b.symbol) return a.symbol === b.symbol;
  if (a.tase_number && b.tase_number) return a.tase_number === b.tase_number;
  if (a.symbol || b.symbol || a.tase_number || b.tase_number) return false;
  return namesMatch(a.name, b.name);
}

/**
 * Combine the rows of several screenshots of one scrolling list. A card that is in two screenshots (the last of
 * one is the first of the next) is merged, NOT summed:
 *  - identical price and value: one row, `duplicate_removed`;
 *  - one copy is cut off (no price or value): the complete copy wins, `duplicate_removed`;
 *  - both complete but different: one row (the later screenshot, fresher), flagged `conflict` with the other numbers.
 */
export function mergeScreenshots(parts: ParsedRows[]): ParsedRows {
  const rows: ImportRow[] = [];
  const meta: RowMeta[] = [];
  for (const part of parts) {
    part.rows.forEach((r, i) => {
      const m = part.meta[i] ?? {};
      const at = parts.length > 1 ? rows.findIndex((x) => same(x, r)) : -1;
      if (at < 0) { rows.push({ ...r }); meta.push({ ...m }); return; }
      const old = rows[at];
      const oldMeta = meta[at];
      if (complete(old) && complete(r) && !(close(old.price ?? null, r.price ?? null) && close(old.value ?? null, r.value ?? null))) {
        rows[at] = { ...r, name: r.name || old.name };
        meta[at] = { ...m, conflict: { price: old.price ?? null, value: old.value ?? null } };
      } else if (!complete(old) && complete(r)) {
        rows[at] = { ...r, name: r.name || old.name };
        meta[at] = { ...m, duplicate_removed: true };
      } else {
        rows[at] = { ...old, name: old.name || r.name };
        meta[at] = { ...oldMeta, duplicate_removed: true };
      }
    });
  }
  rows.forEach((r, i) => { r.index = i; });
  return { layout: parts.find((p) => p.layout !== "generic")?.layout ?? "generic", rows, meta };
}
