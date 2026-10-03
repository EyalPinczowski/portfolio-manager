"use client";
import { useEffect, useRef } from "react";
import {
  createChart, HistogramSeries, LineSeries, ColorType, type IChartApi, type Time,
} from "lightweight-charts";

function palette() {
  const dark = typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  return {
    dark,
    text: dark ? "#cbd5e1" : "#334155",
    grid: dark ? "#1e293b" : "#e2e8f0",
    up: dark ? "#34d399" : "#047857",
    down: dark ? "#f87171" : "#b91c1c",
    you: dark ? "#60a5fa" : "#1d4ed8",
    sp: dark ? "#fbbf24" : "#b45309",
    ta: dark ? "#c084fc" : "#7e22ce",
  };
}

function baseChart(el: HTMLElement, height: number): IChartApi {
  const p = palette();
  return createChart(el, {
    height,
    autoSize: true,
    layout: { background: { type: ColorType.Solid, color: "transparent" }, textColor: p.text, attributionLogo: false },
    grid: { vertLines: { color: p.grid }, horzLines: { color: p.grid } },
    rightPriceScale: { borderColor: p.grid },
    timeScale: { borderColor: p.grid },
    handleScroll: false,
    handleScale: false,
  });
}

export interface BarPoint { time: string; value: number }

/** Bar chart where positive bars are green and negative are red, with a zero baseline. */
export function PnlBarChart({ data, label }: { data: BarPoint[]; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const p = palette();
    const chart = baseChart(el, 160);
    const s = chart.addSeries(HistogramSeries, { priceFormat: { type: "custom", formatter: (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(1)}%` } });
    s.setData(data.filter((d) => isNum(d.value)).map((d) => ({ time: d.time as Time, value: d.value, color: d.value >= 0 ? p.up : p.down })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [data]);
  return <div ref={ref} role="img" aria-label={label} className="h-40 w-full" />;
}

/** Benchmark values are null/undefined where the benchmark has no data for that day; those points are skipped. */
export interface SeriesPoint { time: string; you: number; sp500?: number | null; ta125?: number | null }

const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function SinceStartChart({ data, label, names }: { data: SeriesPoint[]; label: string; names: { you: string; sp500: string; ta125: string } }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const p = palette();
    const chart = baseChart(el, 220);
    const fmt = { type: "custom" as const, formatter: (v: number) => `${v > 0 ? "+" : ""}${v.toFixed(1)}%` };
    const mk = (title: string, color: string, key: "you" | "sp500" | "ta125", width: 1 | 2 | 3) => {
      const s = chart.addSeries(LineSeries, { color, lineWidth: width, title, priceFormat: fmt, lastValueVisible: false, priceLineVisible: false });
      s.setData(data.flatMap((d) => { const v = d[key]; return isNum(v) ? [{ time: d.time as Time, value: v }] : []; }));
    };
    mk(names.sp500, p.sp, "sp500", 1);
    mk(names.ta125, p.ta, "ta125", 1);
    mk(names.you, p.you, "you", 3);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [data, names]);
  return <div ref={ref} role="img" aria-label={label} className="h-56 w-full" />;
}
