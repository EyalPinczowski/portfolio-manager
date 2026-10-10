import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");
const lines = vi.hoisted(() => ({ price: [] as { price: number; title: string; color: string; lineStyle: number }[] }));
vi.mock("lightweight-charts", () => {
  const series = () => ({ setData: vi.fn(), createPriceLine: (o: { price: number; title: string; color: string; lineStyle: number }) => { lines.price.push(o); } });
  const chart = { addSeries: vi.fn(() => series()), timeScale: () => ({ fitContent: vi.fn() }), remove: vi.fn() };
  return { createChart: vi.fn(() => chart), createSeriesMarkers: vi.fn(), CandlestickSeries: "candle", HistogramSeries: "hist", LineSeries: "line", LineStyle: { Solid: 0, Dotted: 1, Dashed: 2 }, ColorType: { Solid: "solid" } };
});

import { api, type AnalystsOut, type ExitLevelsResult } from "@/lib/api";
import { mockRequest } from "@/lib/mock";
import { AnalystView } from "@/components/AnalystView";
import { ScoreBars } from "@/components/ScoreBars";
import { LevelLadder } from "@/components/LevelLadder";
import { MoreSections } from "@/components/MoreSections";
import { LevelLegend, PriceChart } from "@/components/charts";

const wrap = (ui: ReactNode, locale: "en" | "he" = "en") =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>
        <div dir={locale === "he" ? "rtl" : "ltr"}>{ui}</div>
      </NextIntlClientProvider>
    </SWRConfig>,
  );

const ok = (over: Partial<AnalystsOut> = {}): AnalystsOut => ({
  symbol: "NVDA", status: "ok", as_of: "2026-10-01", counts: { strong_buy: 6, buy: 12, hold: 8, sell: 2, strong_sell: 1 }, analysts_total: 29,
  targets: { low: 120, mean: 165, high: 210, currency: "USD" }, source: "yfinance", ...over,
});
const levels = () => mockRequest("GET", "/holdings/1/exit-levels") as ExitLevelsResult;

afterEach(() => { vi.restoreAllMocks(); cleanup(); lines.price.length = 0; });

describe("AnalystView", () => {
  it("shows the five counts, analyst total, as-of date, source and the target ladder vs the current price", async () => {
    vi.spyOn(api, "analysts").mockResolvedValue(ok());
    wrap(<AnalystView symbol="NVDA" price={142.3} currency="USD" />);
    const card = await screen.findByTestId("analyst-view");
    const counts = await within(card).findByTestId("analyst-counts");
    expect([...counts.querySelectorAll("li")].map((li) => li.textContent)).toEqual(["6Strong buy", "12Buy", "8Hold", "2Sell", "1Strong sell"]);
    expect(counts.querySelectorAll("li")).toHaveLength(5);
    expect(card.querySelectorAll("[data-seg]")).toHaveLength(5);
    expect(within(card).getByTestId("analyst-meta").textContent).toMatch(/29 analysts · As of .*2026.* · Source: yfinance/);
    expect(within(card).getByTestId("analyst-bar")).toHaveAttribute("aria-label", expect.stringContaining("Strong buy 6"));
    const ladder = within(card).getByTestId("target-ladder");
    expect(ladder.textContent).toContain("$120.00");
    expect(ladder.textContent).toContain("$165.00");
    expect(ladder.textContent).toContain("$210.00");
    expect(within(ladder).getByTestId("target-price")).toBeInTheDocument();
    expect(ladder.textContent).toMatch(/average target is \+?15\.9\d?%/);
    expect(card.textContent).toContain(en.holding.analysts.ownNote);
  });

  it("omits the ladder when there are no targets", async () => {
    vi.spyOn(api, "analysts").mockResolvedValue(ok({ targets: null }));
    wrap(<AnalystView symbol="NVDA" price={142.3} currency="USD" />);
    await screen.findByTestId("analyst-bar");
    expect(screen.queryByTestId("target-ladder")).toBeNull();
  });

  it("no coverage is its own state, not a neutral bar", async () => {
    vi.spyOn(api, "analysts").mockResolvedValue(ok({ status: "no_coverage", analysts_total: 0, as_of: null, targets: null, counts: { strong_buy: 0, buy: 0, hold: 0, sell: 0, strong_sell: 0 } }));
    wrap(<AnalystView symbol="TEVA.TA" price={62} currency="ILS" />);
    const none = await screen.findByTestId("analyst-no-coverage");
    expect(none.textContent).toContain(en.holding.analysts.noCoverage);
    expect(screen.queryByTestId("analyst-bar")).toBeNull();
  });

  it("loading, then an error with retry", async () => {
    const spy = vi.spyOn(api, "analysts").mockRejectedValue(new Error("x"));
    wrap(<AnalystView symbol="NVDA" price={1} currency="USD" />);
    expect(screen.getByText(en.holding.analysts.loading)).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent(en.holding.analysts.error);
    spy.mockResolvedValue(ok());
    fireEvent.click(screen.getByRole("button", { name: en.holding.analysts.retry }));
    expect(await screen.findByTestId("analyst-bar")).toBeInTheDocument();
  });

  it("Hebrew: Hebrew labels, numbers stay LTR, and the app adds no verdict of its own", async () => {
    vi.spyOn(api, "analysts").mockResolvedValue(ok());
    wrap(<AnalystView symbol="NVDA" price={142.3} currency="USD" />, "he");
    const card = await screen.findByTestId("analyst-view");
    expect(await within(card).findByText(he.holding.analysts.cat.strong_sell)).toBeInTheDocument();
    expect(within(card).getByTestId("analyst-bar")).toHaveAttribute("dir", "ltr");
    expect(card.textContent?.replace("yfinance", "")).not.toMatch(/[A-Za-z]{4,}/); // no English words (the data source name aside)
    expect(card.textContent).toContain(he.holding.analysts.ownNote);
  });

  it("the app's own wording around the card never says buy/sell/hold", () => {
    const own = [en.holding.analysts.title, en.holding.analysts.ownNote, en.holding.analysts.noCoverage, en.holding.analysts.noCoverageBody, en.holding.analysts.meanVsPrice, en.holding.analysts.targets];
    for (const t of own) expect(t).not.toMatch(/\b(buy|sell|hold|recommend\w*)\b/i);
    for (const t of [he.holding.analysts.title, he.holding.analysts.ownNote, he.holding.analysts.noCoverageBody]) expect(t).not.toMatch(/קנה|קנייה|מכור|מכירה|המלצה|המלצת/);
  });
});

