export type Currency = "ILS" | "USD";

export function intlLocale(locale: string): string {
  return locale === "he" ? "he-IL" : "en-US";
}

export function formatMoney(
  value: number,
  currency: string,
  locale: string,
  opts: { signed?: boolean; compact?: boolean } = {},
): string {
  return new Intl.NumberFormat(intlLocale(locale), {
    style: "currency",
    currency,
    maximumFractionDigits: opts.compact ? 0 : currency === "ILS" || currency === "USD" ? 2 : 6,
    minimumFractionDigits: opts.compact ? 0 : undefined,
    signDisplay: opts.signed ? "exceptZero" : "auto",
  }).format(value);
}

export function formatPct(value: number, locale: string, opts: { signed?: boolean } = {}): string {
  return new Intl.NumberFormat(intlLocale(locale), {
    style: "percent",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    signDisplay: opts.signed ? "exceptZero" : "auto",
  }).format(value / 100);
}

export function formatNumber(value: number, locale: string, digits = 2): string {
  return new Intl.NumberFormat(intlLocale(locale), { maximumFractionDigits: digits }).format(value);
}

/** Shown wherever a value is missing (never 0, never an epoch date). */
export const DASH = "—";

/** ISO date (YYYY-MM-DD or full timestamp) to dd/mm/yyyy, timezone-independent for plain dates. Missing or invalid -> "—". */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (m) return `${m[3]}/${m[2]}/${m[1]}`;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return DASH;
  const p = (n: number) => String(n).padStart(2, "0");
  return `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()}`;
}

export function formatTime(iso: string, locale: string): string {
  if (Number.isNaN(new Date(iso).getTime())) return DASH;
  return new Intl.DateTimeFormat(intlLocale(locale), { hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
}

export function pnlSign(value: number): "+" | "-" | "" {
  return value > 0 ? "+" : value < 0 ? "-" : "";
}

/** The one formatter for portfolio/signal weights. Takes a PERCENT (12.5 = 12.5%); convert fractions with `* 100`. */
export function formatWeight(percent: number, locale: string, digits = 1): string {
  return `${formatNumber(percent, locale, digits)}%`;
}
