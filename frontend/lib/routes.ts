/** Path of a holding's detail page. Query-based so it works in a static export. */
export const holdingHref = (id: number): string => `/holding?id=${id}`;

const SYMBOL_RE = /^[A-Za-z0-9.^=-]{1,20}$/;
/** A ticker as typed or from the URL: trimmed and upper-cased, or null when it is not a plausible ticker. */
export const analyzeSymbol = (raw: string | null | undefined): string | null => {
  const s = (raw ?? "").trim();
  return SYMBOL_RE.test(s) ? s.toUpperCase() : null;
};
/** Path of one stock analysis. Query-based so it works in a static export. */
export const analyzeHref = (symbol: string): string => `/analyze?symbol=${encodeURIComponent(symbol)}`;
