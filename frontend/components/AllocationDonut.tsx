"use client";
import { useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import type { Holding } from "@/lib/api";
import { formatNumber } from "@/lib/format";

/** Slices under this share are grouped as "Other"; at most MAX_SLICES named slices are shown. */
export const OTHER_THRESHOLD_PCT = 3;
export const MAX_SLICES = 7;

export interface Slice { key: string; label: string; pct: number; other?: boolean }
export type AllocationMode = "holding" | "currency";

/** Group (label, value) pairs into percentage slices; small ones (and any beyond MAX_SLICES) become one "Other" slice. */
export function groupSlices(items: { key: string; label: string; value: number }[], otherLabel: string, threshold = OTHER_THRESHOLD_PCT, max = MAX_SLICES): Slice[] {
  const pos = items.filter((i) => Number.isFinite(i.value) && i.value > 0);
  const total = pos.reduce((a, i) => a + i.value, 0);
  if (total <= 0) return [];
  const sorted = pos.map((i) => ({ key: i.key, label: i.label, pct: (i.value / total) * 100 })).sort((a, b) => b.pct - a.pct);
  const big = sorted.filter((s) => s.pct >= threshold).slice(0, max);
  const small = sorted.filter((s) => !big.includes(s));
  const rest = small.reduce((a, s) => a + s.pct, 0);
  if (small.length === 1 && big.length < max) return [...big, small[0]].sort((a, b) => b.pct - a.pct);
  return rest > 0 ? [...big, { key: "__other", label: otherLabel, pct: rest, other: true }] : big;
}

const color = (i: number, other?: boolean) => `var(${other ? "--chart-other" : `--chart-${(i % 7) + 1}`})`;

/** Inline-SVG donut of the portfolio by holding or by currency, with a legend and a screen-reader table. */
export function AllocationDonut({ holdings }: { holdings: Holding[] }) {
  const t = useTranslations("overview");
  const locale = useLocale();
  const [mode, setMode] = useState<AllocationMode>("holding");
  const slices = useMemo(() => {
    const items = mode === "holding"
      ? holdings.map((h) => ({ key: String(h.id), label: (locale === "he" ? h.name_he : h.name_en) || h.symbol, value: h.value_ils }))
      : [...holdings.reduce((m, h) => m.set(h.currency, (m.get(h.currency) ?? 0) + (Number.isFinite(h.value_ils) ? h.value_ils : 0)), new Map<string, number>())].map(([c, v]) => ({ key: c, label: c, value: v }));
    return groupSlices(items, t("other"));
  }, [holdings, mode, locale, t]);
  if (slices.length === 0) return null;
  const pct = (n: number) => `${formatNumber(n, locale, n < 10 ? 1 : 0)}%`;
  const modeLabel = t(mode === "holding" ? "byHolding" : "byCurrency");
  const aria = t("donutLabel", { mode: modeLabel, list: slices.map((s) => `${s.label} ${pct(s.pct)}`).join(", ") });
  const offsets = slices.map((_, i) => slices.slice(0, i).reduce((a, x) => a + x.pct, 0));
  return (
    <section className="card space-y-3" aria-label={t("allocation")}>
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-heading">{t("allocation")}</h2>
        <div role="group" aria-label={t("view")} className="flex gap-1 text-sm">
          {(["holding", "currency"] as const).map((m) => (
            <button key={m} type="button" aria-pressed={mode === m} onClick={() => setMode(m)} className={`min-h-11 rounded-lg px-3 ${mode === m ? "bg-brand-soft font-semibold text-brand-text" : "text-muted"}`}>
              {t(m === "holding" ? "byHolding" : "byCurrency")}
            </button>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-4">
        <svg viewBox="0 0 42 42" className="h-28 w-28 shrink-0 -rotate-90" role="img" aria-label={aria} data-testid="donut">
          <circle cx="21" cy="21" r="15.9155" fill="none" stroke="var(--surface-2)" strokeWidth="6" />
          {slices.map((s, i) => {
            const dash = Math.max(s.pct - 0.4, 0.1);
            return <circle key={s.key} cx="21" cy="21" r="15.9155" fill="none" stroke={color(i, s.other)} strokeWidth="6" strokeDasharray={`${dash} ${100 - dash}`} strokeDashoffset={-offsets[i]} />;
          })}
        </svg>
        <ul className="min-w-0 flex-1 space-y-1 text-sm" data-testid="donut-legend">
          {slices.map((s, i) => (
            <li key={s.key} className="flex items-center gap-2">
              <span aria-hidden="true" className="h-3 w-3 shrink-0 rounded-sm" style={{ background: color(i, s.other) }} />
              <bdi className="min-w-0 flex-1 truncate">{s.label}</bdi>
              <span className="tabular-nums text-muted" dir="ltr">{pct(s.pct)}</span>
            </li>
          ))}
        </ul>
      </div>
      <table className="sr-only">
        <caption>{t("tableCaption")} ({modeLabel})</caption>
        <thead><tr><th scope="col">{t("colName")}</th><th scope="col">{t("colShare")}</th></tr></thead>
        <tbody>{slices.map((s) => <tr key={s.key}><td>{s.label}</td><td>{pct(s.pct)}</td></tr>)}</tbody>
      </table>
    </section>
  );
}
