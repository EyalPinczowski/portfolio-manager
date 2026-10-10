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
import { INFER_COST_TOL, INFER_MIN_PNL_PCT, INFER_MIN_VALUE, INFER_ROUND_SLACK } from "../config";
import type { ImportRow } from "../api";
import type { ParsedRows, RowMeta } from "./types";

const STRICT_BULLET = "[•·●∙▪*|]";
const LOOSE_BULLET = "[•·●∙▪*|:+–—-]";
const TICKER = "[A-Z][A-Z0-9]{0,5}(?:[.-][A-Z]{1,2})?";
const TASE_NO = "\\d{6,8}";
const ARROWS = /[↑↓▲▼⬆⬇△▽⇧⇩⬈⬊↗↘]/u;
const revStr = (s: string): string => Array.from(s).reverse().join("");
/** Section header words (letters only), as printed and as a right-to-left line may come out reversed. */
const SECTION_HEADERS = new Set(["קרןסל", "קרנותסל", "אחר", "מניות", "תעודותסל", "קרנותנאמנות", "ניירותזרים"].flatMap((h) => [h, revStr(h), "ןרקלס", "תונרקלס", "םירזתוריינ", "תוריינםירז", "זריםניירות"]));
/** Summary header and list furniture of the full portfolio screen (letters only, as printed and reversed): they never
 *  start a card, whatever amount sits on the same line. */
const FURNITURE: string[][] = [["תיק", "אישי"], ["שינוי", "יומי"], ["שינוי", "מעלות"], ["יתרות"], ["פירוט", "מזומן", "ובטחונות"], ["האחזקות", "שלי"], ["מיון"], ["הכל"], ["ניע", "זרים"], ["קרנות"], ["מיטב", "טרייד"], ["מיטב"], ["טרייד"]];
const furnitureForms = (w: string[]): string[] => [w.join(""), revStr(w.join("")), [...w].reverse().join(""), w.map(revStr).join("")];
const FURNITURE_TOKENS = new Set(FURNITURE.flatMap(furnitureForms));
/** Bottom navigation words (Hebrew app tab bar), as printed and reversed. */
const NAV_TOKENS = new Set(["הוראות", "ניירות", "במעקב", "מסחר", "התיק", "שלי", "ראשי", "בית", "תיק", "שוק", "עוד"].flatMap((w) => [w, revStr(w)]));

/** The label `מספר ני"ע` (security number) next to a TASE number, and its reversed spelling. */
const SEP = "[\\s•·●∙▪*|:.\\-–—]";
const Q = "[\"'״׳”“]";
const LABEL = `(?:מספר\\s*ני${Q}{0,2}ע|ע${Q}{0,2}ינ\\s*רפסמ)`;
const labelRe = () => new RegExp(LABEL, "g");
const labelNumRe = () => new RegExp(`${LABEL}${SEP}{0,3}(\\d{4,8})(?!\\d)|(?<!\\d)(\\d{4,8})${SEP}{0,3}${LABEL}`);
const AMOUNT_PRESENT = /[$₪]\s{0,2}\d|\d\s{0,2}[$₪]/;

/** `labeled`: the line carries the `מספר ני"ע` label, so the card's name is ABOVE the anchor. */
export interface Anchor { start: number; end: number; exchange: string; ticker: string; strict: boolean; labeled?: boolean }

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

interface Block { anchor: Anchor; lines: string[]; hasAmount: boolean }
interface Entry { text: string; kind: "line" | "section" | "furniture" | "nav"; anchors: Anchor[]; labeled: boolean }

/** OCR often prints a minus as an en/em dash or a Unicode hyphen: a dash glued to a digit or % is a minus. */
const MINUS_RE = /(?<!\d)[−–—‐‑](?=[\d%])/g;
const SIMPLE: Anchor = { start: 0, end: 0, exchange: "", ticker: "", strict: false };
const NUM_G = "\\d[\\d,]*(?:\\.\\d+)*";

