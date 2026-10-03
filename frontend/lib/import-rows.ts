/**
 * Makes import rows consistent with what the server accepts (backend ImportRowModel), so the on-device path
 * never gets a 422 for something the client could have fixed. Mirrors the server bounds:
 * index 0-10000; quantity 0-1e12; price/value/cost 0-1e15; currency derived from unit (agorot and ILS mean
 * ILS, USD means USD); symbol ^[A-Z0-9.^=\-]{1,20}$ (empty -> null); tase_number ^\d{5,9}$; name max 200 with
 * runs of 6+ digits masked; candidates max 20 with score 0-100; flags max 10.
 */
import type { ImportRow, MatchCandidate } from "./api";
import { ApiError, type ValidationDetail } from "./errors";

export const MAX_QTY = 1e12;
export const MAX_MONEY = 1e15;
const SYMBOL_RE = /^[A-Z0-9.^=-]{1,20}$/;
const TASE_RE = /^\d{5,9}$/;

type Unit = ImportRow["unit"];
export const currencyForUnit = (unit: Unit): ImportRow["currency"] => (unit === "USD" ? "USD" : "ILS");

/** Non-finite or negative -> null (never invent a 0); above the bound -> the bound. */
function bounded(v: number | null | undefined, max: number): number | null {
  if (v === null || v === undefined || typeof v !== "number" || !Number.isFinite(v) || v < 0) return null;
  return Math.min(v, max);
}

export function sanitizeRow(r: ImportRow): ImportRow {
  const unit: Unit = r.unit === "agorot" || r.unit === "USD" ? r.unit : "ILS";
  const symbol = typeof r.symbol === "string" ? r.symbol.trim().toUpperCase() : "";
  const tase = typeof r.tase_number === "string" ? r.tase_number.trim() : "";
  const index = Number.isFinite(r.index) ? Math.min(10000, Math.max(0, Math.trunc(r.index))) : 0;
  const candidates: MatchCandidate[] = (r.candidates ?? []).slice(0, 20).map((c) => ({
    ...c, score: Number.isFinite(c.score) ? Math.min(100, Math.max(0, c.score)) : 0,
  }));
  return {
    ...r,
    index,
    name: String(r.name ?? "").replace(/\d{6,}/g, "***").slice(0, 200),
    symbol: SYMBOL_RE.test(symbol) ? symbol : null,
    tase_number: TASE_RE.test(tase) ? tase : null,
    quantity: bounded(r.quantity, MAX_QTY),
    price: bounded(r.price, MAX_MONEY),
    value: bounded(r.value, MAX_MONEY),
    cost: bounded(r.cost, MAX_MONEY),
    unit,
    currency: currencyForUnit(unit),
    flags: (r.flags ?? []).slice(0, 10),
    candidates,
  };
}

export const sanitizeRows = (rows: ImportRow[]): ImportRow[] => rows.map(sanitizeRow);

export interface RowProblem { position: number; field: string | null; msg: string }

/** Maps 422 `loc` paths (["body","rows",3,"quantity"]) to the row position and field. */
export function rowProblems(err: unknown): RowProblem[] {
  if (!(err instanceof ApiError)) return [];
  const out: RowProblem[] = [];
  for (const d of err.validation as ValidationDetail[]) {
    const i = d.loc.indexOf("rows");
    const pos = i >= 0 ? d.loc[i + 1] : undefined;
    if (typeof pos !== "number") continue;
    const f = d.loc[i + 2];
    out.push({ position: pos, field: typeof f === "string" ? f : null, msg: d.msg });
  }
  return out;
}
