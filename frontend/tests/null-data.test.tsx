import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";
import type { Holding, Summary } from "@/lib/api";

const { setData } = vi.hoisted(() => ({ setData: vi.fn() }));
vi.mock("lightweight-charts", () => {
  const series = { setData };
  const chart = { addSeries: vi.fn(() => series), timeScale: () => ({ fitContent: vi.fn() }), remove: vi.fn() };
  return { createChart: vi.fn(() => chart), HistogramSeries: {}, LineSeries: {}, ColorType: { Solid: "solid" } };
});
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { HoldingsList } from "@/components/HoldingsList";
import { LiveHeader } from "@/components/LiveHeader";
import { PnlStrip } from "@/components/PnlStrip";
import { formatDate } from "@/lib/format";

const zero = { ils: 0, usd: 0, pct: 0 };
const holding = (over: Partial<Holding>): Holding => ({
  id: 1, symbol: "ARNA.TA", name_en: "Arena Fund", name_he: "קרן ארנה", asset_type: "fund", market: "TASE", quantity: 120,
  price: 31.4, currency: "ILS", day_change_pct: 0.2, value_ils: 3768, value_native: 3768, check_numbers: false, pnl: null, weight_pct: 4.2, horizon: null,
  stop_tp_status: "needs_horizon", score_card: { total: 0, technical: 0, patterns: 0, confidence: 0 }, price_stale: true, price_basis: "last_close", price_is_fresh: false, ...over,
});
const summary = (over: Partial<Summary> = {}): Summary => ({
  value: { ils: 1000, usd: 270 }, day_pnl: zero, week_pnl: zero, month_pnl: zero, since_start_pnl: zero,
  since_start_date: null, week_start: "2026-09-27", fx_stale: false, fx_basis: "live", price_sources: [],
  weekly_bars: [], monthly_bars: [],
  since_start_series: [
    { date: "2026-10-01", pct: 1.5, sp500_pct: null, ta125_pct: null },
    { date: "2026-10-02", pct: 2.1, sp500_pct: null, ta125_pct: undefined },
  ],
  as_of: "2026-10-03T08:55:00Z", screenshot_update_stale: false, markets: { US: { open: false }, TASE: { open: true }, CRYPTO: { open: true } }, ...over,
});

const wrap = (locale: "en" | "he", ui: ReactNode) =>
  render(<NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>{ui}</NextIntlClientProvider>);

describe("missing data never crashes and never shows 0 or an epoch date", () => {
  it.each(["en", "he"] as const)("holding with pnl: null renders a dash (%s)", (locale) => {
    wrap(locale, <HoldingsList holdings={[holding({}), holding({ id: 2, symbol: "TEVA.TA", name_en: "Teva", pnl: { ils: 100, usd: 27, pct: 5 }, price_stale: false, score_card: { total: 40, technical: 40, patterns: 40, confidence: 0.7 } })]} />);
    const dash = (locale === "en" ? en : he).holdings.pnlUnavailable;
    expect(screen.getAllByText(/—/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(dash, { exact: false }).length).toBeGreaterThan(0);
    // the holding with a known pnl still shows it
    expect(screen.getAllByText(/5(\.0+)?%/).length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toMatch(/NaN|1970|undefined|null/);
  });

  it("holding with pnl missing entirely (undefined) also renders a dash", () => {
    const h = holding({});
    delete (h as Partial<Holding>).pnl;
    wrap("en", <HoldingsList holdings={[h]} />);
    expect(screen.getAllByText(/—/).length).toBeGreaterThan(0);
  });

  it.each(["en", "he"] as const)("summary with since_start_date null and null benchmark points (%s)", (locale) => {
    setData.mockClear();
    const s = summary();
    wrap(locale, <><LiveHeader s={s} /><PnlStrip s={s} /></>);
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/1970|NaN|Invalid|undefined|null/);
    // the label has no date and no placeholder left over
    const m = locale === "en" ? en : he;
    expect(screen.getAllByText(m.header.sinceStartNoDate).length).toBeGreaterThan(0);
    expect(text).not.toContain("{date}");
    // charts received no null/NaN points
    for (const [data] of setData.mock.calls) {
      for (const p of data as { time: string; value: number }[]) {
        expect(Number.isFinite(p.value)).toBe(true);
        expect(p.time).not.toMatch(/^1970/);
      }
    }
    // benchmarks have no data, so only "my portfolio" is in the legend
    expect(screen.getByText(m.pnl.you)).toBeInTheDocument();
    expect(screen.queryByText(m.pnl.sp500)).toBeNull();
    expect(screen.queryByText(m.pnl.ta125)).toBeNull();
  });

  it("empty history shows a message instead of an empty chart", () => {
    wrap("en", <PnlStrip s={summary({ since_start_series: [] })} />);
    expect(screen.getByText(en.pnl.noHistory)).toBeInTheDocument();
  });

  it("weekly tile shows the Sunday week_start date", () => {
    wrap("en", <PnlStrip s={summary()} />);
    expect(screen.getByText("Week starting Sunday 27/09/2026")).toBeInTheDocument();
  });

  it("stale FX rate is flagged", () => {
    wrap("en", <LiveHeader s={summary({ fx_stale: true })} />);
    expect(screen.getByRole("status")).toHaveTextContent(en.header.fxStale);
  });

  it("formatDate never prints 1970 for missing or invalid input", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDate(undefined)).toBe("—");
    expect(formatDate("")).toBe("—");
    expect(formatDate("not a date")).toBe("—");
  });
});

