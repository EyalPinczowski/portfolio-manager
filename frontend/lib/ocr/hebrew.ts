/**
 * Hebrew-aware token comparison. Tesseract returns right-to-left lines in visual order, so a Hebrew word can come
 * out reversed ("125כשתא" for "תא125": the letters flip, digits keep their place). Two tokens are the same when
 * they are equal, or equal after reversing the letter runs, or equal after reversing the whole string. Only
 * tokens that contain Hebrew letters get the reversed comparison; Latin tokens must match as written.
 */
const HEB = /[֐-׿]/;
const HEB_RUN = /[֐-׿]+/g;

export const hasHebrew = (s: string): boolean => HEB.test(s);

const rev = (s: string): string => Array.from(s).reverse().join("");

/** Stable form for comparison: NFC, lower case, geresh/gershayim and quotes removed. */
export const normToken = (s: string): string => s.normalize("NFC").toLowerCase().replace(/["'`׳״]/g, "");

export function hebrewVariants(token: string): string[] {
  const t = normToken(token);
  if (!hasHebrew(t)) return [t];
  return Array.from(new Set([t, t.replace(HEB_RUN, rev), rev(t)]));
}

export function tokensMatch(a: string, b: string): boolean {
  const bv = new Set(hebrewVariants(b));
  return hebrewVariants(a).some((v) => bv.has(v));
}

const words = (s: string): string[] => s.split(/[\s\-–—,.;:()/]+/).map(normToken).filter(Boolean);

/** Same words (any order of the words is NOT accepted; each word may be reversed). Empty names never match. */
export function namesMatch(a: string, b: string): boolean {
  const wa = words(a);
  const wb = words(b);
  if (wa.length === 0 || wa.length !== wb.length) return false;
  const fwd = wa.every((w, i) => tokensMatch(w, wb[i]));
  // A fully reversed line reverses the word order as well.
  const bwd = wa.every((w, i) => tokensMatch(w, wb[wb.length - 1 - i]));
  return fwd || bwd;
}
