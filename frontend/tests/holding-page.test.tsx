import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/holding",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { api, type ScoreCardDetail } from "@/lib/api";
import { HoldingPage } from "@/components/HoldingPage";

const render1 = (id: number, locale: "en" | "he" = "en") =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}><HoldingPage id={id} /></NextIntlClientProvider>
    </SWRConfig>,
  );

/** What the real backend returns for a security with no price history: weights are PERCENTS (0..100), confidence 0..1. */
const realNoData = (): ScoreCardDetail => {
  const sig = (name: string, nominal: number) => ({
    name, score: 0, confidence: 0, weight: 0, nominal_weight: nominal, reasons: ["Not available yet (planned for Phase 2)."],
    data_as_of: "2026-10-03T15:16:29Z", explanation: { version: 1, summary: "Not available yet", inputs: {}, rules_applied: [] },
  });
  return {
    holding_id: 3, portfolio_id: 2, symbol: "NICE.TA", name_en: "NICE Ltd", name_he: "נייס", horizon: null, total: 0, confidence: 0,
    available: false, validated: false, disclaimer: "Not financial advice.",
    signals: [sig("technical", 25), sig("patterns", 10), sig("fundamentals", 20), sig("analysts", 20), sig("geo_news", 12.5), sig("sentiment", 12.5)],
    explanation: { version: 1, summary: "No signal has data for this security yet.", inputs: {}, rules_applied: [] },
  } as ScoreCardDetail;
};

describe("holding page", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the not-validated label, no verdict, and signal weights as percents", async () => {
    render1(1);
    expect(await screen.findByText("Not yet validated")).toBeInTheDocument();
    expect(screen.queryByText(/Weight 67%/)).toBeNull(); // weights live behind the collapsed Why?
    fireEvent.click(screen.getAllByRole("button", { name: "Why?" })[0]);
    expect(screen.getByText(/Weight 67%/)).toBeInTheDocument();
    // no verdict anywhere from the app (the brand's "Hold" half is not one; the analysts' own category names sit inside the analyst card)
    expect(screen.queryByText(/^(strong )?(buy|sell|hold)$/i, { ignore: "script, style, [data-wordmark], [data-testid=analyst-view] *" })).toBeNull();
  });

  it("real-backend no-data card: nominal weights read 25%, not 2,500%, and every signal has a translated name", async () => {
    vi.spyOn(api, "scorecard").mockResolvedValue(realNoData());
    render1(3);
    expect(await screen.findByText("No score data yet for this security.")).toBeInTheDocument();
    fireEvent.click(screen.getAllByRole("button", { name: "Why?" })[0]);
    const text = document.body.textContent ?? "";
    expect(text).toContain("nominal 25%");
    expect(text).not.toMatch(/\d,\d{3}%/);
    for (const raw of ["fundamentals", "analysts", "geo_news", "sentiment"]) expect(screen.queryByText(raw)).toBeNull();
    expect(screen.getAllByText("Analyst consensus").length).toBeGreaterThan(0);
    // missing data is grey "No data", never a neutral 0 bar
    const bar = screen.getByTestId("bar-analysts");
    expect(bar).toHaveAttribute("data-nodata", "1");
    expect(bar.textContent).toContain("No data");
  });

  it("shows the exit levels on the holding page (levels) and asks for a period when there is none (needs_horizon)", async () => {
    const a = render1(1);
    expect(await screen.findByTestId("level-stop")).toBeInTheDocument();
    a.unmount();
    render1(2); // LUMI.TA has no holding period
    expect(await screen.findByText("Choose a holding period first")).toBeInTheDocument();
  });

  it("is laid out as header+chart, score+analysts, plan, then collapsed settings", async () => {
    render1(1);
    const pos = await screen.findByTestId("holding-position");
    const bars = await screen.findByTestId("score-bars");
    const ladder = await screen.findByTestId("level-ladder");
    const alerts = await screen.findByRole("button", { name: en.holding.alertsTitle });
    const order = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING);
    expect(order(pos, bars)).toBe(true);
    expect(order(bars, ladder)).toBe(true);
    expect(order(ladder, alerts)).toBe(true);
    expect(await screen.findByTestId("analyst-view")).toBeInTheDocument();
    expect(screen.getByTestId("scale-plan")).toBeInTheDocument();
    // the duplicated scale-out list is gone: no "Scale-out plan" list heading, only the single plan bar
    expect(screen.queryByText(en.exit.scaleTitle)).toBeNull();
    // collapsed by default
    expect(alerts).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByLabelText(en.holding.alertPrice)).toBeNull();
    expect(screen.queryByTestId("plan-rules")).toBeNull();
    expect(screen.queryByRole("button", { name: en.holdingEdit.edit })).toBeNull();
    fireEvent.click(alerts);
    expect(screen.getByLabelText(en.holding.alertPrice)).toBeInTheDocument();
    expect(screen.getAllByText(/Not financial advice/).length).toBeGreaterThan(0);
  });

  it("Edit and Remove sit in the 'more' menu", async () => {
    render1(1);
    const more = await screen.findByRole("button", { name: /More actions for/ });
    expect(more).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(more);
    const menu = document.getElementById(more.getAttribute("aria-controls")!)!;
    expect(within(menu).getByRole("button", { name: /^Edit / })).toBeInTheDocument();
    expect(within(menu).getByRole("button", { name: /^Remove / })).toBeInTheDocument();
  });

  it("shows the analysts' ratings with an as-of date, or 'no analyst coverage'", async () => {
    const a = render1(5); // NVDA
    const card = await screen.findByTestId("analyst-view");
    expect(await within(card).findByTestId("analyst-bar")).toBeInTheDocument();
    expect(card.textContent).toMatch(/29 analysts/);
    expect(card.textContent).toMatch(/As of/);
    a.unmount();
    render1(1); // TEVA.TA: no coverage in the mock
    const none = await screen.findByTestId("analyst-no-coverage");
    expect(none.textContent).toContain(en.holding.analysts.noCoverage);
  });

  it("Hebrew: same page, Hebrew analyst strings", async () => {
    render1(5, "he");
    expect(await screen.findByText(he.holding.scoreCard)).toBeInTheDocument();
    const card = await screen.findByTestId("analyst-view");
    expect(await within(card).findByText(he.holding.analysts.cat.strong_buy)).toBeInTheDocument();
    expect(card.textContent).toContain(he.holding.analysts.ownNote);
  });
});
