import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/dividends",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { api, ApiError } from "@/lib/api";
import { setMockDividendsState } from "@/lib/mock-funds";
import { DividendsPage } from "@/components/DividendsPage";
import { ActionsSheet } from "@/components/ActionsSheet";

const wrap = (locale: "en" | "he", ui: ReactNode) =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <div dir={locale === "he" ? "rtl" : "ltr"} data-testid="root">
        <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>{ui}</NextIntlClientProvider>
      </div>
    </SWRConfig>,
  );

const NO_ADVICE_EN = /\b(buy|sell|hold|recommend\w*|should|must|consider|outperform\w*)\b/i;
const NO_ADVICE_HE = /קנה|קנו|מכור|מכרו|מומלץ|המלצה|כדאי|עליכם|עליך|שקלו|שקול/;

describe("dividends page", () => {
  afterEach(() => { setMockDividendsState("full"); vi.restoreAllMocks(); });

  it("full: upcoming rows with ex date, pay date or 'not known', per share, holding amount, and an estimate mark", async () => {
    wrap("en", <DividendsPage />);
    const rows = await screen.findAllByTestId("upcoming-row");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("Apple");
    expect(rows[0]).toHaveTextContent("10/10/2026");
    expect(rows[0]).toHaveTextContent("16/10/2026");
    expect(rows[0]).toHaveTextContent("$0.26");
    expect(rows[0]).toHaveTextContent("$5.20"); // 0.26 x 20
    expect(within(rows[0]).queryByTestId("estimate-chip")).toBeNull();
    expect(rows[1]).toHaveTextContent("Payment date not known");
    expect(within(rows[1]).getByTestId("estimate-chip")).toHaveTextContent("Estimate");
    expect(within(rows[2]).getByTestId("estimate-chip")).toBeInTheDocument();
    expect(rows[2]).toHaveTextContent("₪810.00");
    expect(rows[2]).not.toHaveTextContent("about"); // ILS holding: no second conversion line
    expect(rows[0]).toHaveTextContent("about ₪19.24");
  });

  it("full: 12-month income is flagged as an estimate with its note and the symbols without data", async () => {
    wrap("en", <DividendsPage />);
    const income = await screen.findByTestId("income");
    expect(within(income).getByTestId("income-total")).toHaveTextContent("₪3,290.50");
    expect(income).toHaveTextContent("Estimate");
    expect(income).toHaveTextContent(en.dividends.incomeEstimateNote);
    expect(within(income).getByTestId("income-without")).toHaveTextContent("TEVA.TA, CHKP");
    expect(within(income).getByTestId("income-lines").children).toHaveLength(3);
  });

  it("full: every per-symbol status has its own honest wording", async () => {
    wrap("en", <DividendsPage />);
    await screen.findByTestId("income");
    const expectStatus = (sym: string, text: string, key: string) => {
      const row = screen.getByTestId(`status-${sym}`);
      expect(row).toHaveTextContent(text);
      expect(row.querySelector(`[data-status="${key}"]`)).not.toBeNull();
    };
    expectStatus("AAPL", "Data found", "ok");
    expectStatus("TEVA.TA", "No dividend data found", "no_data");
    expectStatus("NICE.TA", "Dividends appear to have stopped", "stopped");
    expectStatus("BTC-USD", "Not applicable to this type of holding", "not_applicable");
    expectStatus("CHKP", "The data source is not reachable right now", "unavailable");
    expectStatus("ESLT.TA", "The data source is busy, not checked yet", "rate_limited");
    expectStatus("ARNA.TA", "Not checked yet", "not_checked");
    expect(screen.getByTestId("status-AAPL")).toHaveTextContent("Last ex-date 11/08/2026, $0.25 per share");
    expect(screen.getByTestId("status-NICE.TA")).toHaveTextContent("₪1.10");
  });

  it("full: footer credit has the source, date and personal non-commercial terms", async () => {
    wrap("en", <DividendsPage />);
    const credit = await screen.findByTestId("dividends-credit");
    expect(credit).toHaveTextContent("Yahoo Finance");
    expect(credit).toHaveTextContent("as of 03/10/2026");
    expect(credit).toHaveTextContent(/non-commercial/i);
  });

  it("no data (null total): says 'No data', never a zero amount, lists the statuses", async () => {
    setMockDividendsState("no_data");
    wrap("en", <DividendsPage />);
    const income = await screen.findByTestId("income");
    expect(within(income).getByTestId("income-total")).toHaveTextContent("No data");
    expect(income.textContent ?? "").not.toMatch(/₪\s?0|\b0\.00/);
    expect(income).toHaveTextContent(en.dividends.incomeNoDataNote);
    expect(screen.getByTestId("upcoming-none")).toHaveTextContent("No upcoming dividends");
    expect(screen.getByTestId("status-SPY")).toHaveTextContent("No dividend data found");
    expect(screen.queryByTestId("upcoming-row")).toBeNull();
  });

  it("unavailable and rate limited: statuses are shown and the total is 'No data'", async () => {
    setMockDividendsState("unavailable");
    wrap("en", <DividendsPage />);
    expect(await screen.findByTestId("status-AAPL")).toHaveTextContent("not reachable");
    expect(screen.getByTestId("status-NVDA")).toHaveTextContent("busy");
    expect(screen.getByTestId("status-TEVA.TA")).toHaveTextContent("Not checked yet");
    expect(screen.getByTestId("income-total")).toHaveTextContent("No data");
  });

  it("an API error shows an alert, not an empty calendar", async () => {
    vi.spyOn(api, "dividends").mockRejectedValue(new ApiError(500, "x"));
    wrap("en", <DividendsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent(en.dividends.error);
    expect(screen.queryByTestId("income")).toBeNull();
  });

  it("Hebrew RTL: Hebrew copy, ltr dates and amounts, no advice wording", async () => {
    wrap("he", <DividendsPage />);
    const rows = await screen.findAllByTestId("upcoming-row");
    expect(screen.getByTestId("root")).toHaveAttribute("dir", "rtl");
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("לוח דיבידנדים");
    expect(rows[1]).toHaveTextContent("תאריך התשלום לא ידוע");
    expect(within(rows[1]).getByTestId("estimate-chip")).toHaveTextContent("הערכה");
    expect(screen.getByTestId("income")).toHaveTextContent("הערכה");
    expect(rows[0].querySelector("dd[dir=ltr]")).not.toBeNull();
    expect(screen.getByTestId("status-NICE.TA")).toHaveTextContent("נראה שהדיבידנדים הופסקו");
    expect(screen.getByTestId("dividends-credit")).toHaveTextContent("לשימוש אישי ולא מסחרי בלבד");
    expect(screen.getByTestId("root").textContent ?? "").not.toMatch(NO_ADVICE_HE);
  });

  it("Hebrew no data: 'אין נתונים', no zero", async () => {
    setMockDividendsState("no_data");
    wrap("he", <DividendsPage />);
    expect(await screen.findByTestId("income-total")).toHaveTextContent("אין נתונים");
    expect(screen.getByTestId("income").textContent ?? "").not.toMatch(/₪\s?0/);
  });

  it("English text has no buy/sell/recommend wording", async () => {
    wrap("en", <DividendsPage />);
    await screen.findByTestId("income");
    expect(screen.getByTestId("root").textContent ?? "").not.toMatch(NO_ADVICE_EN);
  });

  it("is reachable from the + menu", () => {
    wrap("en", <ActionsSheet onClose={vi.fn()} />);
    expect(screen.getByRole("link", { name: /Dividend calendar/ })).toHaveAttribute("href", "/dividends");
  });

  it("API is called with the portfolio id", async () => {
    const spy = vi.spyOn(api, "dividends");
    wrap("en", <DividendsPage />);
    await screen.findByTestId("income");
    expect(spy).toHaveBeenCalledWith(1);
  });
});