const hasAmount = (s: string): boolean => AMOUNT_PRESENT.test(s);
const isTextLine = (s: string): boolean => (s.replace(new RegExp(NUM_G, "g"), " ").match(/\p{L}/gu) ?? []).length >= 3;
/** A figure-only line that can be a price: no letters, no amount, no percent. */
const isPriceLine = (s: string): boolean => !isTextLine(s) && !hasAmount(s) && !s.includes("%") && /\d/.test(s);
const isPercentLine = (s: string): boolean => s.includes("%") && !isTextLine(s) && !hasAmount(s);
const isFurnitureLine = (line: string): boolean => FURNITURE_TOKENS.has(line.replace(/[^\p{L}]/gu, ""));
const isNavLine = (line: string): boolean => {
  if (/[\d$₪%]/.test(line)) return false;
  const words = line.match(/\p{L}+/gu) ?? [];
  return words.length > 0 && words.every((w) => NAV_TOKENS.has(w));
};

function prepare(text: string): Entry[] {
  const out: Entry[] = [];
  for (const raw of text.split(/\r?\n/)) {
    let line = raw.replace(MINUS_RE, "-").replace(/−/g, "-").trim();
    if (!line) continue;
    if (isSectionLine(line)) { out.push({ text: line, kind: "section", anchors: [], labeled: false }); continue; }
    if (isFurnitureLine(line)) { out.push({ text: line, kind: "furniture", anchors: [], labeled: false }); continue; }
    if (isNavLine(line)) { out.push({ text: line, kind: "nav", anchors: [], labeled: false }); continue; }
    let anchors = findAnchors(line);
    let labeled = false;
    if (labelRe().test(line)) {
      const m = labelNumRe().exec(line);
      const blankLabel = (l: string) => l.replace(labelRe(), (x) => " ".repeat(x.length));
      if (anchors.length > 0) {
        line = blankLabel(line);
        labeled = anchors.length === 1;
        anchors = anchors.map((a) => ({ ...a, labeled }));
      } else if (m) {
        anchors = [{ start: m.index, end: m.index + m[0].length, exchange: "", ticker: m[1] ?? m[2], strict: true, labeled: true }];
        labeled = true;
      } else {
        line = blankLabel(line);
      }
    }
    out.push({ text: line, kind: "line", anchors, labeled });
  }
  return out;
}

/** Lines ABOVE a labeled anchor (`TLV • 1180422 מספר ני"ע`) that belong to its card: the nearest name line, the figures
 *  between it and the anchor, and up to two price lines above the name. Never across an amount line, a section bar,
 *  the navigation or another anchor. */
function preRange(entries: Entry[], i: number, lo: number): [number, number] | null {
  for (let k = i - 1; k >= lo && i - k <= 4; k--) {
    const s = entries[k].text;
    if (hasAmount(s)) return null;
    if (isTextLine(s)) {
      let start = k;
      while (start - 1 >= lo && k - start < 2 && isPriceLine(entries[start - 1].text)) start--;
      return [start, i];
    }
  }
  return null;
}

/** Cards with no exchange line and no security number: a name, a `$`/`₪` value and a price. `run` is a stretch of
 *  lines that belong to no anchored card. A card starts at a name line (text, no amount) once the card before it has
 *  its amount; up to two price lines just above the name come with it. Stretches without an amount give no card. */
function simpleCards(run: string[]): string[][] {
  const groups: string[][] = [[]]; // groups[0] holds the lines before the first name
  for (const s of run) {
    const last = groups[groups.length - 1];
    if (isTextLine(s) && !hasAmount(s) && (groups.length === 1 || last.some(hasAmount))) {
      const carry: string[] = [];
      while (last.length > 0 && carry.length < 2 && isPriceLine(last[last.length - 1])) carry.unshift(last.pop()!);
      groups.push([...carry, s]);
    } else {
      last.push(s);
    }
  }
  return groups.slice(1).filter((g) => g.some(hasAmount));
}

