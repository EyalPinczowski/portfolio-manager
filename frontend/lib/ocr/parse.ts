/**
 * Port of backend/app/importer/parse.py (parse_line / parse_ocr_text) so OCR text can be turned into holding
 * rows on the device. Keep the two in step: tests/ocr-parse.test.ts mirrors the backend cases.
 *
 * Differences from Python, all deliberate:
 *  - `\w` is Unicode-aware in Python 3 (Hebrew letters count); JS `\w` is ASCII, so `[\p{L}\p{N}_]` is used.
 *  - lines that mention an account ("חשבון", "account") are dropped and 9+ digit runs are removed (scrubIdentifiers).
 */
import { IMPORT_VALUE_TOLERANCE, OCR_DROP_DIGITS_MIN, OCR_ID_DIGITS_MIN } from "../config";
import type { ImportRow } from "../api";

type Unit = ImportRow["unit"];

const W = "\\p{L}\\p{N}_";
const numberRe = () => new RegExp(`(?<![${W}.])[-+]?\\(?\\d[\\d,]*(?:\\.\\d+)?\\)?%?`, "gu");
const symbolRe = () => new RegExp(`(?<![${W}.$])[A-Z]{1,5}(?:[.-][A-Z]{1,3})?(?![${W}])`, "gu");
const HEBREW_RE = /[֐-׿]/;
const AGOROT_MARKERS = ["אגורות", "אגורה", "אג'", "אג׳", "agorot", "agora"];
const ILS_MARKERS = ["₪", 'ש"ח', "שח", "nis", "ils"];
const HEADER_WORDS = [
  "שם נייר", "שם הנייר", "כמות", "שער", "שווי", "עלות", 'סה"כ', "סהכ", "מספר נייר",
  "quantity", "price", "value", "symbol", "total", "holdings", "portfolio", "name",
];
const SYMBOL_STOPWORDS = new Set(["USD", "ILS", "NIS", "ETF", "ILA", "LTD", "INC", "CORP", "PLC", "CO", "THE", "NYSE"]);
const ACCOUNT_LINE_RE = /חשבון|מס['׳]? ?חשבון|\baccount\b|\bacct\b|\ba\/c\b/i;

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

function toNumber(token: string): number | null {
  let t = token.trim().replace(/%+$/, "");
  const neg = t.startsWith("-") || (t.startsWith("(") && t.endsWith(")"));
  t = t.replace(/^[+\-()]+|[+\-()]+$/g, "").replace(/,/g, "");
  if (!/^\d+(\.\d+)?$/.test(t)) return null;
  const v = Number(t);
  return neg ? -v : v;
}

const isIdLike = (token: string): boolean => /^\d+$/.test(token.trim()) && token.trim().length >= OCR_ID_DIGITS_MIN && token.trim().length <= 8;

function* combinations(n: number): Generator<[number, number, number]> {
  for (let a = 0; a < n; a++) for (let b = a + 1; b < n; b++) for (let c = b + 1; c < n; c++) yield [a, b, c];
}
function* permutations(n: number): Generator<[number, number, number]> {
  for (let a = 0; a < n; a++) for (let b = 0; b < n; b++) {
    if (b === a) continue;
    for (let c = 0; c < n; c++) if (c !== a && c !== b) yield [a, b, c];
  }
}

/** Indices (quantity, price, value) with quantity x price ~ value; printed left-to-right order is tried first. */
function findTriple(nums: number[], unit: Unit, tol: number, orderedOnly: boolean): [number, number, number] | null {
  const limit = Math.min(nums.length, 6);
  for (const [q, p, v] of orderedOnly ? combinations(limit) : permutations(limit)) {
    if (nums[v] <= 0 || nums[q] <= 0 || nums[p] <= 0) continue;
    const price = unit === "agorot" ? nums[p] / 100 : nums[p];
    if (Math.abs(nums[q] * price - nums[v]) / nums[v] <= tol) return [q, p, v];
  }
  return null;
}

export function parseLine(line: string, agorotGlobal: boolean, tol: number = IMPORT_VALUE_TOLERANCE): ImportRow | null {
  const low = line.toLowerCase();
  const all = line.match(numberRe()) ?? [];
  if (HEADER_WORDS.some((h) => low.includes(h)) && all.length < 2) return null;
  const tokens = all.filter((t) => !t.endsWith("%"));
  if (tokens.length < 2) return null;
  const letters = line.replace(numberRe(), " ");
  if (!/[A-Za-z֐-׿]/.test(letters)) return null;

  const agorot = agorotGlobal || AGOROT_MARKERS.some((m) => low.includes(m));
  let currency: "ILS" | "USD";
  if (line.includes("$") || low.includes("usd")) currency = "USD";
  else if (agorot || ILS_MARKERS.some((m) => low.includes(m)) || HEBREW_RE.test(letters)) currency = "ILS";
  else currency = "USD";
  let unit: Unit = agorot && currency === "ILS" ? "agorot" : currency === "USD" ? "USD" : "ILS";

  const nums: number[] = [];
  const rawTokens: string[] = [];
  for (const t of tokens) {
    const v = toNumber(t);
    if (v !== null) { nums.push(v); rawTokens.push(t); }
  }
  const row: ImportRow = {
    index: 0, name: "", symbol: null, tase_number: null, quantity: null, price: null, value: null, cost: null,
    currency, unit, matched_name: null, flags: [],
  };
  const alt: Unit = unit === "agorot" ? "ILS" : "agorot";
  const units: Unit[] = currency === "ILS" ? [unit, alt] : [unit];
  let triple: [number, number, number] | null = null;
  outer: for (const ordered of [true, false]) {
    for (const u of units) {
      triple = findTriple(nums, u, tol, ordered);
      if (triple) { unit = u; row.unit = u; break outer; }
    }
  }
  const used = new Set<number>();
  if (triple) {
    const [q, p, v] = triple;
    row.quantity = nums[q]; row.price = nums[p]; row.value = nums[v];
    used.add(q); used.add(p); used.add(v);
  } else if (nums.length >= 3) {
    [row.quantity, row.price, row.value] = nums.slice(0, 3);
    [0, 1, 2].forEach((i) => used.add(i));
  } else if (nums.length === 2) {
    [row.quantity, row.value] = nums;
    used.add(0); used.add(1);
  }
  for (let i = 0; i < nums.length; i++) {
    if (used.has(i)) continue;
    if (isIdLike(rawTokens[i]) && row.tase_number === null) row.tase_number = rawTokens[i].trim();
    else if (row.cost === null && nums[i] > 0) row.cost = nums[i];
  }
  let clean = line.replace(numberRe(), " ").replace(/[₪$%|]/g, " ");
  for (const m of AGOROT_MARKERS) clean = clean.replace(new RegExp(escapeRe(m), "gi"), " ");
  for (const sym of clean.match(symbolRe()) ?? []) {
    if (!SYMBOL_STOPWORDS.has(sym) && row.symbol === null && sym.length <= 5) { row.symbol = sym; break; }
  }
  row.name = clean.replace(/\s+/g, " ").replace(/^[ \-:,.]+|[ \-:,.]+$/g, "");
  return row;
}

/**
 * Privacy pass over raw OCR text, run before parsing: drops any line that mentions an account, and removes digit
 * runs of OCR_DROP_DIGITS_MIN+ (account numbers, phones, national IDs). 6-8 digit runs survive: they are TASE
 * security numbers and become `tase_number`.
 */
export function scrubIdentifiers(text: string): string {
  const longRun = new RegExp(`\\d{${OCR_DROP_DIGITS_MIN},}`, "g");
  return text
    .split(/\r?\n/)
    .filter((l) => !ACCOUNT_LINE_RE.test(l))
    .map((l) => {
      // The 7-digit security number of a `TLV • 1234567` card is needed for matching and is not an account number:
      // set it aside so no digit-run rule (now or later) can touch it.
      const kept: string[] = [];
      const hold = (_: string, a: string, n: string, b = "") => `${a}\u0001${kept.push(n) - 1}\u0001${b}`;
      const guarded = l
        .replace(/(TLV\s*[•·●∙▪*|:+–—-]?\s*)(\d{6,8})(?!\d)/g, (m, a, n) => hold(m, a, n))
        .replace(/(?<!\d)(\d{6,8})(\s*[•·●∙▪*|]\s*TLV)/g, (m, n, b) => hold(m, "", n, b));
      return guarded.replace(longRun, " ").replace(/\u0001(\d+)\u0001/g, (_, i) => kept[Number(i)]);
    })
    .join("\n");
}

export function parseOcrText(text: string, tol: number = IMPORT_VALUE_TOLERANCE): ImportRow[] {
  const lowered = text.toLowerCase();
  const agorotGlobal = AGOROT_MARKERS.some((m) => lowered.includes(m));
  const rows: ImportRow[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    const row = parseLine(line, agorotGlobal, tol);
    if (row) { row.index = rows.length; rows.push(row); }
  }
  return rows;
}
