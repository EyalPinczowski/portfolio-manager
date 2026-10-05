/**
 * Second Hebrew broker app, "תיק אישי" holdings list (`hebrew_broker_cards`), see docs/import-formats.md.
 * Port of backend/app/importer/hebrew_cards.py: keep the two in step (same rules, same results on the shared
 * fixture tests/fixtures/hebrew_broker_cards.json).
 *
 * One card per holding: the last price, a coloured daily-change chip (`-0.33%`), a name or symbol and `כמות N`.
 * OCR gives them on one line or stacked, so each `כמות N` line is an anchor and its card is the few lines around
 * it. Quantity = the number after `כמות`; price = the nearest decimal number; `כמות` never stays in the name; a
 * number inside a Hebrew name (`35 מחקה ת"א MTF`) is part of the name. No value or cost on this screen (null).
 * Currency is guessed from the name (US ticker = USD, Hebrew fund = ILS) and every row is flagged
 * `currency_changed`, which blocks confirming until the user has checked the currency.
 */
import type { ImportRow } from "../api";
import { HEBREW_RE, SYMBOL_STOPWORDS, numberRe, symbolRe, toNumber } from "./parse";

const QTY_WORD = "כמות";
const QTY_MAX = 1e12;
const MONEY_MAX = 1e15;
const NAME_MAX_CHARS = 200;
const CARD_LINES = 4; // lines of a stacked card besides the one with `כמות`
const W = "\\p{L}\\p{N}_";
const QTY_AFTER = new RegExp(`${QTY_WORD}\\s*:?\\s*(\\d[\\d,]*(?:\\.\\d+)?)`, "u");
const QTY_BEFORE_LINE = new RegExp(`^\\s*(\\d[\\d,]*(?:\\.\\d+)?)\\s*${QTY_WORD}\\s*$`, "u");
const CHIP = /[-+]?\(?\d[\d,]*(?:\.\d+)?\)?\s*%/g;
const DECIMAL_ONLY = /^\d[\d,]*\.\d+$/;
const DECIMAL_TOKEN = new RegExp(`(?<![${W}])[-+]?\\d[\\d,]*\\.\\d+(?![${W}])`, "gu");
const NAME_JUNK = /[₪$%|▲▼△▽↑↓()+\-:,.]/g;
const SKIP_WORDS = ["האחזקות", "נתוני", "מיון", "סוג נייר", "כח קניה", "אחזקות", "פילוח", "יתרות"];

const chipless = (s: string): string => s.replace(CHIP, " ");

function quantityOf(line: string): { qty: number | null; rest: string } | null {
  const m = QTY_AFTER.exec(line) ?? QTY_BEFORE_LINE.exec(line);
  if (!m) return null;
  return { qty: toNumber(m[1]), rest: `${line.slice(0, m.index)} ${line.slice(m.index + m[0].length)}`.trim() };
}

/** `כמות` followed by a number (or a line `N כמות`), the one thing a table header never has. */
export function detectHebrewCards(text: string): boolean {
  return text.split(/\r?\n/).some((l) => quantityOf(l) !== null);
}

const tokensOf = (line: string): string[] => (line.match(numberRe()) ?? []).filter((t) => !t.endsWith("%"));

function decimalToken(line: string): string | null {
  for (const t of tokensOf(chipless(line))) if (DECIMAL_ONLY.test(t.replace(/^[+\-()]+|[+\-()]+$/g, ""))) return t;
  return null;
}

const isSkipped = (line: string): boolean => SKIP_WORDS.some((w) => line.includes(w));

/** The text of a line that is not a number, chip or sign: what could be a name. */
function letters(line: string): string {
  return chipless(line).replace(DECIMAL_TOKEN, " ").replace(NAME_JUNK, " ").replace(/\s+/g, " ").trim();
}

function nameOfRest(rest: string): string {
  return chipless(rest).replace(DECIMAL_TOKEN, " ").replace(NAME_JUNK, " ").replace(/\s+/g, " ").trim();
}