function toBlocks(text: string): Block[] {
  const entries = prepare(text);
  // Pass 1: the lines above each labeled anchor belong to that anchor's card.
  const claimed = new Set<number>();
  const pre = new Map<number, string[]>();
  let lo = 0;
  entries.forEach((e, i) => {
    if (e.kind === "line" && e.anchors.length === 0) return;
    if (e.kind === "line" && e.labeled) {
      const rng = preRange(entries, i, lo);
      if (rng) {
        pre.set(i, entries.slice(rng[0], rng[1]).map((x) => x.text));
        for (let k = rng[0]; k < rng[1]; k++) claimed.add(k);
      }
    }
    lo = i + 1;
  });
  // Pass 2: walk the lines, a line goes to the open card above it or waits in `orphans`.
  const blocks: Block[] = [];
  let cur: Block | null = null;
  let orphans: string[] = [];
  let started = false; // the list has started: a card or a section bar was seen
  const flush = () => {
    // Text above the list (summary header, totals, tabs) is never a simple card.
    if (started) for (const g of simpleCards(orphans)) blocks.push({ anchor: SIMPLE, lines: g, hasAmount: true });
    orphans = [];
  };
  for (let i = 0; i < entries.length; i++) {
    const e = entries[i];
    if (e.kind !== "line") { flush(); cur = null; started = started || e.kind === "section"; continue; }
    if (claimed.has(i)) continue;
    if (e.anchors.length === 0) {
      const closed = cur !== null && !!cur.anchor.labeled && cur.hasAmount;
      // After its amount a labeled card still takes a stray percent line (the P&L %).
      if (cur !== null && (!closed || isPercentLine(e.text))) {
        cur.lines.push(e.text);
        cur.hasAmount = cur.hasAmount || hasAmount(e.text);
      } else {
        orphans.push(e.text);
      }
      continue;
    }
    flush();
    started = true;
    for (let j = 0; j < e.anchors.length; j++) {
      const a = e.anchors[j];
      const from = j === 0 ? 0 : a.start;
      const to = j + 1 < e.anchors.length ? e.anchors[j + 1].start : e.text.length;
      const rest = `${e.text.slice(from, a.start)} ${e.text.slice(a.end, to)}`.trim();
      const lines = j === 0 ? [...(pre.get(i) ?? [])] : [];
      if (rest) lines.push(rest);
      cur = { anchor: a, lines, hasAmount: lines.some(hasAmount) };
      blocks.push(cur);
    }
  }
  flush();
  return blocks;
}

