import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { api, type Holding, type Portfolio } from "@/lib/api";
import { AddHoldingForm } from "@/components/AddHoldingForm";
import { ExitLevelsPanel } from "@/components/ExitLevelsPanel";
import { HoldingCard } from "@/components/HoldingsList";
import { mockRequest } from "@/lib/mock";

const portfolios = [{ id: 1, name: "Main" }, { id: 2, name: "Second" }] as Portfolio[];
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

const strings = (o: unknown): string[] => (typeof o === "string" ? [o] : o && typeof o === "object" ? Object.values(o).flatMap(strings) : []);

const openFund = () => fireEvent.click(screen.getByRole("button", { name: /Add a fund|הוספת קופה/ }));
const search = (q: string) => {
  fireEvent.change(screen.getByRole("textbox", { name: /Fund name or number|שם או מספר קופה/ }), { target: { value: q } });
  fireEvent.submit(screen.getByRole("search"));
};

describe("fund wording", () => {
  it("has no buy/sell/recommend wording in either language", () => {
    for (const ns of ["funds", "dividends"] as const) {
      for (const s of strings(en[ns])) expect(s, s).not.toMatch(NO_ADVICE_EN);
      for (const s of strings(he[ns])) expect(s, s).not.toMatch(NO_ADVICE_HE);
    }
    const fundKeys = [en.exit.fundNoLevelsNote, en.exit.reason.fund_no_levels, en.holdings.fundChip, en.holdings.manualChip, en.holdings.costOnlyChip, en.holdings.noValueChip, en.holdings.fundStaleHint, en.holdings.manualAsOf, en.holdings.fundCostOnly, en.holdings.fundNoValue, ...strings(en.addHolding)];
    for (const s of fundKeys) expect(s, s).not.toMatch(NO_ADVICE_EN);
    for (const s of [he.exit.fundNoLevelsNote, he.exit.reason.fund_no_levels, he.holdings.fundChip, he.holdings.manualChip, he.holdings.costOnlyChip, he.holdings.noValueChip, he.holdings.fundStaleHint, he.holdings.manualAsOf, he.holdings.fundCostOnly, he.holdings.fundNoValue, he.addHolding.fundOpen, he.addHolding.fundTitle]) expect(s, s).not.toMatch(NO_ADVICE_HE);
  });
  it("he and en have the same keys", () => {
    const keys = (o: unknown, p = ""): string[] => (o && typeof o === "object" ? Object.entries(o).flatMap(([k, v]) => keys(v, `${p}${k}.`)) : [p]);
    expect(keys(he.funds).sort()).toEqual(keys(en.funds).sort());
    expect(keys(he.dividends).sort()).toEqual(keys(en.dividends).sort());
  });
});

describe("add a fund: search and returns", () => {
  afterEach(() => vi.restoreAllMocks());

  it("lists name, track and manager; tapping shows returns with the category average, credit line and no price", async () => {
    wrap("en", <AddHoldingForm portfolios={portfolios} portfolioId={2} onClose={vi.fn()} />);
    openFund();
    search("gemel");
    const list = await screen.findByTestId("fund-results");
    expect(within(list).getAllByRole("button")).toHaveLength(2);
    expect(list).toHaveTextContent("Kupat Gemel Equities Track");
    expect(list).toHaveTextContent("Equities");
    expect(list).toHaveTextContent("Alpha Provident");
    fireEvent.click(within(list).getByRole("button", { name: /Equities Track/ }));
    const detail = await screen.findByTestId("fund-detail");
    expect(within(detail).getByTestId("return-1m")).toHaveTextContent("+1.20%");
    expect(within(detail).getByTestId("cat-1m")).toHaveTextContent("0.80%");
    expect(within(detail).getByTestId("cat-1m")).toHaveTextContent("+0.40 pts");
    expect(within(detail).getByTestId("return-3y")).toHaveTextContent("8.70% a year");
    expect(within(detail).queryByTestId("fund-stale")).toBeNull();
    expect(within(detail).getByTestId("fund-no-price")).toHaveTextContent(/returns, not a unit price/);
    const credit = within(detail).getByTestId("fund-credit");
    expect(credit).toHaveTextContent("GemelNet");
    expect(credit).toHaveTextContent("data as of 02/10/2026");
    expect(credit).toHaveTextContent(/non-commercial/i);
  });

  it("stale data, a gap and too few months: honest wording, a stale badge and no category average", async () => {
    wrap("en", <AddHoldingForm portfolios={portfolios} portfolioId={2} onClose={vi.fn()} />);
    openFund();
    search("S&P");
    fireEvent.click(await screen.findByRole("button", { name: /S&P 500 Track/ }));
    const detail = await screen.findByTestId("fund-detail");
    expect(within(detail).getByTestId("fund-stale")).toHaveTextContent("2026-06");
    expect(within(detail).getByTestId("missing-1y")).toHaveTextContent("A month is missing in the series");
    expect(within(detail).getByTestId("missing-3y")).toHaveTextContent("Not enough months of data");
    expect(within(detail).getByTestId("no-category")).toBeInTheDocument();
    expect(within(detail).queryByText("Category average")).toBeNull();
    expect(within(detail).getByTestId("return-1m")).toHaveTextContent("-0.60%");
  });

  it.each([
    ["qqqq", "search-no_data", /No fund matched/, false],
    ["unavailable", "search-unavailable", /not reachable/, true],
    ["ratelimit", "search-rate_limited", /busy/, true],
  ])("data status for %s", async (q, id, text, manual) => {
    wrap("en", <AddHoldingForm portfolios={portfolios} portfolioId={2} onClose={vi.fn()} />);
    openFund();
    search(q);
    const box = await screen.findByTestId(id);
    expect(box).toHaveTextContent(text);
    expect(/still add a fund by hand/.test(box.textContent ?? "")).toBe(manual);
    expect(screen.queryByTestId("fund-results")).toBeNull();
  });
});

