import { DASH, formatMoney, formatPct, pnlSign } from "@/lib/format";

const ok = (n: number | null | undefined): n is number => typeof n === "number" && Number.isFinite(n);

/**
 * P&L text with an explicit +/- sign and an up/down arrow, never colour alone.
 * Missing or non-finite numbers render a dash, never 0, NaN or null.
 */
export function PnlText({
  value, pct, currency, locale, className = "",
}: { value?: number | null; pct?: number | null; currency?: string; locale: string; className?: string }) {
  const v = ok(value) ? value : undefined;
  const p = ok(pct) ? pct : undefined;
  if (v === undefined && p === undefined) return <span className={`text-muted ${className}`}>{DASH}</span>;
  const sign = pnlSign(v ?? p ?? 0);
  const tone = sign === "+" ? "text-gain" : sign === "-" ? "text-loss" : "text-muted";
  const arrow = sign === "+" ? "▲" : sign === "-" ? "▼" : "";
  return (
    <span className={`${tone} tabular-nums ${className}`} dir="ltr">
      {arrow && <span aria-hidden="true" className="me-1 text-[0.7em]">{arrow}</span>}
      {v !== undefined && currency && formatMoney(v, currency, locale, { signed: true })}
      {v !== undefined && currency && p !== undefined && " "}
      {p !== undefined && (v !== undefined && currency ? `(${formatPct(p, locale, { signed: true })})` : formatPct(p, locale, { signed: true }))}
    </span>
  );
}
