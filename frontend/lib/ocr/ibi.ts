/**
 * Third broker layout, the IBI app's "תיק ההשקעות שלי" cards (`ibi_cards`), see docs/import-formats.md.
 * Port of backend/app/importer/ibi.py: keep the two in step (same rules, same results on the shared fixture
 * tests/fixtures/ibi_cards.json).
 *
 * One card per holding. Right side: a ticker (`ACME`) or a Hebrew name (may contain digits, `תכ.תא35`) and `N יחידות`.
 * Left side: the last price, a coloured day-change chip (`-0.31%`) and the day's profit or loss with a currency sign
 * (`-$0.89`, `+₪6.40`). No value, cost or currency column. Each `N יחידות` line (or `יחידות N`, when OCR reverses it)
 * is an anchor and its card is the few lines around it.
 *
 * Rules: quantity = the number next to `יחידות`; price = the nearest decimal that is not an amount; `$` on the day
 * amount = USD; `₪` = ILS, and unit `agorot` when the amount fits quantity x price/100 x chip better than
 * quantity x price x chip. A leading number in a Hebrew name is part of the name; `יחידות` never stays in it. Value and
 * cost stay null; every row is flagged `currency_changed`, so the user must confirm currency and unit.
 */
import type { ImportRow } from "../api";
import { HEBREW_RE, SYMBOL_STOPWORDS, symbolRe } from "./parse";

const UNITS_WORD = "יחידות";
const QTY_MAX = 1e12;
const MONEY_MAX = 1e15;
const NAME_MAX_CHARS = 200;
const CARD_LINES = 5; // lines of a stacked card besides the one with `יחידות`
const W = "\\p{L}\\p{N}_";
const NUM = "\\d[\\d,]*(?:\\.\\d+)?";
const UNITS_BEFORE = new RegExp(`(?<![${W}.,])(${NUM})\\s*${UNITS_WORD}`, "u");
const UNITS_AFTER = new RegExp(`${UNITS_WORD}\\s*:?\\s*(${NUM})(?![${W}])`, "u");
const CHIP = new RegExp(`([-+−])?\\(?(${NUM})\\)?\\s*%`, "gu");
const AMOUNT = new RegExp(`(?<![\\d.,])[-+−]?\\s?[$₪]\\s?[-+−]?${NUM}[-+−]?|(?<![\\d.,])${NUM}[$₪]`, "gu");
const DECIMAL = new RegExp(`(?<![${W}.,])\\d[\\d,]*\\.\\d+(?![${W}])`, "gu");
const INTEGER = new RegExp(`(?<![${W}.,])\\d[\\d,]*(?![${W}.,])`, "gu");
const NAME_JUNK = /[₪$%|▲▼△▽↑↓()+\-−:,]/g;
const LETTER = /[A-Za-z֐-׿]/;
const SKIP_WORDS = ["תיק", "מחיר", "רווח", "הפסד", "טרייד", "מרכז ידע", "הוראות", "שוק", "מיון", "נתוני"];

const num = (s: string): number => Number(s.replace(/,/g, ""));
const first = (re: RegExp, s: string): RegExpExecArray | null => { re.lastIndex = 0; return re.exec(s); };

function unitsOf(line: string): { qty: number; rest: string } | null {
  const m = UNITS_BEFORE.exec(line) ?? UNITS_AFTER.exec(line);
  if (!m) return null;
  return { qty: num(m[1]), rest: `${line.slice(0, m.index)} ${line.slice(m.index + m[0].length)}`.trim() };
}

/** A number next to `יחידות`, the one thing no other layout has. */
export function detectIbi(text: string): boolean {
  return text.split(/\r?\n/).some((l) => unitsOf(l) !== null);
}

const bare = (line: string): string => line.replace(CHIP, " ").replace(AMOUNT, " ");
const decimalOf = (line: string): string | null => first(DECIMAL, bare(line))?.[0] ?? null;

/** What could be a name: no chip, amount or decimal number; a leading integer stays. */
function clean(line: string): string {
  return bare(line).replace(DECIMAL, " ").replace(NAME_JUNK, " ").replace(/\s+/g, " ").trim().replace(/^[ .]+|[ .]+$/g, "");
}

const nameOk = (s: string): boolean => s !== "" && LETTER.test(s) && !SKIP_WORDS.some((w) => s.includes(w));

function amountOf(line: string): { value: number; sign: string } | null {
  const m = first(AMOUNT, line.replace(CHIP, " "));
  if (!m) return null;
  const value = num(first(new RegExp(NUM, "u"), m[0])![0]);
  return { value: /[-−]/.test(m[0]) ? -value : value, sign: m[0].includes("$") ? "$" : "₪" };
}