describe("add a fund: manual value", () => {
  afterEach(() => vi.restoreAllMocks());

  const toValueForm = async () => {
    wrap("en", <AddHoldingForm portfolios={portfolios} portfolioId={2} onClose={onClose} />);
    openFund();
    search("gemel");
    fireEvent.click(await screen.findByRole("button", { name: /Equities Track/ }));
    fireEvent.click(await screen.findByRole("button", { name: "Add this fund to my portfolio" }));
    return screen.findByTestId("fund-value-form");
  };
  const onClose = vi.fn();

  it("starts with an empty value and date (no default), explains the value is the user's, and validates", async () => {
    const add = vi.spyOn(api, "addHolding").mockResolvedValue({} as never);
    const form = await toValueForm();
    expect(within(form).getByLabelText("Value in shekels")).toHaveValue("");
    expect(within(form).getByLabelText("Value as of (date)")).toHaveValue("");
    expect(form).toHaveTextContent(/Nothing is filled in for you/);
    fireEvent.click(within(form).getByRole("button", { name: "Add fund" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("Enter a value above zero.");
    fireEvent.change(within(form).getByLabelText("Value in shekels"), { target: { value: "50,000" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add fund" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent(/Enter the date/);
    fireEvent.change(within(form).getByLabelText("Value as of (date)"), { target: { value: "2999-01-01" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add fund" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent(/cannot be in the future/);
    expect(add).not.toHaveBeenCalled();
  });

  it("sends GEMEL-<number>, quantity 1, the manual value and its date", async () => {
    const add = vi.spyOn(api, "addHolding").mockResolvedValue({} as never);
    const form = await toValueForm();
    fireEvent.change(within(form).getByLabelText("Value in shekels"), { target: { value: "50,000" } });
    fireEvent.change(within(form).getByLabelText("Value as of (date)"), { target: { value: "2026-09-30" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add fund" }));
    await waitFor(() => expect(add).toHaveBeenCalled());
    expect(add).toHaveBeenCalledWith(2, {
      symbol: "GEMEL-1001", quantity: 1, manual_value_ils: 50000, manual_value_as_of: "2026-09-30",
      fund_name: "Kupat Gemel Equities Track", track: "Equities",
    });
  });

  it("already in the portfolio (409) is explained", async () => {
    const { ApiError } = await import("@/lib/api");
    vi.spyOn(api, "addHolding").mockRejectedValue(new ApiError(409, "dup"));
    const form = await toValueForm();
    fireEvent.change(within(form).getByLabelText("Value in shekels"), { target: { value: "10" } });
    fireEvent.change(within(form).getByLabelText("Value as of (date)"), { target: { value: "2026-09-30" } });
    fireEvent.click(within(form).getByRole("button", { name: "Add fund" }));
    expect(await within(form).findByRole("alert")).toHaveTextContent("already in the portfolio");
  });
});

describe("fund flow in Hebrew (RTL)", () => {
  afterEach(() => vi.restoreAllMocks());
  it("renders Hebrew copy under dir=rtl, with ltr numbers and no advice wording", async () => {
    wrap("he", <AddHoldingForm portfolios={portfolios} portfolioId={2} onClose={vi.fn()} />);
    openFund();
    search("gemel");
    fireEvent.click(await screen.findByRole("button", { name: /Equities Track/ }));
    const detail = await screen.findByTestId("fund-detail");
    expect(screen.getByTestId("root")).toHaveAttribute("dir", "rtl");
    expect(detail).toHaveTextContent("תשואה");
    expect(detail).toHaveTextContent("ממוצע הקטגוריה");
    expect(within(detail).getByTestId("fund-credit")).toHaveTextContent("לשימוש אישי ולא מסחרי בלבד");
    expect(within(detail).getByTestId("fund-no-price")).toHaveTextContent("מחיר יחידה");
    expect(within(detail).getByTestId("return-1m").querySelector("[dir=ltr]")).not.toBeNull();
    expect(screen.getByTestId("fund-flow").textContent ?? "").not.toMatch(NO_ADVICE_HE);
  });
  it("Hebrew unavailable status says manual entry still works", async () => {
    wrap("he", <AddHoldingForm portfolios={portfolios} portfolioId={2} onClose={vi.fn()} />);
    openFund();
    search("unavailable");
    expect(await screen.findByTestId("search-unavailable")).toHaveTextContent("עדיין אפשר להוסיף קופה ידנית");
  });
});

/** Fund holdings come back from the mock exactly as the backend shapes them. */
async function fundHoldings(): Promise<Record<string, Holding>> {
  if (!(mockRequest("GET", "/portfolios/1/holdings") as Holding[]).some((h) => h.symbol === "GEMEL-2001")) for (const body of [
    { symbol: "GEMEL-2001", quantity: 1, manual_value_ils: 50000, manual_value_as_of: "2026-09-30", fund_name: "Fund A", track: "Equities" },
    { symbol: "GEMEL-2002", quantity: 1, avg_cost: 1000, cost_currency: "ILS", fund_name: "Fund B" },
    { symbol: "GEMEL-2003", quantity: 1, fund_name: "Fund C" },
  ]) mockRequest("POST", "/portfolios/1/holdings", body);
  const all = mockRequest("GET", "/portfolios/1/holdings") as Holding[];
  return Object.fromEntries(all.filter((h) => h.fund).map((h) => [h.symbol, h]));
}

describe("fund holdings in the list", () => {
  it.each(["en", "he"] as const)("manual value with its date and a stale chip (%s)", async (loc) => {
    const h = (await fundHoldings())["GEMEL-2001"];
    wrap(loc, <HoldingCard h={h} />);
    const v = screen.getByTestId("fund-value");
    expect(v).toHaveTextContent(loc === "en" ? "manual value as of 30/09/2026" : "שווי ידני נכון ל-30/09/2026");
    expect(v).toHaveTextContent(/50,000/);
    expect(screen.getByRole("list")).toHaveTextContent(loc === "en" ? "Stale price" : he.holdings.stale);
    expect(screen.queryByText(en.holdings.stopMissing)).toBeNull();
    expect(screen.queryByText(en.holdings.needsHorizon)).toBeNull();
  });
  it("cost only and no value: a dash and a plain chip, never a made-up value", async () => {
    const f = await fundHoldings();
    const { unmount } = wrap("en", <HoldingCard h={f["GEMEL-2002"]} />);
    expect(screen.getByTestId("fund-value")).toHaveTextContent("No value entered, only the cost");
    expect(screen.getByText("Cost only, no current value")).toBeInTheDocument();
    unmount();
    wrap("en", <HoldingCard h={f["GEMEL-2003"]} />);
    expect(screen.getByText("No value entered", { selector: "li" })).toBeInTheDocument();
    expect(screen.getByTestId("fund-value").textContent).not.toMatch(/₪\s?0|\b0\.00/);
  });
});

describe("exit levels panel for a fund", () => {
  it.each(["en", "he"] as const)("shows the fund_no_levels reason plainly and no numbers (%s)", async (loc) => {
    const f = (await fundHoldings())["GEMEL-2001"];
    wrap(loc, <ExitLevelsPanel holdingId={f.id} portfolioId={1} />);
    const msg = await screen.findByText(loc === "en" ? en.exit.noLevelsTitle : he.exit.noLevelsTitle).then((e) => e.parentElement as HTMLElement);
    expect(msg).toHaveTextContent(loc === "en" ? en.exit.reason.fund_no_levels : he.exit.reason.fund_no_levels);
    expect(msg).toHaveTextContent(loc === "en" ? en.exit.fundNoLevelsNote : he.exit.fundNoLevelsNote);
    expect(msg.textContent ?? "").not.toMatch(/\d/);
    expect(screen.queryByRole("radiogroup")).toBeNull();
    expect(screen.queryByText(/R:R/)).toBeNull();
  });
});
