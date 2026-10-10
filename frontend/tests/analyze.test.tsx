import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");
const chartCalls = vi.hoisted(() => ({ candles: [] as unknown[], lines: [] as unknown[] }));
vi.mock("lightweight-charts", () => {
  const mk = (kind: "candles" | "lines") => ({ setData: (d: unknown) => { chartCalls[kind].push(d); }, createPriceLine: vi.fn() });
  const chart = { addSeries: vi.fn((k: string) => (k === "candle" ? mk("candles") : mk("lines"))), timeScale: () => ({ fitContent: vi.fn() }), remove: vi.fn() };
  return { createChart: vi.fn(() => chart), createSeriesMarkers: vi.fn(), CandlestickSeries: "candle", HistogramSeries: "hist", LineSeries: "line", LineStyle: { Solid: 0, Dotted: 1, Dashed: 2 }, ColorType: { Solid: "solid" } };
});
const push = vi.fn();
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/analyze",
  useRouter: () => ({ replace: vi.fn(), push }),
}));
let symbolParam: string | null = null;
vi.mock("next/navigation", () => ({ useSearchParams: () => ({ get: () => symbolParam }) }));

import { api, ApiError, type AnalyzeOut } from "@/lib/api";
import { mockRequest } from "@/lib/mock";
import { resetMockLists } from "@/lib/mock-analyze";
import { AnalyzeHome } from "@/components/AnalyzeHome";
import { AnalyzeResult } from "@/components/AnalyzeResult";
import { AnalyzeRoute } from "@/components/AnalyzeRoute";

const wrap = (locale: "en" | "he", ui: ReactNode) =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>
        <div dir={locale === "he" ? "rtl" : "ltr"} data-testid="root">{ui}</div>
      </NextIntlClientProvider>
    </SWRConfig>,
  );
const an = (q: string) => mockRequest("GET", `/analyze/${q}`) as AnalyzeOut;
const NO_VERDICT = /\b(buy|sell|hold|recommend(ed|ation)?)\b/i;
/** Page text without the analysts' own rating categories (their names are the analysts', shown as reported, not the app's wording). */
const bodyText = () => {
  const c = document.body.cloneNode(true) as HTMLElement;
  c.querySelectorAll("[data-testid=analyst-view]").forEach((e) => e.remove());
  return c.textContent ?? "";
};
const openAsk = async (locale: "en" | "he" = "en") => {
  const m = locale === "en" ? en : he;
  fireEvent.click(await screen.findByRole("button", { name: m.analyze.askTitle }));
};
const openSignals = async (locale: "en" | "he" = "en") => {
  const m = locale === "en" ? en : he;
  const region = await screen.findByRole("region", { name: m.analyze.signalsTitle });
  fireEvent.click(within(region).getByRole("button", { name: m.exit.why }));
};

beforeEach(() => { chartCalls.candles.length = 0; window.matchMedia ??= (() => ({ matches: false })) as unknown as typeof window.matchMedia; resetMockLists(); push.mockClear(); symbolParam = null; });
afterEach(() => vi.restoreAllMocks());

async function fillForm(opts: { pf?: string; amount: string; cur: string; horizon?: string }) {
  const form = await screen.findByRole("form", { name: en.analyze.fitTitle });
  if (opts.pf) fireEvent.click(await within(form).findByRole("radio", { name: opts.pf }));
  fireEvent.change(within(form).getByLabelText(en.analyze.formAmount), { target: { value: opts.amount } });
  fireEvent.click(within(form).getByRole("radio", { name: opts.cur }));
  if (opts.horizon) fireEvent.click(within(form).getByRole("radio", { name: opts.horizon }));
  fireEvent.click(within(form).getByRole("button", { name: en.analyze.formApply }));
}