describe("ScoreBars", () => {
  const rows = [
    { name: "technical", label: "Technical", score: 40, noData: false },
    { name: "patterns", label: "Patterns", score: -25, noData: false },
    { name: "analysts", label: "Analysts", score: 0, noData: true },
  ];
  it("draws one diverging bar per signal, grey 'No data' for a missing signal, and the total", () => {
    wrap(<ScoreBars rows={rows} total={18} />);
    const pos = screen.getByTestId("bar-technical");
    const neg = screen.getByTestId("bar-patterns");
    expect(pos.textContent).toContain("+40");
    expect(neg.textContent).toContain("-25");
    expect(pos.querySelector(".bg-gain")).not.toBeNull();
    expect(neg.querySelector(".bg-loss")).not.toBeNull();
    const none = screen.getByTestId("bar-analysts");
    expect(none).toHaveAttribute("data-nodata", "1");
    expect(none.textContent).toContain(en.holding.bars.noData);
    expect(none.querySelector(".bg-gain, .bg-loss")).toBeNull(); // not a neutral coloured bar
    expect(screen.getByTestId("score-total").textContent).toContain("+18");
  });
  it("no total when there is no score", () => {
    wrap(<ScoreBars rows={rows} total={null} />);
    expect(screen.queryByTestId("score-total")).toBeNull();
  });
  it("Hebrew label for the total", () => {
    wrap(<ScoreBars rows={rows} total={5} />, "he");
    expect(screen.getByTestId("score-total").textContent).toContain(he.holding.bars.total);
  });
});

