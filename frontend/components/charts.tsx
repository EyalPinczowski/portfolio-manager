"use client";
import { useEffect, useRef } from "react";
import {
  createChart, createSeriesMarkers, CandlestickSeries, HistogramSeries, LineSeries, LineStyle, ColorType, type IChartApi, type SeriesMarker, type Time,
} from "lightweight-charts";

export function palette() {
  const dark = typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches;
  return {
    dark,
    text: dark ? "#a5b0c0" : "#475467",
    grid: dark ? "#232d3c" : "#e4e8ef",
    up: dark ? "#4cd9a0" : "#046c4e",
    down: dark ? "#ff8a80" : "#b42318",
    you: dark ? "#34d399" : "#047857",
    sp: dark ? "#fbbf24" : "#b45309",
    ta: dark ? "#c084fc" : "#7e22ce",
    sma20: dark ? "#60a5fa" : "#1d4ed8",
    sma50: dark ? "#fbbf24" : "#b45309",
    bands: dark ? "#7d8aa0" : "#98a2b3",
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

export interface Candle { time: string; open: number; high: number; low: number; close: number }
export interface PriceLevel { price: number; kind: "support" | "resistance" }
export interface PatternMark { time: string; text: string; bearish: boolean }
export interface PriceChartLabels { support: string; resistance: string; aria: string }

/** Simple moving average over closes; null until `n` bars exist. Display only. */
export function smaSeries(closes: number[], n: number): (number | null)[] {
  return closes.map((_, i) => (i + 1 < n ? null : closes.slice(i + 1 - n, i + 1).reduce((a, b) => a + b, 0) / n));
}

/** Bollinger bands (n bars, k standard deviations). Display only. */
export function bollingerSeries(closes: number[], n = 20, k = 2): { upper: (number | null)[]; lower: (number | null)[] } {
  const mid = smaSeries(closes, n);
  const upper: (number | null)[] = [];
  const lower: (number | null)[] = [];
  closes.forEach((_, i) => {
    const m = mid[i];
    if (m === null) { upper.push(null); lower.push(null); return; }
    const w = closes.slice(i + 1 - n, i + 1);
    const sd = Math.sqrt(w.reduce((a, b) => a + (b - m) ** 2, 0) / n);
    upper.push(m + k * sd);
    lower.push(m - k * sd);
  });
  return { upper, lower };
}

/** Candlesticks (or a line when there is no OHLC) with SMA20/SMA50, Bollinger bands, level lines and pattern markers. */
export function PriceChart({ bars, levels, marks, labels, height = 280 }: {
  bars: Candle[]; levels: PriceLevel[]; marks: PatternMark[]; labels: PriceChartLabels; height?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || bars.length === 0 || typeof window.matchMedia !== "function") return; // no canvas/media APIs (tests)
    let chart: IChartApi;
    try { chart = baseChart(el, height); } catch { return; }
    const p = palette();
    const good = bars.filter((b) => [b.open, b.high, b.low, b.close].every(isNum));
    const closes = good.map((b) => b.close);
    const line = (vals: (number | null)[], color: string, width: 1 | 2, style: LineStyle = LineStyle.Solid) => {
      const s = chart.addSeries(LineSeries, { color, lineWidth: width, lineStyle: style, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
      s.setData(vals.flatMap((v, i) => (v === null ? [] : [{ time: good[i].time as Time, value: v }])));
      return s;
    };
    const bb = bollingerSeries(closes);
    line(bb.upper, p.bands, 1, LineStyle.Dashed);
    line(bb.lower, p.bands, 1, LineStyle.Dashed);
    line(smaSeries(closes, 50), p.sma50, 1);
    line(smaSeries(closes, 20), p.sma20, 1);
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: p.up, downColor: p.down, borderUpColor: p.up, borderDownColor: p.down, wickUpColor: p.up, wickDownColor: p.down,
      priceLineVisible: false,
    });
    candles.setData(good.map((b) => ({ time: b.time as Time, open: b.open, high: b.high, low: b.low, close: b.close })));
    for (const l of levels) {
      if (!isNum(l.price)) continue;
      candles.createPriceLine({
        price: l.price, color: l.kind === "support" ? p.up : p.down, lineWidth: 1, lineStyle: LineStyle.Dotted,
        axisLabelVisible: true, title: l.kind === "support" ? labels.support : labels.resistance,
      });
    }
    const times = new Set(good.map((b) => b.time));
    const markers: SeriesMarker<Time>[] = marks
      .filter((m) => times.has(m.time))
      .sort((a, b) => a.time.localeCompare(b.time))
      .map((m) => ({
        time: m.time as Time, position: m.bearish ? "aboveBar" : "belowBar", shape: m.bearish ? "arrowDown" : "arrowUp",
        color: m.bearish ? p.down : p.up, text: m.text,
      }));
    if (markers.length > 0) createSeriesMarkers(candles, markers);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars, levels, marks, labels, height]);
  return <div dir="ltr" ref={ref} role="img" aria-label={labels.aria} data-testid="price-chart" className="w-full" style={{ height }} />;
}