describe("analyze home", () => {
  it("shows recent searches and the watchlist with cached price and freshness", async () => {
    wrap("en", <AnalyzeHome />);
    const recent = await screen.findByRole("region", { name: en.analyze.recentTitle });
    expect(await within(recent).findByRole("link", { name: "Analyze AMD" })).toHaveAttribute("href", "/analyze?symbol=AMD");
    const watch = screen.getByRole("region", { name: en.analyze.watchTitle });
    expect(await within(watch).findByText("NVDA")).toBeInTheDocument();
    expect(watch.textContent).toContain(en.analyze.fresh);
    expect(watch.textContent).toContain(en.analyze.lastClose); // ARNA.TA: a last close, labelled
    expect(watch.textContent).toContain(en.analyze.stale);
    expect(watch.textContent).toContain(en.analyze.noQuote); // XOM: nothing cached yet
    expect(bodyText()).not.toMatch(NO_VERDICT);
  });

  it("removes one recent search, then clears all", async () => {
    wrap("en", <AnalyzeHome />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove AMD from recent searches" }));
    await waitFor(() => expect(screen.queryByRole("link", { name: "Analyze AMD" })).toBeNull());
    expect(screen.getByRole("link", { name: "Analyze TEVA.TA" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: en.analyze.clearRecent }));
    expect(await screen.findByText(en.analyze.recentNone)).toBeInTheDocument();
  });

  it("removes a watchlist entry", async () => {
    wrap("en", <AnalyzeHome />);
    fireEvent.click(await screen.findByRole("button", { name: "Remove NVDA from the watchlist" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Remove NVDA from the watchlist" })).toBeNull());
  });

  it("searches by name, lists matches, and opens a typed ticker", async () => {
    wrap("en", <AnalyzeHome />);
    const box = screen.getByLabelText(en.analyze.searchLabel);
    fireEvent.change(box, { target: { value: "teva" } });
    expect(await screen.findByRole("link", { name: "Analyze TEVA.TA" }, { timeout: 2000 })).toBeInTheDocument();
    fireEvent.change(box, { target: { value: "amd" } });
    fireEvent.submit(box.closest("form")!);
    expect(push).toHaveBeenCalledWith("/analyze?symbol=AMD");
  });

  it("rejects something that is not a ticker", () => {
    wrap("en", <AnalyzeHome />);
    const box = screen.getByLabelText(en.analyze.searchLabel);
    fireEvent.change(box, { target: { value: "bad symbol!" } });
    fireEvent.submit(box.closest("form")!);
    expect(screen.getByRole("alert")).toHaveTextContent(en.analyze.invalidSymbol);
    expect(push).not.toHaveBeenCalled();
  });

  it("renders in Hebrew inside an RTL container", async () => {
    wrap("he", <AnalyzeHome />);
    expect(screen.getByTestId("root")).toHaveAttribute("dir", "rtl");
    expect(await screen.findByRole("heading", { name: he.analyze.recentTitle })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: he.analyze.watchTitle })).toBeInTheDocument();
    expect((await screen.findAllByText("NVDA"))[0].closest("[dir=ltr]")).not.toBeNull(); // tickers stay LTR
  });
});

describe("analyze route", () => {
  it("shows the search without a symbol and the analysis with ?symbol=", async () => {
    wrap("en", <AnalyzeRoute />);
    expect(await screen.findByLabelText(en.analyze.searchLabel)).toBeInTheDocument();
  });
  it("reads the symbol from the query", async () => {
    symbolParam = "amd";
    wrap("en", <AnalyzeRoute />);
    expect(await screen.findByTestId("fit-section")).toBeInTheDocument();
  });
});

describe("analyze result", () => {
  it("incomplete: headline first, form with nothing preselected, no verdict wording", async () => {
    wrap("en", <AnalyzeResult symbol="AMD" />);
    const fit = await screen.findByTestId("fit-section");
    expect(fit).toHaveAttribute("data-status", "incomplete");
    // chart/summary card first, then the score and analysts, then the fit section
    const regions = screen.getAllByRole("region");
    expect(regions.indexOf(screen.getByRole("region", { name: en.analyze.signalsTitle }))).toBeLessThan(regions.indexOf(fit));
    expect(screen.getByTestId("top-summary")).toBeInTheDocument();
    expect(within(fit).getByRole("heading", { name: en.analyze.fitTitle })).toBeInTheDocument();
    expect(fit.textContent).toContain(en.analyze.needsTitle);
    for (const k of ["portfolio_id", "amount", "currency", "horizon"] as const) expect(fit.textContent).toContain(en.analyze.needs[k]);
    const form = within(fit).getByRole("form");
    for (const r of within(form).getAllByRole("radio")) expect(r).toHaveAttribute("aria-checked", "false");
    expect(within(form).getByLabelText(en.analyze.formAmount)).toHaveValue("");
    expect(screen.queryByTestId("max-size")).toBeNull();
    expect(screen.getByTestId("gate-notice").textContent).toContain(en.analyze.gateTitle);
    expect(bodyText()).not.toMatch(NO_VERDICT);
  });

  it("rejects an empty form without calling the API again", async () => {
    const spy = vi.spyOn(api, "analyze");
    wrap("en", <AnalyzeResult symbol="AMD" />);
    const form = await screen.findByRole("form", { name: en.analyze.fitTitle });
    fireEvent.click(within(form).getByRole("button", { name: en.analyze.formApply }));
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("fits: max size, exposure before/after, levels, suggested size, new position", async () => {
    wrap("en", <AnalyzeResult symbol="XOM" />);
    await fillForm({ pf: "התיק הראשי", amount: "2000", cur: "$ USD", horizon: "3 months" });
    await waitFor(() => expect(screen.getByTestId("fit-section")).toHaveAttribute("data-status", "fits"));
    const fit = screen.getByTestId("fit-section");
    expect(within(fit).getByTestId("fit-status")).toHaveTextContent(en.analyze.fit.fits);
    expect(within(fit).getByTestId("max-size").textContent).toContain("₪");
    for (const d of ["position", "sector", "country"]) expect(within(fit).getByTestId(`exposure-${d}`)).toBeInTheDocument();
    expect(within(fit).getAllByText(en.analyze.withinCap)).toHaveLength(3);
    expect(within(fit).getByTestId("held")).toHaveTextContent(en.analyze.heldNew);
    expect(within(fit).getByTestId("level-stop")).toBeInTheDocument();
    expect(within(fit).getByTestId("level-ladder")).toBeInTheDocument();
    expect(within(fit).getByTestId("level-entry")).toBeInTheDocument(); // the entry price is on the ladder
    expect(within(fit).getAllByTestId("level-take_profit").length).toBeGreaterThan(0);
    expect(within(fit).getByText(en.analyze.suggested)).toBeInTheDocument();
    expect(within(fit).getByText(new RegExp(en.analyze.entry.replace("{price}", ".*")))).toBeInTheDocument();
    expect(bodyText()).not.toMatch(NO_VERDICT);
  });

  it("fits smaller: names the cap and a smaller maximum", async () => {
    wrap("en", <AnalyzeResult symbol="XOM" />);
    await fillForm({ pf: "התיק הראשי", amount: "60000", cur: "$ USD", horizon: "3 months" });
    await waitFor(() => expect(screen.getByTestId("fit-section")).toHaveAttribute("data-status", "fits_smaller"));
    const fit = screen.getByTestId("fit-section");
    expect(fit.textContent).toContain(en.analyze.fit.fits_smaller);
    expect(fit.textContent).toContain(en.analyze.breaks);
    expect(fit.textContent).toMatch(/Caps broken at the requested amount: position size/);
  });

  it("does not fit: names the sector cap that breaks and offers no size", async () => {
    wrap("en", <AnalyzeResult symbol="AMD" />);
    await fillForm({ pf: "התיק הראשי", amount: "2000", cur: "$ USD", horizon: "3 months" });
    await waitFor(() => expect(screen.getByTestId("fit-section")).toHaveAttribute("data-status", "does_not_fit"));
    const fit = screen.getByTestId("fit-section");
    expect(within(fit).getByTestId("exposure-sector")).toHaveTextContent(en.analyze.breaks);
    expect(within(fit).getByTestId("max-size")).toHaveTextContent(en.analyze.maxSizeNone);
    expect(fit.textContent).toMatch(/Caps broken at the requested amount: sector/);
  });

  it("held: shows the existing position and takes the holding's period (no horizon asked)", async () => {
    vi.spyOn(api, "analyze").mockResolvedValue(an("NVDA?portfolio_id=1&amount=5000&currency=ILS"));
    wrap("en", <AnalyzeResult symbol="NVDA" />);
    const held = await screen.findByTestId("held");
    expect(held.textContent).toContain(en.analyze.held);
    expect(held.textContent).toMatch(/You own 30 units/);
    expect(screen.getByTestId("fit-section").textContent).not.toContain(en.analyze.needsTitle);
  });

  it("stale price and no levels: says so, shows no numbers, lists what is not available", async () => {
    vi.spyOn(api, "analyze").mockResolvedValue(an("ARNA.TA?portfolio_id=1&amount=500&currency=ILS&horizon=1m"));
    wrap("en", <AnalyzeResult symbol="ARNA.TA" />);
    const price = await screen.findByTestId("price");
    expect(price.textContent).toMatch(/Not a live price/);
    expect(screen.queryByTestId("level-stop")).toBeNull();
    expect(screen.getByText(en.exit.noLevelsTitle)).toBeInTheDocument();
    expect(screen.queryByTestId("not-available")).toBeNull(); // collapsed
    fireEvent.click(screen.getByRole("button", { name: en.analyze.missingTitle }));
    const na = screen.getByTestId("not-available");
    expect(na.textContent).toContain("No analyst coverage found.");
    expect(screen.getByTestId("score-line")).toHaveTextContent(en.analyze.scoreNone);
    expect(screen.getByTestId("bar-trend").textContent).toContain(en.holding.bars.noData);
    await openSignals();
    expect(screen.getByTestId("signal-trend").textContent).toContain(en.analyze.noData);
  });

  it("signal breakdown, chart levels and a Why? from the Explanation", async () => {
    wrap("en", <AnalyzeResult symbol="XOM" />);
    await screen.findByTestId("bar-trend");
    await openSignals();
    expect(screen.getByTestId("signal-trend")).toBeInTheDocument();
    expect(screen.queryByTestId("chart-level-support")).toBeNull(); // collapsed
    fireEvent.click(screen.getByRole("button", { name: en.analyze.chartTitle }));
    expect(screen.getByTestId("chart-level-support")).toBeInTheDocument();
    expect(screen.getByTestId("chart-level-resistance")).toBeInTheDocument();
    const chart = screen.getByTestId("more-chart");
    fireEvent.click(within(chart).getByRole("button", { name: en.exit.why }));
    expect(within(chart).getByTestId("explanation")).toBeInTheDocument();
    const fit = screen.getByTestId("fit-section");
    fireEvent.click(within(fit).getAllByRole("button", { name: en.exit.why }).at(-1)!);
    expect(within(fit).getAllByTestId("explanation").length).toBeGreaterThan(0);
  });

  it("not found (404) shows a clear message", async () => {
    wrap("en", <AnalyzeResult symbol="NOPE" />);
    const nf = await screen.findByTestId("not-found");
    expect(nf.textContent).toContain("NOPE");
    expect(nf).toHaveAttribute("role", "alert");
  });

  it("another API error offers a retry", async () => {
    vi.spyOn(api, "analyze").mockRejectedValue(new ApiError(500, "boom"));
    wrap("en", <AnalyzeResult symbol="AMD" />);
    expect(await screen.findByText(en.analyze.error)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: en.analyze.retry })).toBeInTheDocument();
  });

  it("the star adds and removes the stock from the watchlist", async () => {
    wrap("en", <AnalyzeResult symbol="AMD" />);
    fireEvent.click(await screen.findByRole("button", { name: "Add AMD to the watchlist" }));
    const on = await screen.findByRole("button", { name: /AMD is on your watchlist/ });
    expect(on).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(on);
    expect(await screen.findByRole("button", { name: "Add AMD to the watchlist" })).toHaveAttribute("aria-pressed", "false");
  });

  it("saves nothing in browser storage (no results, no notes)", async () => {
    const set = vi.spyOn(Storage.prototype, "setItem");
    wrap("en", <AnalyzeResult symbol="XOM" />);
    await screen.findByTestId("fit-section");
    await openAsk();
    fireEvent.change(screen.getByLabelText(en.analyze.askNotes), { target: { value: "my private note" } });
    expect(set).not.toHaveBeenCalled();
  });
});

describe("ask box", () => {
  it("answers from the data with a template label and what it is grounded in", async () => {
    wrap("en", <AnalyzeResult symbol="AMD" />);
    await openAsk();
    fireEvent.change(await screen.findByLabelText(en.analyze.askLabel), { target: { value: "What is the trend?" } });
    fireEvent.click(screen.getByRole("button", { name: en.analyze.askSend }));
    const ans = await screen.findByTestId("ask-answer");
    expect(ans.textContent).toContain(en.analyze.askTemplate);
    expect(ans.textContent).toContain("Based on:");
    expect(screen.getByText(en.analyze.askNote)).toBeInTheDocument(); // says plainly: computed, nothing saved
  });

  it("a question asking for a trade instruction is declined and answered with facts", async () => {
    wrap("en", <AnalyzeResult symbol="AMD" />);
    await openAsk();
    fireEvent.change(await screen.findByLabelText(en.analyze.askLabel), { target: { value: "Should I buy it?" } });
    fireEvent.click(screen.getByRole("button", { name: en.analyze.askSend }));
    const ans = await screen.findByTestId("ask-answer");
    expect(ans.textContent).toContain(en.analyze.askDeclined);
  });

  it("empty question and an API error are handled", async () => {
    wrap("en", <AnalyzeResult symbol="AMD" />);
    await openAsk();
    const send = await screen.findByRole("button", { name: en.analyze.askSend });
    fireEvent.click(send);
    expect(screen.getByText(en.analyze.askEmpty)).toBeInTheDocument();
    vi.spyOn(api, "askAboutStock").mockRejectedValue(new ApiError(500, "x"));
    fireEvent.change(screen.getByLabelText(en.analyze.askLabel), { target: { value: "q" } });
    fireEvent.click(send);
    expect(await screen.findByText(en.analyze.askError)).toBeInTheDocument();
  });
});

describe("analyze result in Hebrew (RTL)", () => {
  it("renders the headline, statuses and numbers with LTR islands, and no verdict wording", async () => {
    vi.spyOn(api, "analyze").mockResolvedValue(an("AMD?portfolio_id=1&amount=2000&currency=USD&horizon=3m"));
    wrap("he", <AnalyzeResult symbol="AMD" />);
    const fit = await screen.findByTestId("fit-section");
    expect(screen.getByTestId("root")).toHaveAttribute("dir", "rtl");
    expect(within(fit).getByRole("heading", { name: he.analyze.fitTitle })).toBeInTheDocument();
    expect(within(fit).getByTestId("fit-status")).toHaveTextContent(he.analyze.fit.does_not_fit);
    expect(within(fit).getByTestId("max-size").closest("[dir]")).not.toBeNull();
    expect(screen.getByTestId("gate-notice").textContent).toContain(he.analyze.gateTitle);
    expect(screen.getByRole("button", { name: he.analyze.askTitle })).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByRole("region", { name: he.analyze.signalsTitle })).toBeInTheDocument();
    expect(bodyText()).not.toMatch(/קנה|מכור|מומלץ|המלצה/);
  });
});

describe("entries to the analyze screen", () => {
  it("the tab bar and the + menu both open /analyze (the main page uses the tab bar, per docs/ui-decisions.md)", async () => {
    const { TabBar } = await import("@/components/AppShell");
    const { ActionsSheet } = await import("@/components/ActionsSheet");
    wrap("he", <><TabBar /><ActionsSheet onClose={() => {}} /></>);
    expect(screen.getByRole("link", { name: he.nav.analyze })).toHaveAttribute("href", "/analyze");
    const menu = screen.getByRole("dialog", { name: he.actions.menuTitle });
    expect(within(menu).getByRole("link", { name: new RegExp(he.actions.analyze) })).toHaveAttribute("href", "/analyze");
  });

  it("draws the candles, and shows the gate as one short line with details behind a toggle", async () => {
    wrap("en", <AnalyzeResult symbol="XOM" />);
    await screen.findByTestId("bar-trend");
    expect(screen.getByTestId("price-chart")).toHaveAttribute("dir", "ltr");
    expect((chartCalls.candles[0] as unknown[]).length).toBe(120);
    expect(screen.getByTestId("level-legend")).toBeInTheDocument();
    expect(screen.queryByLabelText(en.analyze.indicators)).toBeNull(); // raw numbers only inside Why?
    const gate = screen.getByTestId("gate-notice");
    expect(screen.getByTestId("gate-progress").textContent).toBe("Backtest: not yet · Weeks 0/4 · Calls 0/50");
    expect(gate.textContent).not.toContain("Paper trading has run");
    fireEvent.click(within(gate).getByRole("button", { name: en.reason.gateDetails }));
    expect(gate.textContent).toContain("Paper trading has run 0.0 of 4 weeks");
  });

  it("Hebrew: reasons, gate and chart annotations are Hebrew; at most 2 reasons per signal until more", async () => {
    wrap("he", <AnalyzeResult symbol="XOM" />);
    await screen.findByTestId("bar-trend");
    await openSignals("he");
    const trend = await screen.findByTestId("signal-trend");
    expect(trend.querySelectorAll("li").length).toBe(2);
    expect(trend.textContent).toContain("המחיר גבוה ב-3.2% מהממוצע של 50 הימים האחרונים.");
    fireEvent.click(within(trend).getByRole("button", { name: he.reason.more.replace("{n}", "1") }));
    expect(trend.querySelectorAll("li").length).toBe(3);
    const gate = screen.getByTestId("gate-notice");
    fireEvent.click(within(gate).getByRole("button", { name: he.reason.gateDetails }));
    expect(gate.textContent).toContain("המסחר הדמיוני רץ 0.0 מתוך 4 שבועות");
    fireEvent.click(screen.getByRole("button", { name: he.analyze.chartTitle }));
    expect(screen.getByTestId("more-chart").textContent).toContain("צלב זהב");
    expect(screen.getByTestId("root").textContent).not.toMatch(/Paper trading|Price is|RSI is|touched/);
  });
});
