import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";
import type { Holding } from "@/lib/api";

vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { HoldingsList } from "@/components/HoldingsList";
import { HoldingActions } from "@/components/HoldingActions";

const wrap = (locale: "en" | "he", ui: ReactNode) =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>{ui}</NextIntlClientProvider>
    </SWRConfig>,
  );
const holding = (over: Partial<Holding>): Holding => ({
  id: 1, symbol: "AAPL", name_en: "Apple", name_he: "אפל", asset_type: "stock", market: "US", quantity: 10,
  price: 200, currency: "USD", day_change_pct: 1, value_ils: 7000, value_native: 2000, pnl: { ils: 1750, usd: 500, pct: 33.33 },
  pnl_native: 500, check_numbers: false, weight_pct: 50, horizon: null, stop_tp_status: "needs_horizon",
  score_card: { total: 10, technical: 10, patterns: 10, confidence: 0.7 }, price_stale: false, price_basis: "live", price_is_fresh: true, ...over,
});

describe("holding cards: position value, quantity x price, P&L in the same currency", () => {
  it("a US card leads with the position value in dollars and P&L in dollars", () => {
    wrap("en", <HoldingsList holdings={[holding({})]} />);
    expect(screen.getByTestId("position-value")).toHaveTextContent("$2,000.00");
    expect(screen.getByTestId("qty-price")).toHaveTextContent("10 × $200.00");
    expect(screen.getByText(/\+\$500\.00/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/₪/);
  });

  it("a TASE card shows shekels, with the agorot figure in brackets", () => {
    wrap("en", <HoldingsList holdings={[holding({
      symbol: "TEVA.TA", name_en: "Teva", market: "TASE", currency: "ILS", price: 22.17, quantity: 100, value_native: 2217,
      pnl: { ils: 217, usd: 62, pct: 10.85 }, pnl_native: 217,
    })]} />);
    expect(screen.getByTestId("position-value")).toHaveTextContent("₪2,217.00");
    expect(screen.getByTestId("qty-price")).toHaveTextContent("100 × ₪22.17 (2,217 agorot)");
    expect(screen.getByText(/\+₪217\.00/)).toBeInTheDocument();
  });

  it.each(["en", "he"] as const)("the check-numbers chip shows only when flagged (%s)", (locale) => {
    const label = (locale === "en" ? en : he).holdings.checkNumbers;
    const { unmount } = wrap(locale, <HoldingsList holdings={[holding({})]} />);
    expect(screen.queryByText(label)).toBeNull();
    unmount();
    wrap(locale, <HoldingsList holdings={[holding({ check_numbers: true })]} />);
    expect(screen.getByText(label)).toBeInTheDocument();
  });

  it("values are never altered by the flag", () => {
    wrap("en", <HoldingsList holdings={[holding({ check_numbers: true, value_native: 15500, pnl_native: 16000000, pnl: { ils: 1, usd: 1, pct: 103421 } })]} />);
    expect(screen.getByTestId("position-value")).toHaveTextContent("$15,500.00");
  });
});

describe("edit form shows the computed value", () => {
  it("updates live with the quantity and shows the TASE price unit", () => {
    wrap("en", <HoldingActions h={{ id: 1, quantity: 100, horizon: null, avg_cost: 20, cost_currency: "ILS", price: 22.17, currency: "ILS", symbol: "TEVA.TA" }} portfolioId={1} name="Teva" />);
    fireEvent.click(screen.getByRole("button", { name: "Edit Teva" }));
    const box = screen.getByTestId("edit-computed-value");
    expect(box).toHaveTextContent("Value now: ₪2,217.00");
    fireEvent.change(screen.getByLabelText(/^Quantity/), { target: { value: "200" } });
    expect(within(screen.getByRole("dialog")).getByTestId("edit-computed-value")).toHaveTextContent("Value now: ₪4,434.00");
    expect(screen.getByTestId("edit-price-unit")).toHaveTextContent("2,217 agorot");
  });

  it("a US holding shows the value but no agorot hint", () => {
    wrap("en", <HoldingActions h={{ id: 1, quantity: 10, horizon: null, price: 200, currency: "USD", symbol: "AAPL" }} portfolioId={1} name="Apple" />);
    fireEvent.click(screen.getByRole("button", { name: "Edit Apple" }));
    expect(screen.getByTestId("edit-computed-value")).toHaveTextContent("Value now: $2,000.00");
    expect(screen.queryByTestId("edit-price-unit")).toBeNull();
  });
});
