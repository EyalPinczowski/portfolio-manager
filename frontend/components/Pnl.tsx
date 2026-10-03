import { formatMoney, formatPct, pnlSign } from "@/lib/format";

/** P&L text with an explicit +/- sign and arrow, never colour alone. */
export function PnlText({
  value, pct, currency, locale, className = "",
}: { value?: number; pct?: number; currency?: string; locale: string; className?: string }) {
  const basis = value ?? pct ?? 0;
  const sign = pnlSign(basis);
  const tone =
    sign === "+" ? "text-emerald-700 dark:text-emerald-400"
    : sign === "-" ? "text-red-700 dark:text-red-400"
    : "text-slate-600 dark:text-slate-400";
  const arrow = sign === "+" ? "▲" : sign === "-" ? "▼" : "";
  return (
    <span className={`${tone} tabular-nums ${className}`} dir="ltr">
      {arrow && <span aria-hidden="true" className="me-1 text-[0.7em]">{arrow}</span>}
      {value !== undefined && currency && formatMoney(value, currency, locale, { signed: true })}
      {value !== undefined && currency && pct !== undefined && " "}
      {pct !== undefined && (value !== undefined ? `(${formatPct(pct, locale, { signed: true })})` : formatPct(pct, locale, { signed: true }))}
    </span>
  );
}