function chipOf(line: string): number | null {
  const m = first(CHIP, line);
  if (!m) return null;
  const v = num(m[2]);
  return m[1] === "-" || m[1] === "−" ? -v : v;
}

/** True when the day amount fits the price in agorot (price/100) better than the price in ILS. */
function isAgorot(qty: number, price: number, amount: number, chip: number): boolean {
  if (chip === 0 || amount === 0) return false;
  const day = Math.abs(amount);
  const pct = Math.abs(chip) / 100;
  return Math.abs((qty * price / 100) * pct - day) < Math.abs(qty * price * pct - day);
}

const cleanNumber = (x: number | null, ceiling = MONEY_MAX): number | null =>
  x === null || !Number.isFinite(x) || x < 0 || x > ceiling ? null : x;

const maskDigitRuns = (s: string): string => s.slice(0, NAME_MAX_CHARS).replace(/\d{6,}/g, "***").slice(0, NAME_MAX_CHARS);

function cardRow(chunk: string[], at: number): ImportRow | null {
  const found = unitsOf(chunk[at]);
  if (!found) return null;
  const { qty } = found;
  const lines = [...chunk];
  lines[at] = found.rest;
  const order = [at, ...lines.map((_, i) => i).filter((i) => i !== at).sort((a, b) => Math.abs(a - at) - Math.abs(b - at))];
  let price: number | null = null;
  let priceToken: string | null = null;
  let priceLine = -1;
  for (const i of order) {
    const tok = decimalOf(lines[i]);
    if (tok !== null) { price = num(tok); priceToken = tok; priceLine = i; break; }
  }
  if (price === null) { // no decimal anywhere: a whole-number price on a line of its own
    for (const i of order) {
      if (LETTER.test(clean(lines[i]))) continue;
      const ints = bare(lines[i]).match(INTEGER);
      if (ints) { priceToken = ints[ints.length - 1]; price = num(priceToken); priceLine = i; break; }
    }
  }
  let amount: { value: number; sign: string } | null = null;
  for (const i of order) { amount = amountOf(lines[i]); if (amount) break; }
  let chip: number | null = null;
  for (const i of order) { chip = chipOf(lines[i]); if (chip !== null) break; }

  const texts = lines.map((ln, i) => clean(i === priceLine && priceToken ? ln.replace(priceToken, " ") : ln));
  const atName = order.find((i) => nameOk(texts[i]));
  let name = "";
  if (atName !== undefined) {
    let lo = atName;
    let hi = atName;
    if (atName !== at) { // a name split over several lines
      while (lo - 1 >= 0 && lo - 1 !== at && nameOk(texts[lo - 1])) lo--;
      while (hi + 1 < texts.length && hi + 1 !== at && nameOk(texts[hi + 1])) hi++;
    }
    name = texts.slice(lo, hi + 1).join(" ");
  }
  if (!name && price === null) return null;
  let symbol: string | null = null;
  if (!HEBREW_RE.test(name)) {
    for (const sym of name.match(symbolRe()) ?? []) {
      if (!SYMBOL_STOPWORDS.has(sym) && sym.length <= 5) { symbol = sym; break; }
    }
  }
  let currency: "USD" | "ILS";
  let unit: "USD" | "ILS" | "agorot";
  if (amount && amount.sign === "$") { currency = "USD"; unit = "USD"; }
  else if (amount) {
    currency = "ILS";
    unit = price !== null && chip !== null && isAgorot(qty, price, amount.value, chip) ? "agorot" : "ILS";
  } else { currency = unit = symbol !== null ? "USD" : "ILS"; }
  return {
    index: 0, name: maskDigitRuns(name), symbol, tase_number: null, quantity: cleanNumber(qty, QTY_MAX), price: cleanNumber(price),
    value: null, cost: null, currency, unit, matched_name: null, flags: ["currency_changed"],
  };
}

export function parseIbiText(text: string): ImportRow[] {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  const anchors = lines.map((l, i) => (unitsOf(l) ? i : -1)).filter((i) => i >= 0);
  if (anchors.length === 0) return [];
  const oneLine = anchors.every((a) => decimalOf(unitsOf(lines[a])!.rest) !== null);
  const chunks: [string[], number][] = [];
  if (oneLine) {
    for (const a of anchors) chunks.push([[lines[a]], 0]);
  } else {
    const last = anchors[anchors.length - 1];
    const afterFirst = lines.slice(last + 1, last + 1 + CARD_LINES).some((l) => decimalOf(l) !== null);
    anchors.forEach((a, k) => {
      if (afterFirst) { // `N יחידות` first, name / price / chip / amount follow
        const end = Math.min(k + 1 < anchors.length ? anchors[k + 1] : lines.length, a + 1 + CARD_LINES);
        chunks.push([lines.slice(a, end), 0]);
      } else { // `N יחידות` last, the card is the lines above it
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
