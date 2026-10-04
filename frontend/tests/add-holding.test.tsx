import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");
vi.mock("lightweight-charts", () => {
  const series = { setData: vi.fn() };
  const chart = { addSeries: vi.fn(() => series), timeScale: () => ({ fitContent: vi.fn() }), remove: vi.fn() };
  return { createChart: vi.fn(() => chart), HistogramSeries: {}, LineSeries: {}, ColorType: { Solid: "solid" } };
});
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { api, ApiError, type Portfolio } from "@/lib/api";
import { AddHoldingForm } from "@/components/AddHoldingForm";
import { MainPage } from "@/components/MainPage";

const portfolios = [{ id: 1, name: "Main" }, { id: 2, name: "Second" }] as Portfolio[];
const wrap = (locale: "en" | "he", ui: ReactNode) =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>{ui}</NextIntlClientProvider>
    </SWRConfig>,
  );
const type = (label: string, value: string) => fireEvent.change(screen.getByLabelText(new RegExp(label)), { target: { value } });
const submit = () => fireEvent.click(screen.getByRole("button", { name: "Add holding" }));

describe("add a holding manually", () => {
  afterEach(() => vi.restoreAllMocks());

  it("sends the symbol upper-cased, a locale number, and a NULL horizon unless the user picks one (no default)", async () => {
    const add = vi.spyOn(api, "addHolding").mockResolvedValue({} as never);
    const onClose = vi.fn();
    wrap("en", <AddHoldingForm portfolios={portfolios} portfolioId={2} onClose={onClose} />);
    type("^Symbol", " amzn ");
    type("^Quantity", "1,5");
    submit();
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(add).toHaveBeenCalledWith(2, { symbol: "AMZN", quantity: 1.5, avg_cost: null, cost_currency: null, horizon: null });
  });

  it("sends cost, cost currency and the chosen horizon", async () => {
    const add = vi.spyOn(api, "addHolding").mockResolvedValue({} as never);
    wrap("en", <AddHoldingForm portfolios={portfolios} onClose={vi.fn()} />);
    type("^Symbol", "TEVA.TA");
    type("^Quantity", "100");
    type("Average cost", "51.2");
    type("Cost currency", "ILS");
    type("Holding period", "3m");
    submit();
    await waitFor(() => expect(add).toHaveBeenCalled());
    expect(add).toHaveBeenCalledWith(1, { symbol: "TEVA.TA", quantity: 100, avg_cost: 51.2, cost_currency: "ILS", horizon: "3m" });
    expect((screen.getByLabelText(/Holding period/) as HTMLSelectElement).options[0].textContent).toBe("Decide later");
  });

  it("refuses a bad symbol, a zero quantity and an unreadable cost without calling the API", () => {
    const add = vi.spyOn(api, "addHolding");
    wrap("en", <AddHoldingForm portfolios={portfolios} onClose={vi.fn()} />);
    type("^Symbol", "bad symbol!");
    type("^Quantity", "5");
    submit();
    expect(screen.getByRole("alert")).toHaveTextContent(/symbol is not valid/);
    type("^Symbol", "AAPL");
    type("^Quantity", "0");
    submit();
    expect(screen.getByRole("alert")).toHaveTextContent(/quantity above zero/);
    type("^Quantity", "2");
    type("Average cost", "abc");
    submit();
    expect(screen.getByRole("alert")).toHaveTextContent(/cost is not a valid number/);
    expect(add).not.toHaveBeenCalled();
  });

  it("409 says the holding already exists; 422 and 429 get their own messages (he too)", async () => {
    const add = vi.spyOn(api, "addHolding").mockRejectedValueOnce(new ApiError(409, "dup"))
      .mockRejectedValueOnce(new ApiError(429, "slow", 5))
      .mockRejectedValueOnce(new ApiError(422, "x", undefined, { detail: [{ loc: ["body", "quantity"], msg: "bad" }] }));
    wrap("en", <AddHoldingForm portfolios={portfolios} onClose={vi.fn()} />);
    type("^Symbol", "AAPL");
    type("^Quantity", "2");
    submit();
    expect(await screen.findByRole("alert")).toHaveTextContent(/already in the portfolio/);
    submit();
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/Too many additions/));
    submit();
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/quantity above zero/));
    expect(add).toHaveBeenCalledTimes(3);
    expect(he.addHolding.error.duplicate).toBeTruthy();
  });

  it("main page: the button opens the form and creates the holding (mock mode)", async () => {
    wrap("en", <MainPage />);
    fireEvent.click(await screen.findByRole("button", { name: "Add a holding manually" }));
    type("^Symbol", "MSFT");
    type("^Quantity", "4");
    submit();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    // (the page refetches through SWR; the mock store is what proves the holding was created)
    expect((await api.holdings(1)).some((h) => h.symbol === "MSFT" && h.quantity === 4 && h.horizon === null)).toBe(true);
  });
});
