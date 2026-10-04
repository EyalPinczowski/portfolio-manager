/**
 * Locale-tolerant number parser for the import review table.
 *
 * Accepts "1234", "1,234", "1,234.56", "1.234,5", "1 234,5", "1.234.567", "(12.5)" (negative), "-3", "₪1,234".
 * Rules (documented because "1.234" is genuinely ambiguous):
 *  - both "," and "." present: the LAST one is the decimal separator, the other is grouping.
 *  - one kind only, repeated ("1,234,567" / "1.234.567"): grouping.
 *  - a single "," followed by exactly 3 digits and a 1-3 digit head ("1,234"): grouping (Israeli/US style).
 *  - any other single "," ("12,5"): decimal comma.
 *  - a single "." is always a decimal point ("1.234" is 1.234), the usual way brokers print prices.
 * Returns null for empty or unparseable input (never NaN).
 */
export function parseLocaleNumber(input: string): number | null {
  let s = input.trim().replace(/[\s  ₪$€%]/g, "");
  if (s === "") return null;
  let neg = false;
  if (/^\(.*\)$/.test(s)) { neg = true; s = s.slice(1, -1); }
  if (s.startsWith("-") || s.startsWith("−")) { neg = !neg; s = s.slice(1); }
  else if (s.startsWith("+")) s = s.slice(1);
  if (!/^[\d.,]+$/.test(s) || !/\d/.test(s)) return null;

  const commas = (s.match(/,/g) ?? []).length;
  const dots = (s.match(/\./g) ?? []).length;
  let normalized: string;
  if (commas > 0 && dots > 0) {
    const decimalSep = s.lastIndexOf(",") > s.lastIndexOf(".") ? "," : ".";
    const groupSep = decimalSep === "," ? "." : ",";
    if (s.split(decimalSep).length > 2) return null;
    normalized = s.split(groupSep).join("").replace(decimalSep, ".");
  } else if (commas > 1) {
    normalized = s.replace(/,/g, "");
  } else if (dots > 1) {
    if (!/^\d{1,3}(\.\d{3})+$/.test(s)) return null;
    normalized = s.replace(/\./g, "");
  } else if (commas === 1) {
    normalized = /^\d{1,3},\d{3}$/.test(s) ? s.replace(",", "") : s.replace(",", ".");
  } else {
    normalized = s;
  }
  if (/^\.|\.$/.test(normalized) && normalized.replace(".", "") === "") return null;
  const v = Number(normalized);
  if (!Number.isFinite(v)) return null;
  return neg ? -v : v;
}