const NUM = "\\d[\\d,]*(?:\\.\\d+)*"; // `2.891.30`: the earlier dots are thousands marks
const toNum = (raw: string): number => {
  const parts = raw.replace(/,/g, "").replace(/[.,]+$/, "").split(".");
  return Number(parts.length > 2 ? `${parts.slice(0, -1).join("")}.${parts[parts.length - 1]}` : parts.join("."));
};
const decimals = (raw: string): number => (/\.(\d+)$/.exec(raw.replace(/[.,]+$/, ""))?.[1].length ?? 0);
// A price glued to a name by RTL layout (`3,481 <name>`): digits with a thousands comma or a decimal
// part, so a bare 5-9 digit security number and names like `S&P 500` or `TA-125` stay intact.
const PRICE_TOKEN = "(?:\\d{1,3}(?:,\\d{3})+(?:\\.\\d+)?|\\d+\\.\\d+)";
const NAME_PRICE_LEAD = new RegExp(`^[₪$]?\\s?${PRICE_TOKEN}[₪$]?\\s+(?=\\S)`);
const NAME_PRICE_TRAIL = new RegExp(`(?<=\\S)\\s+[₪$]?\\s?${PRICE_TOKEN}[₪$]?$`);
const cleanName = (w: string): string => {
  const name = w.replace(/[^\p{L}\p{N}\s.&'’"\-–,/()…]/gu, " ").replace(/^[\s.…\-–,]+|[\s\-–,]+$/gu, "").replace(/\s+/g, " ");
  const stripped = name.replace(NAME_PRICE_LEAD, "").replace(NAME_PRICE_TRAIL, "");
  return /\p{L}/u.test(stripped) ? stripped : name;
};
const blank = (s: string, from: number, to: number): string => s.slice(0, from) + " ".repeat(to - from) + s.slice(to);

interface Amt { value: number; raw: string; symbol: "$" | "₪"; line: number; pos: number }
interface Pct { value: number; line: number; pos: number; arrow: boolean; signed: boolean }
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
    pcts.push({ value: m[1] === "-" ? -v : v, line, pos: m.index!, arrow: arrowNear(m.index!, m.index! + m[0].length), signed: m[1] !== "" });
  }
  let w = work.replace(new RegExp(`(?<![\\d.,])([-+]?)\\s?(${NUM})\\s*%`, "g"), (s) => " ".repeat(s.length));
  // Reversed (visual-order) form: "%-0.31" or "%0.31-", number glued to the percent sign.
  for (const m of w.matchAll(new RegExp(`%([-+]?)(${NUM})([-+]?)(?![\\d])`, "g"))) {
    const v = toNum(m[2]);
    const neg = m[1] === "-" || (m[1] === "" && m[3] === "-");
    pcts.push({ value: neg ? -v : v, line, pos: m.index!, arrow: arrowNear(m.index!, m.index! + m[0].length), signed: m[1] !== "" || m[3] !== "" });
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
function pickPnl(pcts: Pct[], value: Amt | null, priceLine: number | null): Pct | null {
  if (pcts.length === 0) return null;
  if (value) {
    const onValue = pcts.filter((p) => p.line === value.line);
    if (onValue.length > 0) return onValue.reduce((a, b) => (Math.abs(a.pos - value.pos) <= Math.abs(b.pos - value.pos) ? a : b));
  }
  const arrows = pcts.filter((p) => p.arrow);
  if (arrows.length === 1) return arrows[0];
  if (pcts.length === 2) {
    const zeros = pcts.filter((p) => p.value === 0);
    if (zeros.length === 1) return pcts.find((p) => p.value !== 0)!;
    if (value && priceLine !== null) {
      const score = (p: Pct) => Math.abs(p.line - value.line) - Math.abs(p.line - priceLine);
      const [a, b] = pcts;
      if (score(a) < score(b)) return a;
      if (score(b) < score(a)) return b;
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
  const hasSymbol = ["NASDAQ", "NYSE", "AMEX"].includes(b.anchor.exchange);
  // No exchange line: a simple value card or a security-number card (currency, fund).
  const simple = !hasSymbol && !tlv;
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
  const currency: "ILS" | "USD" = value ? (value.symbol === "$" ? "USD" : "ILS") : hasSymbol ? "USD" : "ILS";
  const agorot = tlv && currency === "ILS";

  // Price candidates: numbers on figure-only lines first, then numbers at the edge of name lines.
  const tier1 = lines.flatMap((l) => (l.text ? [] : l.nums));
  const tier2 = lines.flatMap((l) => (l.text ? l.nums.filter((n) => l.work.slice(0, n.start).trim() === "" || l.work.slice(n.end).trim() === "") : []));
  // A figure-only line wins: a number glued to a name (`חיסכון ירוק 41`) is part of the name.
  const cands = tier1.length > 0 ? tier1 : tier2;
  let price: Num | null = null;
  if (value) {
    // No guessing: a price with no whole quantity is used only when it is the single candidate (a real fractional holding).
    price = cands.find((c) => wholeQuantity(value, c, agorot) !== null) ?? (cands.length === 1 ? cands[0] : null);
  }
  const picked = pickPnl(pcts, value, price ? price.line : null);
  // An unsigned P&L is trusted except the classic misread of `+10.00%` as `110.00%` (the '+' read as a '1'):
  // unsigned, integer part starting with 1 and value >= 100. Then the P&L is unknown.
  const pnl = picked && !(!picked.signed && picked.value >= 100 && String(Math.trunc(picked.value)).startsWith("1")) ? picked.value : null;

  const meta: RowMeta = {};
  let quantity: number | null = null;
  if (value && price && value.value >= INFER_MIN_VALUE) {
    quantity = wholeQuantity(value, price, agorot);
    if (quantity === null && !simple) {
      const unitPrice = agorot ? price.value / 100 : price.value;
      if (unitPrice > 0) { quantity = round4(value.value / unitPrice); meta.quantity_fractional = true; }
    }
  }
  if (quantity === null) meta.quantity_uncertain = true;

  let cost: number | null = null;
  if (price && pnl !== null && pnl > INFER_MIN_PNL_PCT) {
    let consistent = true;
    if (quantity !== null && value) { // value / (price x quantity) must be ~1
      const unitPrice = agorot ? price.value / 100 : price.value;
      consistent = unitPrice > 0 && Math.abs(value.value / (unitPrice * quantity) - 1) <= INFER_COST_TOL;
    }
    if (consistent) {
      cost = round4(price.value / (1 + pnl / 100));
      meta.cost_inferred = true;
      meta.pnl_pct = pnl;
    }
  }

  // Name: the first line that reads as text (names are truncated and unreliable; the ticker is the identity).
  let name = "";
  for (const l of lines) {
    if (!l.text) continue;
    let w = l.work;
    if (price && price.line === lines.indexOf(l)) w = blank(w, price.start, price.end);
    name = cleanName(w);
    if (name) break;
  }
  if (!name) name = hasSymbol ? b.anchor.ticker : "";

  const row: ImportRow = {
    index: 0, name,
    symbol: hasSymbol ? b.anchor.ticker : null,
    tase_number: (tlv || b.anchor.labeled) && b.anchor.ticker ? b.anchor.ticker : null,
    quantity, price: price ? price.value : null, value: value ? value.value : null, cost,
    currency, unit: agorot ? "agorot" : currency === "USD" ? "USD" : "ILS",
    matched_name: null, flags: [],
  };
  return { row, meta };
}

const TOTAL_LABEL = new Set(furnitureForms(["תיק", "אישי"]));
const hasLetter = (x: string): boolean => /\p{L}/u.test(x);

/**
 * The `תיק אישי` (personal portfolio) amount of the summary header, in ILS: a number only. The amount sits on the
 * label's line or on the line just before or after it (OCR order varies); the largest pure `₪` amount of those
 * lines wins, because the daily change and the change from cost printed next to it are always smaller.
 * Null when the screen has no such header. Mirrors `detect_broker_total` in `backend/app/importer/meitav.py`.
 */
export function detectBrokerTotal(text: string): number | null {
  const lines = text.split(/\r?\n/).map((l) => l.replace(MINUS_RE, "-").replace(/−/g, "-").trim());
  for (let i = 0; i < lines.length; i++) {
    if (!TOTAL_LABEL.has(lines[i].replace(/[^\p{L}]/gu, "")) || lines[i].includes("%")) continue;
    let best: number | null = null;
    for (const k of [i - 1, i, i + 1]) {
      if (k < 0 || k >= lines.length) continue;
      const { work, amts } = takeAmounts(lines[k], k);
      if ((k !== i && hasLetter(work)) || work.includes("%")) continue;
      for (const a of amts) {
        if (a.symbol === "₪" && a.value > 0 && a.value <= 1e15 && (best === null || a.value > best)) best = a.value;
      }
    }
    if (best !== null) return best;
  }
  return null;
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
  return { layout: "meitav_trade", rows, meta, broker_total: detectBrokerTotal(text) };
}
