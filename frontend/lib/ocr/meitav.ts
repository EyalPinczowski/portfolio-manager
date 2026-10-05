/**
 * Meitav Trade (מיטב טרייד) mobile "My portfolio" layout, see docs/import-formats.md. One card per holding:
 *   `<EXCHANGE> • <TICKER>` (or `TLV • <7-digit security number>`), a name, the position value (`$` or `₪`),
 *   the broker's total P&L %, and on the other side the price and the day change %. There is no quantity and no
 *   cost column, so quantity = value / price and cost = price / (1 + P&L % / 100).
 *
 * OCR gives these in any order (right-to-left lines in visual order, columns split), so the text is cut into
 * cards at each anchor and every figure is found anywhere inside its card's block:
 *   value = the `$`/`₪` amount; percentages = tokens with `%`; price = the remaining number that is not the
 *   security number. Nothing is guessed when it is ambiguous: no P&L % means no cost, no value means no quantity.
 */
import { INFER_MIN_PNL_PCT, INFER_MIN_VALUE, INFER_ROUND_SLACK } from "../config";
import type { ImportRow } from "../api";
import type { ParsedRows, RowMeta } from "./types";

const STRICT_BULLET = "[•·●∙▪*|]";
const LOOSE_BULLET = "[•·●∙▪*|:+–—-]";
const TICKER = "[A-Z][A-Z0-9]{0,5}(?:[.-][A-Z]{1,2})?";
const TASE_NO = "\\d{6,8}";
const ARROWS = /[↑↓▲▼⬆⬇△▽⇧⇩⬈⬊↗↘]/u;
const revStr = (s: string): string => Array.from(s).reverse().join("");
/** Section header words (letters only), as printed and as a right-to-left line may come out reversed. */
const SECTION_HEADERS = new Set(["קרןסל", "קרנותסל"].flatMap((h) => [h, revStr(h), "ןרקלס", "תונרקלס"]));

export interface Anchor { start: number; end: number; exchange: string; ticker: string; strict: boolean }

const fwdRe = () => new RegExp(`(?<![A-Za-z])(NASDAQ|NYSE|AMEX|TLV)\\s*(${LOOSE_BULLET})?\\s*(${TICKER}|${TASE_NO})(?![A-Za-z0-9])`, "g");
// Visual-order (reversed) line: `ACME • NASDAQ`. The bullet is required here to avoid false hits.
const revRe = () => new RegExp(`(?<![A-Za-z0-9])(${TICKER}|${TASE_NO})\\s*(${STRICT_BULLET})\\s*(NASDAQ|NYSE|AMEX|TLV)(?![A-Za-z])`, "g");

export function findAnchors(line: string): Anchor[] {
  const out: Anchor[] = [];
  for (const m of line.matchAll(fwdRe())) {
    const ticker = m[3];
    const tlv = m[1] === "TLV";
    if (tlv !== /^\d+$/.test(ticker)) continue; // TLV needs a number, US exchanges need a letter ticker
    out.push({ start: m.index!, end: m.index! + m[0].length, exchange: m[1], ticker, strict: new RegExp(`^${STRICT_BULLET}$`).test(m[2] ?? "") });
  }
  for (const m of line.matchAll(revRe())) {
    const ticker = m[1];
    const tlv = m[3] === "TLV";
    if (tlv !== /^\d+$/.test(ticker)) continue;
    const start = m.index!;
    if (out.some((a) => start < a.end && start + m[0].length > a.start)) continue;
    out.push({ start, end: start + m[0].length, exchange: m[3], ticker, strict: true });
  }
  return out.sort((a, b) => a.start - b.start);
}

const isSectionLine = (line: string): boolean => SECTION_HEADERS.has(line.replace(/[^\p{L}]/gu, ""));

/** True when the text looks like a Meitav Trade list: an `exchange • ticker` anchor with a bullet, or two anchors. */
export function detectMeitav(text: string): boolean {
  let any = 0;
  let strict = 0;
  let section = false;
  for (const line of text.split(/\r?\n/)) {
    if (isSectionLine(line.trim())) section = true;
    const a = findAnchors(line);
    any += a.length;
    if (a.some((x) => x.strict)) strict += 1;
  }
  return strict >= 1 || any >= 2 || (section && any >= 1);
}

interface Block { anchor: Anchor; lines: string[] }

/** OCR often prints a minus as an en/em dash or a Unicode hyphen: a dash glued to a digit or % is a minus. */
const MINUS_RE = /(?<!\d)[−–—‐‑](?=[\d%])/g;

function toBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.replace(MINUS_RE, "-").replace(/−/g, "-").trim();
    if (!line) continue;
    const anchors = findAnchors(line);
    if (anchors.length === 0) {
      const cur = blocks[blocks.length - 1];
      if (cur && !isSectionLine(line)) cur.lines.push(line);
      continue;
    }
    anchors.forEach((a, i) => {
      const from = i === 0 ? 0 : a.start;
      const to = i + 1 < anchors.length ? anchors[i + 1].start : line.length;
      const rest = `${line.slice(from, a.start)} ${line.slice(a.end, to)}`.trim();
      blocks.push({ anchor: a, lines: rest ? [rest] : [] });
    });
  }
  return blocks;
}