describe("LevelLadder", () => {
  it("one table: stop, trailing, break-even, entry, price, TP1, TP2 in that order, with % from price and P&L", () => {
    const r = levels();
    wrap(<LevelLadder r={r} entry={55} />);
    const ids = [...document.querySelectorAll("[data-testid^=level-]")].map((e) => e.getAttribute("data-testid"));
    expect(ids).toEqual(expect.arrayContaining(["level-stop", "level-trailing_stop", "level-entry", "level-price", "level-take_profit"]));
    expect(ids.indexOf("level-stop")).toBeLessThan(ids.indexOf("level-trailing_stop"));
    expect(ids.indexOf("level-entry")).toBeLessThan(ids.indexOf("level-price"));
    expect(ids.indexOf("level-price")).toBeLessThan(ids.indexOf("level-take_profit"));
    expect(screen.getAllByTestId("level-take_profit")).toHaveLength(2);
    expect(screen.getByTestId("level-stop").textContent).toContain("▼");
    expect(screen.getAllByTestId("level-take_profit")[1].textContent).toContain("▲");
    expect(screen.getAllByRole("table")).toHaveLength(1);
  });
  it("no entry given: no entry row; no cost: P&L is a dash, never 0", () => {
    const r = levels();
    r.stop = { ...r.stop!, pnl_native: null, pnl_ils: null, pnl_usd: null };
    wrap(<LevelLadder r={r} />);
    expect(screen.queryByTestId("level-entry")).toBeNull();
    expect(screen.getByTestId("level-stop").textContent).toContain(en.exit.pnlUnknown);
  });
  it("Hebrew: Hebrew row labels", () => {
    wrap(<LevelLadder r={levels()} entry={55} />, "he");
    expect(screen.getByTestId("level-entry").textContent).toContain(he.exit.entryLevel);
    expect(screen.getByTestId("level-stop").textContent).toContain(he.exit.kind.stop);
  });
});

describe("MoreSections", () => {
  it("starts collapsed, toggles with the button (keyboard-activatable), wires aria-expanded/aria-controls", () => {
    wrap(<MoreSections items={[{ id: "a", title: "Alpha", body: <p>alpha body</p> }, { id: "b", title: "Beta", body: <p>beta body</p> }]} />);
    const a = screen.getByRole("button", { name: /Alpha/ });
    expect(a.tagName).toBe("BUTTON"); // native button: Enter and Space work
    expect(a).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("alpha body")).toBeNull();
    fireEvent.click(a);
    expect(a).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById(a.getAttribute("aria-controls")!)).toHaveTextContent("alpha body");
    expect(screen.queryByText("beta body")).toBeNull(); // sections toggle independently
    fireEvent.click(a);
    expect(screen.queryByText("alpha body")).toBeNull();
  });
});

describe("PriceChart level kinds", () => {
  it("draws entry/stop/target/price lines with their own labels and styles", () => {
    window.matchMedia ??= (() => ({ matches: false })) as unknown as typeof window.matchMedia;
    const bars = Array.from({ length: 5 }, (_, i) => ({ time: `2026-09-0${i + 1}`, open: 10, high: 11, low: 9, close: 10 + i / 10 }));
    render(
      <PriceChart
        bars={bars} marks={[]} labels={{ support: "S", resistance: "R", aria: "x", entry: "Entry", stop: "Stop", target: "Target", price: "Price" }}
        levels={[{ price: 8, kind: "stop" }, { price: 12, kind: "target", title: "TP1" }, { price: 10, kind: "entry" }, { price: 10.4, kind: "price" }]}
      />,
    );
    const by = Object.fromEntries(lines.price.map((l) => [l.title, l]));
    expect(Object.keys(by).sort()).toEqual(["Entry", "Price", "Stop", "TP1"]);
    expect(by.Stop.lineStyle).toBe(2);
    expect(by.Entry.lineStyle).toBe(1);
    expect(by.Price.lineStyle).toBe(0);
    expect(new Set(lines.price.map((l) => l.color)).size).toBeGreaterThan(2); // distinct colours per kind
  });
  it("legend lists the kinds drawn", () => {
    wrap(<LevelLegend kinds={["stop", "target", "entry", "price"]} labels={{ stop: "Stop", target: "Target", entry: "Entry", price: "Price" }} />);
    expect(screen.getByTestId("level-legend").querySelectorAll("li")).toHaveLength(4);
  });
});

describe("holding.* strings", () => {
  const keys = (o: unknown, p = ""): string[] => (o && typeof o === "object" ? Object.entries(o).flatMap(([k, v]) => keys(v, p ? `${p}.${k}` : k)) : [p]);
  it("he and en define the same analysts, chart, bars and more keys", () => {
    for (const blk of ["analysts", "chart", "bars", "more"] as const) expect(keys((he.holding as never)[blk]).sort()).toEqual(keys((en.holding as never)[blk]).sort());
    expect(keys(en.holding.analysts).length).toBeGreaterThan(15);
  });
});
