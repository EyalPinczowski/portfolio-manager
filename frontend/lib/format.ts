import { useSyncExternalStore } from "react";

export type Currency = "ILS" | "USD";

/**
 * The saved display preferences (Settings: main currency, number format). One module-level store read by
 * formatMoney / formatNumber, so every screen follows the setting without threading it through each call.
 * Screens re-render on a change through `useFormatPrefs` (AppShell re-keys its content on it).
 */
export type FormatPrefs = { mainCurrency: Currency; numberFormat: "full" | "compact" };
const DEFAULT_PREFS: FormatPrefs = { mainCurrency: "ILS", numberFormat: "full" };
let prefs: FormatPrefs = DEFAULT_PREFS;
const listeners = new Set<() => void>();
/** `notify: false` updates the store without waking subscribers (used while rendering, where waking them would warn). */
export function setFormatPrefs(next: Partial<FormatPrefs>, opts: { notify?: boolean } = {}): void {
  const merged = { ...prefs, ...next };
  if (merged.mainCurrency === prefs.mainCurrency && merged.numberFormat === prefs.numberFormat) return;
  prefs = merged;
  if (opts.notify !== false) listeners.forEach((l) => l());
}
export const getFormatPrefs = (): FormatPrefs => prefs;
export const resetFormatPrefs = (): void => setFormatPrefs(DEFAULT_PREFS);
export function useFormatPrefs(): FormatPrefs {
  return useSyncExternalStore((cb) => { listeners.add(cb); return () => { listeners.delete(cb); }; }, getFormatPrefs, () => DEFAULT_PREFS);
}
/** The two currencies of a pair, the saved main currency first (it is shown large, the other small). */
export function mainFirst<T>(ils: T, usd: T): { main: { cur: Currency; v: T }; other: { cur: Currency; v: T } } {
  const a = { cur: "ILS" as const, v: ils }, b = { cur: "USD" as const, v: usd };
  return prefs.mainCurrency === "USD" ? { main: b, other: a } : { main: a, other: b };
}

export function intlLocale(locale: string): string {
  return locale === "he" ? "he-IL" : "en-US";
}

export function formatMoney(
  value: number,
  currency: string,
  locale: string,
  opts: { signed?: boolean; compact?: boolean } = {},
): string {
  // "Compact" number format: 1,234,567 -> 1.2M. Small values (under 1,000) keep their exact decimals.
  if (prefs.numberFormat === "compact" && Math.abs(value) >= 1000) {
    return new Intl.NumberFormat(intlLocale(locale), {
      style: "currency", currency, notation: "compact", maximumFractionDigits: 1, signDisplay: opts.signed ? "exceptZero" : "auto",
    }).format(value);
  }
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
  if (prefs.numberFormat === "compact" && Math.abs(value) >= 1000) {
    return new Intl.NumberFormat(intlLocale(locale), { notation: "compact", maximumFractionDigits: 1 }).format(value);
  }
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

/** Israeli (TASE) symbols are quoted in shekels by the API; Yahoo's agorot are converted at the provider. */
export const isTaseSymbol = (symbol: string): boolean => symbol.toUpperCase().endsWith(".TA");

/** The price of a TASE stock in agorot (shekels x 100), for the "unit made explicit" hint. */
export const toAgorot = (shekels: number): number => Math.round(shekels * 100 * 100) / 100;

/** The one formatter for portfolio/signal weights. Takes a PERCENT (12.5 = 12.5%); convert fractions with `* 100`. */
export function formatWeight(percent: number, locale: string, digits = 1): string {
  return `${formatNumber(percent, locale, digits)}%`;
}

/** Age of a timestamp as a unit + count (for "5 min ago"); null for missing/invalid input. */
export function ageOf(iso: string | null | undefined, now: number = Date.now()): { unit: "now" | "minutes" | "hours" | "days"; n: number } | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return null;
  const min = Math.max(0, Math.floor((now - t) / 60_000));
  if (min < 1) return { unit: "now", n: 0 };
  if (min < 60) return { unit: "minutes", n: min };
  if (min < 24 * 60) return { unit: "hours", n: Math.floor(min / 60) };
  return { unit: "days", n: Math.floor(min / (24 * 60)) };
}

/** Quotes older than this while a stock market is open count as stale. */
export const QUOTES_STALE_MIN = 15;