const NUM = "\\d[\\d,]*(?:\\.\\d+)?";
const toNum = (raw: string): number => Number(raw.replace(/,/g, "").replace(/[.,]+$/, ""));
const decimals = (raw: string): number => (/\.(\d+)$/.exec(raw.replace(/[.,]+$/, ""))?.[1].length ?? 0);
const blank = (s: string, from: number, to: number): string => s.slice(0, from) + " ".repeat(to - from) + s.slice(to);

interface Amt { value: number; raw: string; symbol: "$" | "₪"; line: number; pos: number }
interface Pct { value: number; line: number; pos: number; arrow: boolean }
interface Num { value: number; raw: string; line: number; start: number; end: number }
interface LineInfo { work: string; text: boolean; nums: Num[] }

/** `$`/`₪` amount: the number touching the symbol, preferring "symbol then number" and no gap. */
function takeAmounts(work: string, line: number): { work: string; amts: Amt[] } {
  const amts: Amt[] = [];
  for (let i = 0; i < work.length; i++) {
    const sym = work[i];
    if (sym !== "$" && sym !== "₪") continue;
    const tries: [RegExp, "after" | "before"][] = [
      [new RegExp(`^(${NUM})`), "after"],
      [new RegExp(`(?<![\\d,.])(${NUM})$`), "before"],
      [new RegExp(`^\\s{1,2}(${NUM})`), "after"],
      [new RegExp(`(?<![\\d,.])(${NUM})\\s{1,2}$`), "before"],
    ];
    for (const [re, side] of tries) {
      const base = side === "after" ? i + 1 : 0;
      const m = re.exec(side === "after" ? work.slice(i + 1) : work.slice(0, i));
      if (!m) continue;
      const raw = m[1];
      const idx = base + m.index + m[0].indexOf(raw);
      amts.push({ value: toNum(raw), raw, symbol: sym, line, pos: idx });
      work = blank(blank(work, idx, idx + raw.length), i, i + 1);
      break;
    }
  }
  return { work, amts };
}

function takePercents(work: string, original: string, line: number): { work: string; pcts: Pct[] } {
  const pcts: Pct[] = [];
  const arrowNear = (a: number, b: number) => ARROWS.test(original.slice(Math.max(0, a - 3), b + 3));
  for (const m of work.matchAll(new RegExp(`(?<![\\d.,])([-+]?)\\s?(${NUM})\\s*%`, "g"))) {
    const v = toNum(m[2]);
    pcts.push({ value: m[1] === "-" ? -v : v, line, pos: m.index!, arrow: arrowNear(m.index!, m.index! + m[0].length) });
  }
  let w = work.replace(new RegExp(`(?<![\\d.,])([-+]?)\\s?(${NUM})\\s*%`, "g"), (s) => " ".repeat(s.length));
  // Reversed (visual-order) form: "%-0.31" or "%0.31-", number glued to the percent sign.
  for (const m of w.matchAll(new RegExp(`%([-+]?)(${NUM})([-+]?)(?![\\d])`, "g"))) {
    const v = toNum(m[2]);
    const neg = m[1] === "-" || (m[1] === "" && m[3] === "-");
    pcts.push({ value: neg ? -v : v, line, pos: m.index!, arrow: arrowNear(m.index!, m.index! + m[0].length) });
  }
  w = w.replace(new RegExp(`%([-+]?)(${NUM})([-+]?)(?![\\d])`, "g"), (s) => " ".repeat(s.length));
  return { work: w, pcts };
}

function takeNumbers(work: string, line: number): Num[] {
  const out: Num[] = [];
  for (const m of work.matchAll(new RegExp(`(?<![\\p{L}\\p{N}_.,:])(${NUM})(?![\\p{L}\\p{N}_:])`, "gu"))) {
    const raw = m[1].replace(/[.,]+$/, "");
    if (!/\./.test(raw) && raw.replace(/,/g, "").length >= 7) continue; // security-number-like, never a price
    out.push({ value: toNum(raw), raw, line, start: m.index!, end: m.index! + m[1].length });
  }
  return out;
}

/** Which percentage is the broker's total P&L %? Null when it cannot be told apart from the day change. */
function pickPnl(pcts: Pct[], value: Amt | null, priceLine: number | null): number | null {
  if (pcts.length === 0) return null;
  if (value) {
    const onValue = pcts.filter((p) => p.line === value.line);
    if (onValue.length > 0) return onValue.reduce((a, b) => (Math.abs(a.pos - value.pos) <= Math.abs(b.pos - value.pos) ? a : b)).value;
  }
  const arrows = pcts.filter((p) => p.arrow);
  if (arrows.length === 1) return arrows[0].value;
  if (pcts.length === 2) {
    const zeros = pcts.filter((p) => p.value === 0);
    if (zeros.length === 1) return pcts.find((p) => p.value !== 0)!.value;
    if (value && priceLine !== null) {
      const score = (p: Pct) => Math.abs(p.line - value.line) - Math.abs(p.line - priceLine);
      const [a, b] = pcts;
      if (score(a) < score(b)) return a.value;
      if (score(b) < score(a)) return b.value;
    }
  }
  return null;
}

