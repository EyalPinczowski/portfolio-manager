"use client";
import { useLocale, useTranslations } from "next-intl";
import { formatNumber } from "@/lib/format";

export interface ScoreBarRow {
  name: string;
  label: string;
  score: number;
  /** True when the signal has no data (confidence 0): drawn grey with "No data", never as a neutral 0. */
  noData: boolean;
}

const clamp = (n: number) => Math.max(-100, Math.min(100, n));

/** One diverging bar per signal (-100..+100 around a centre line) and the total. Display only. */
export function ScoreBars({ rows, total, totalLabel, confidenceText }: {
  rows: ScoreBarRow[]; total: number | null; totalLabel?: string; confidenceText?: string;
}) {
  const t = useTranslations("holding");
  const locale = useLocale();
  const sign = (n: number) => `${n > 0 ? "+" : ""}${formatNumber(n, locale, 0)}`;
  const bar = (r: { score: number; noData: boolean }) => {
    const w = Math.abs(clamp(r.score)) / 2; // percent of the full track, half each side
    const left = r.score < 0;
    return (
      <div className="relative h-3 w-full rounded-full bg-surface-2" dir="ltr" aria-hidden="true">
        <span className="absolute inset-y-0 w-px bg-line" style={{ left: "50%" }} />
        {!r.noData && (
          <span
            className={`absolute inset-y-0 rounded-full ${left ? "bg-loss" : "bg-gain"}`}
            style={left ? { right: "50%", width: `${w}%` } : { left: "50%", width: `${w}%` }}
          />
        )}
      </div>
    );
  };
  return (
    <div className="space-y-1.5" role="list" aria-label={t("bars.aria")} data-testid="score-bars">
      {total !== null && (
        <div className="flex items-center gap-2" role="listitem" data-testid="score-total">
          <span className="w-28 shrink-0 text-sm font-bold">{totalLabel ?? t("bars.total")}</span>
          <div className="min-w-0 flex-1">{bar({ score: total, noData: false })}</div>
          <span className="w-14 shrink-0 text-end text-sm font-bold tabular-nums" dir="ltr">{sign(total)}</span>
        </div>
      )}
      {confidenceText && <p className="text-caption text-muted">{confidenceText}</p>}
      {rows.map((r) => (
        <div key={r.name} className="flex items-center gap-2" role="listitem" data-testid={`bar-${r.name}`} data-nodata={r.noData ? "1" : undefined}>
          <span className={`w-28 shrink-0 truncate text-sm ${r.noData ? "text-muted" : ""}`}>{r.label}</span>
          <div className="min-w-0 flex-1">{bar(r)}</div>
          {r.noData
            ? <span className="w-14 shrink-0 text-end text-caption text-muted">{t("bars.noData")}</span>
            : <span className="w-14 shrink-0 text-end text-sm tabular-nums" dir="ltr">{sign(r.score)}</span>}
        </div>
      ))}
    </div>
  );
}
