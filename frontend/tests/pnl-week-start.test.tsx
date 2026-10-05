import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import type { Summary } from "@/lib/api";

const { day } = vi.hoisted(() => ({ day: { v: "monday" } }));
vi.mock("lightweight-charts", () => {
  const series = { setData: vi.fn() };
  const chart = { addSeries: vi.fn(() => series), timeScale: () => ({ fitContent: vi.fn() }), remove: vi.fn() };
  return { createChart: vi.fn(() => chart), HistogramSeries: {}, LineSeries: {}, ColorType: { Solid: "solid" } };
});
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
}));
vi.mock("@/lib/hooks", () => ({ useSettings: () => ({ data: { week_start_day: day.v } }) }));

import { PnlStrip } from "@/components/PnlStrip";

const zero = { ils: 0, usd: 0, pct: 0 };
const summary = (over: Partial<Summary> = {}) => ({
  value: { ils: 1, usd: 1 }, day_pnl: zero, week_pnl: zero, month_pnl: zero, since_start_pnl: zero, since_start_date: null,
  week_start: "2026-09-28", weekly_bars: [], monthly_bars: [], since_start_series: [], ...over,
}) as unknown as Summary;

describe("PnlStrip week captions", () => {
  it("uses the user's week_start_day", () => {
    render(<NextIntlClientProvider locale="en" messages={en}><PnlStrip s={summary()} /></NextIntlClientProvider>);
    expect(screen.getByText(/Week starting Monday/)).toBeInTheDocument();
    expect(screen.getByText("Each bar is a week that starts on Monday.")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/Sunday/);
  });
});