/** value / price as a whole number, allowing for the rounding of both figures; null when it is not whole. */
function wholeQuantity(value: Amt, price: Num, agorot: boolean): number | null {
  const unitPrice = agorot ? price.value / 100 : price.value;
  if (!(unitPrice > 0) || !(value.value > 0)) return null;
  const q = value.value / unitPrice;
  const r = Math.round(q);
  const rel = (0.5 * 10 ** -decimals(value.raw)) / value.value + (0.5 * 10 ** -decimals(price.raw)) / price.value;
  return r >= 1 && Math.abs(q - r) <= q * rel * INFER_ROUND_SLACK + 1e-9 ? r : null;
}

const round4 = (n: number): number => Math.round(n * 1e4) / 1e4;

function parseBlock(b: Block): { row: ImportRow; meta: RowMeta } {
  const tlv = b.anchor.exchange === "TLV";
  const lines: LineInfo[] = [];
  const amts: Amt[] = [];
  const pcts: Pct[] = [];
  b.lines.forEach((orig, i) => {
    const a = takeAmounts(orig, i);
    const p = takePercents(a.work, orig, i);
    amts.push(...a.amts);
    pcts.push(...p.pcts);
    const letters = (p.work.replace(new RegExp(`${NUM}`, "g"), " ").match(/\p{L}/gu) ?? []).length;
    lines.push({ work: p.work, text: letters >= 3, nums: takeNumbers(p.work, i) });
  });

  const value = amts[0] ?? null;
  const currency: "ILS" | "USD" = value ? (value.symbol === "$" ? "USD" : "ILS") : tlv ? "ILS" : "USD";
  const agorot = tlv && currency === "ILS";

  // Price candidates: numbers on figure-only lines first, then numbers at the edge of name lines.
  const tier1 = lines.flatMap((l) => (l.text ? [] : l.nums));
  const tier2 = lines.flatMap((l) => (l.text ? l.nums.filter((n) => l.work.slice(0, n.start).trim() === "" || l.work.slice(n.end).trim() === "") : []));
  const cands = [...tier1, ...tier2];
  let price: Num | null = null;
  if (value) {
    price = cands.find((c) => wholeQuantity(value, c, agorot) !== null) ?? cands[0] ?? null;
  }
  const pnl = pickPnl(pcts, value, price ? price.line : null);

  const meta: RowMeta = {};
  let quantity: number | null = null;
  if (value && price && value.value >= INFER_MIN_VALUE) {
    quantity = wholeQuantity(value, price, agorot);
    if (quantity === null) {
      const unitPrice = agorot ? price.value / 100 : price.value;
      if (unitPrice > 0) { quantity = round4(value.value / unitPrice); meta.quantity_fractional = true; }
    }
  }
  if (quantity === null) meta.quantity_uncertain = true;

  let cost: number | null = null;
  if (price && pnl !== null && pnl > INFER_MIN_PNL_PCT) {
    cost = round4(price.value / (1 + pnl / 100));
    meta.cost_inferred = true;
    meta.pnl_pct = pnl;
  }

  // Name: the first line that reads as text (names are truncated and unreliable; the ticker is the identity).
  let name = "";
  for (const l of lines) {
    if (!l.text) continue;
    let w = l.work;
    if (price && price.line === lines.indexOf(l)) w = blank(w, price.start, price.end);
    name = w.replace(/[^\p{L}\p{N}\s.&'’"\-–,/()…]/gu, " ").replace(/^[\s.…\-–,]+|[\s\-–,]+$/gu, "").replace(/\s+/g, " ");
    if (name) break;
  }
  if (!name) name = tlv ? "" : b.anchor.ticker;

  const row: ImportRow = {
    index: 0, name,
    symbol: tlv ? null : b.anchor.ticker,
    tase_number: tlv ? b.anchor.ticker : null,
    quantity, price: price ? price.value : null, value: value ? value.value : null, cost,
    currency, unit: agorot ? "agorot" : currency === "USD" ? "USD" : "ILS",
    matched_name: null, flags: [],
  };
  return { row, meta };
}

/** Parse OCR text of one Meitav Trade screenshot. Returns null when the text has no card at all. */
export function parseMeitavText(text: string): ParsedRows | null {
  const blocks = toBlocks(text);
  if (blocks.length === 0) return null;
  const rows: ImportRow[] = [];
  const meta: RowMeta[] = [];
  for (const b of blocks) {
    const { row, meta: m } = parseBlock(b);
    row.index = rows.length;
    rows.push(row);
    meta.push(m);
  }
  return { layout: "meitav_trade", rows, meta };
}