const cleanNumber = (x: number | null, ceiling = MONEY_MAX): number | null =>
  x === null || !Number.isFinite(x) || x < 0 || x > ceiling ? null : x;

const maskDigitRuns = (s: string): string => s.slice(0, NAME_MAX_CHARS).replace(/\d{6,}/g, "***").slice(0, NAME_MAX_CHARS);

function cardRow(chunk: string[], at: number): ImportRow | null {
  const found = quantityOf(chunk[at]);
  if (!found) return null;
  const { qty, rest } = found;
  const others = chunk.map((_, i) => i).filter((i) => i !== at).sort((a, b) => Math.abs(a - at) - Math.abs(b - at));
  const order = [rest, ...others.map((i) => chunk[i])];
  let price: number | null = null;
  let priceToken: string | null = null;
  let priceFrom = -1;
  for (let k = 0; k < order.length; k++) {
    const tok = decimalToken(order[k]);
    if (tok !== null) { price = toNumber(tok); priceToken = tok; priceFrom = k; break; }
  }
  if (price === null) { // no decimal anywhere: a whole-number price on a line of its own
    for (let k = 0; k < order.length; k++) {
      const toks = tokensOf(chipless(order[k]));
      if (letters(order[k]) === "" && toks.length > 0) { price = toNumber(toks[toks.length - 1]); priceToken = toks[toks.length - 1]; priceFrom = k; break; }
    }
  }
  let name = "";
  for (let k = 0; k < order.length; k++) {
    let s = order[k];
    if (k === priceFrom && priceToken) s = s.replace(priceToken, " ");
    s = k ? letters(s) : nameOfRest(s);
    if (s && !isSkipped(s) && /[A-Za-z֐-׿]/.test(s)) { name = s; break; }
  }
  if (!name && price === null) return null;
  let symbol: string | null = null;
  if (!HEBREW_RE.test(name)) {
    for (const sym of name.match(symbolRe()) ?? []) {
      if (!SYMBOL_STOPWORDS.has(sym) && sym.length <= 5) { symbol = sym; break; }
    }
  }
  const usd = symbol !== null;
  return {
    index: 0, name: maskDigitRuns(name), symbol, tase_number: null, quantity: cleanNumber(qty, QTY_MAX), price: cleanNumber(price),
    value: null, cost: null, currency: usd ? "USD" : "ILS", unit: usd ? "USD" : "ILS", matched_name: null, flags: ["currency_changed"],
  };
}

export function parseHebrewCardsText(text: string): ImportRow[] {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const anchors = lines.map((l, i) => (quantityOf(l) ? i : -1)).filter((i) => i >= 0);
  if (anchors.length === 0) return [];
  const oneLine = anchors.every((a) => decimalToken(quantityOf(lines[a])!.rest) !== null);
  const chunks: [string[], number][] = [];
  if (oneLine) {
    for (const a of anchors) chunks.push([[lines[a]], 0]);
  } else {
    const last = anchors[anchors.length - 1];
    const afterFirst = lines.slice(last + 1, last + 1 + CARD_LINES).some((l) => decimalToken(l) !== null);
    anchors.forEach((a, k) => {
      if (afterFirst) { // `כמות N` first, price / chip / name follow
        const end = Math.min(k + 1 < anchors.length ? anchors[k + 1] : lines.length, a + 1 + CARD_LINES);
        chunks.push([lines.slice(a, end), 0]);
      } else { // `כמות N` last, the card is the lines above it
        const start = Math.max(k ? anchors[k - 1] + 1 : 0, a - CARD_LINES);
        chunks.push([lines.slice(start, a + 1), a - start]);
      }
    });
  }
  const rows: ImportRow[] = [];
  for (const [chunk, at] of chunks) {
    const row = cardRow(chunk, at);
    if (row) { row.index = rows.length; rows.push(row); }
  }
  return rows;
}
