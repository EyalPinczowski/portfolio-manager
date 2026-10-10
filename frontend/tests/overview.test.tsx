import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";
import type { Holding, Summary } from "@/lib/api";

vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { AllocationDonut, groupSlices } from "@/components/AllocationDonut";
import { PortfolioSummary, totalPnl } from "@/components/PortfolioSummary";
import { HoldingsList } from "@/components/HoldingsList";

const wrap = (locale: "en" | "he", ui: ReactNode) =>
  render(<NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>{ui}</NextIntlClientProvider>);
const h = (id: number, over: Partial<Holding> = {}): Holding => ({
  id, symbol: `S${id}`, name_en: `Name${id}`, name_he: `שם${id}`, asset_type: "stock", market: "US", quantity: 1,
  price: 10, currency: "USD", day_change_pct: 0, value_ils: 1000, value_native: 270, pnl: { ils: 100, usd: 27, pct: 11 },
  pnl_native: 27, check_numbers: false, weight_pct: 10, horizon: null, stop_tp_status: "needs_horizon",
  score_card: { total: 0, technical: 0, patterns: 0, confidence: 0 }, price_stale: false, price_basis: "live", price_is_fresh: true, ...over,
});

describe("groupSlices", () => {
  it("groups slices under the threshold as Other and keeps the total at 100", () => {
    const s = groupSlices([
      { key: "a", label: "A", value: 60 }, { key: "b", label: "B", value: 35.5 },
      { key: "c", label: "C", value: 2.5 }, { key: "d", label: "D", value: 2 },
    ], "Other");
    expect(s.map((x) => x.label)).toEqual(["A", "B", "Other"]);
    expect(s[2].pct).toBeCloseTo(4.5);
    expect(s.reduce((a, x) => a + x.pct, 0)).toBeCloseTo(100);
  });
  it("a single small slice keeps its own name, and bad values are ignored", () => {
    const s = groupSlices([{ key: "a", label: "A", value: 99 }, { key: "b", label: "B", value: 1 }, { key: "x", label: "X", value: NaN }, { key: "y", label: "Y", value: 0 }], "Other");
    expect(s.map((x) => x.label)).toEqual(["A", "B"]);
    expect(groupSlices([], "Other")).toEqual([]);
  });
  it("caps the number of named slices", () => {
    const items = Array.from({ length: 12 }, (_, i) => ({ key: String(i), label: `L${i}`, value: 10 }));
    const s = groupSlices(items, "Other");
    expect(s).toHaveLength(8);
    expect(s[7].other).toBe(true);
  });
});

describe("allocation donut", () => {
  it.each(["en", "he"] as const)("legend, accessible name and table fallback (%s)", (loc) => {
    const m = loc === "en" ? en : he;
    wrap(loc, <AllocationDonut holdings={[h(1, { value_ils: 7000 }), h(2, { value_ils: 2800 }), h(3, { value_ils: 100 }), h(4, { value_ils: 100 })]} />);
    const legend = screen.getByTestId("donut-legend");
    expect(within(legend).getByText(loc === "en" ? "Name1" : "שם1")).toBeInTheDocument();
    expect(within(legend).getByText(/70%/)).toBeInTheDocument();
    expect(within(legend).getByText(m.overview.other)).toBeInTheDocument();
    expect(screen.getByRole("img").getAttribute("aria-label")).toContain(m.overview.byHolding);
    expect(screen.getByRole("table", { hidden: true })).toBeInTheDocument();
  });
  it("toggles to currency", () => {
    wrap("en", <AllocationDonut holdings={[h(1, { value_ils: 6000 }), h(2, { value_ils: 4000, currency: "ILS" })]} />);
    fireEvent.click(screen.getByRole("button", { name: en.overview.byCurrency }));
    const legend = screen.getByTestId("donut-legend");
    expect(legend).toHaveTextContent("USD");
    expect(legend).toHaveTextContent("ILS");
    expect(legend).toHaveTextContent("60%");
  });
});

describe("summary math", () => {
  it("total P&L sums holdings with a cost basis and ignores the rest", () => {
    const t = totalPnl([h(1, { value_ils: 1100, pnl: { ils: 100, usd: 27, pct: 10 } }), h(2, { value_ils: 600, pnl: { ils: -100, usd: -27, pct: -14 } }), h(3, { pnl: null }), h(4, { value_ils: 5000, pnl: undefined })]);
    expect(t?.ils).toBe(0);
    const u = totalPnl([h(1, { value_ils: 1100, pnl: { ils: 100, usd: 27, pct: 10 } })]);
    expect(u?.pct).toBeCloseTo(10);
    expect(totalPnl([h(1, { pnl: null })])).toBeNull();
  });
  it("renders value, total P&L and today", () => {
    const z = { ils: 0, usd: 0, pct: 0 };
    const s = { value: { ils: 3700, usd: 1000 }, day_pnl: { ils: 37, usd: 10, pct: 1 }, week_pnl: z, month_pnl: z, since_start_pnl: z } as unknown as Summary;
    wrap("en", <PortfolioSummary s={s} holdings={[h(1, { value_ils: 1100, pnl: { ils: 100, usd: 27, pct: 10 } })]} />);
    expect(screen.getByTestId("total-main")).toBeInTheDocument();
    expect(screen.getByTestId("summary-total-pnl")).toHaveTextContent(/100/);
    expect(screen.getByTestId("summary-total-pnl")).toHaveTextContent(/10\.00%/);
    expect(screen.getByTestId("summary-day")).toHaveTextContent(/37|10/);
  });
});

describe("compact rows", () => {
  it("one link row per holding", () => {
    wrap("en", <HoldingsList holdings={Array.from({ length: 20 }, (_, i) => h(i + 1))} />);
    expect(screen.getAllByTestId("holding-row")).toHaveLength(20);
  });
});
