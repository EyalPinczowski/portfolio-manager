"use client";
import { useLocale } from "next-intl";
import type { HeatmapItem } from "@/lib/api";
import { formatPct } from "@/lib/format";

export function tileColor(chg: number): string {
  const t = Math.min(1, Math.abs(chg) / 3);
  if (Math.abs(chg) < 0.05) return "hsl(215 16% 40%)";
  return chg > 0 ? `hsl(145 60% ${34 - t * 10}%)` : `hsl(0 65% ${44 - t * 10}%)`;
}
const span = (w: number) => (w >= 20 ? 3 : w >= 10 ? 2 : 1);

/** Treemap-like grid: tile area follows weight, colour follows day change, sign is always printed. */
export function Heatmap({ items }: { items: HeatmapItem[] }) {
  const locale = useLocale();
  const sorted = [...items].sort((a, b) => b.weight_pct - a.weight_pct);
  return (
    <ul className="grid grid-flow-dense grid-cols-4 gap-1 md:grid-cols-6" style={{ gridAutoRows: "4.5rem" }} dir="ltr">
      {sorted.map((i) => (
        <li
          key={i.symbol}
          className="flex flex-col justify-between overflow-hidden rounded-lg p-2 text-white"
          style={{ background: tileColor(i.day_change_pct), gridColumn: `span ${span(i.weight_pct)}`, gridRow: `span ${i.weight_pct >= 20 ? 2 : 1}` }}
        >
          <span className="truncate text-sm font-bold">{i.symbol}</span>
          <span className="text-xs">{i.sector}</span>
          <span className="text-xs font-semibold tabular-nums">
            {formatPct(i.day_change_pct, locale, { signed: true })} · {i.weight_pct.toFixed(1)}%
          </span>
        </li>
      ))}
    </ul>
  );
}
