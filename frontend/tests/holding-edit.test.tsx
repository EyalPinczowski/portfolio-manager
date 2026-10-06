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

import { api, ApiError } from "@/lib/api";
import { HoldingActions } from "@/components/HoldingActions";
import { MainPage } from "@/components/MainPage";

const wrap = (locale: "en" | "he", ui: ReactNode) =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>{ui}</NextIntlClientProvider>
    </SWRConfig>,
  );
const h = { id: 7, quantity: 10, avg_cost: 50, cost_currency: "USD", horizon: "3m" as const };
const set = (label: string, value: string) => fireEvent.change(screen.getByLabelText(new RegExp(label)), { target: { value } });

describe("edit and remove a holding by hand", () => {
  afterEach(() => vi.restoreAllMocks());

  it("edit opens a prefilled form and PATCHes only what the user changed (horizon untouched stays unsent)", async () => {
    const patch = vi.spyOn(api, "patchHolding").mockResolvedValue({} as never);
    wrap("en", <HoldingActions h={h} portfolioId={3} name="Apple" />);
    fireEvent.click(screen.getByRole("button", { name: "Edit Apple" }));
    expect((screen.getByLabelText(/^Quantity/) as HTMLInputElement).value).toBe("10");
    expect((screen.getByLabelText(/Average cost/) as HTMLInputElement).value).toBe("50");
    set("^Quantity", "12,5");
    set("Average cost", "48");
    set("Cost currency", "ILS");
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(patch).toHaveBeenCalledWith(3, 7, { quantity: 12.5, avg_cost: 48, cost_currency: "ILS" });
  });

  it("the cost can be cleared (null) and the holding period can go back to unset", async () => {
    const patch = vi.spyOn(api, "patchHolding").mockResolvedValue({} as never);
    wrap("en", <HoldingActions h={h} portfolioId={3} name="Apple" />);
    fireEvent.click(screen.getByRole("button", { name: "Edit Apple" }));
    set("Average cost", "");
    set("Holding period", "");
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(patch).toHaveBeenCalled());
    expect(patch).toHaveBeenCalledWith(3, 7, { quantity: 10, avg_cost: null, horizon: null });
  });

  it("refuses a zero quantity and an unreadable cost without calling the API; 404 and 429 get messages", async () => {
    const patch = vi.spyOn(api, "patchHolding").mockRejectedValueOnce(new ApiError(404, "x")).mockRejectedValueOnce(new ApiError(429, "x", 5));
    wrap("en", <HoldingActions h={h} portfolioId={3} name="Apple" />);
    fireEvent.click(screen.getByRole("button", { name: "Edit Apple" }));
    set("^Quantity", "0");
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(screen.getByRole("alert")).toHaveTextContent(/quantity above zero/);
    set("^Quantity", "2");
    set("Average cost", "abc");
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(screen.getByRole("alert")).toHaveTextContent(/cost is not a valid number/);
    expect(patch).not.toHaveBeenCalled();
    set("Average cost", "5");
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/was not found/));
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent(/Too many changes/));
  });

  it("remove asks first: cancel deletes nothing, confirm calls deleteHolding and onRemoved", async () => {
    const del = vi.spyOn(api, "deleteHolding").mockResolvedValue(undefined);
    const onRemoved = vi.fn();
    wrap("en", <HoldingActions h={h} portfolioId={3} name="Apple" onRemoved={onRemoved} />);
    fireEvent.click(screen.getByRole("button", { name: "Remove Apple" }));
    expect(screen.getByRole("dialog", { name: "Remove Apple?" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(del).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Remove Apple" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, remove" }));
    await waitFor(() => expect(onRemoved).toHaveBeenCalled());
    expect(del).toHaveBeenCalledWith(3, 7);
  });

  it("a failed removal shows an error and keeps the dialog open", async () => {
    vi.spyOn(api, "deleteHolding").mockRejectedValue(new ApiError(500, "boom"));
    wrap("en", <HoldingActions h={h} portfolioId={3} name="Apple" />);
    fireEvent.click(screen.getByRole("button", { name: "Remove Apple" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, remove" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/Could not remove/);
  });

  it("Hebrew strings are used and exist for every key", () => {
    expect(Object.keys(he.holdingEdit).sort()).toEqual(Object.keys(en.holdingEdit).sort());
    wrap("he", <HoldingActions h={h} portfolioId={3} name="אפל" />);
    fireEvent.click(screen.getByRole("button", { name: "הסרת אפל" }));
    expect(screen.getByRole("dialog", { name: "להסיר את אפל?" })).toBeTruthy();
  });

  it("main page (mock): every card has Edit and Remove; editing and removing change the list", async () => {
    wrap("en", <MainPage />);
    const edits = await screen.findAllByRole("button", { name: /^Edit / });
    expect(edits.length).toBeGreaterThan(5);
    const before = (await api.holdings(1)).length;
    fireEvent.click(await screen.findByRole("button", { name: "Edit Teva" }));
    set("^Quantity", "611");
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect((await api.holdings(1)).find((x) => x.symbol === "TEVA.TA")?.quantity).toBe(611);
    fireEvent.click(await screen.findByRole("button", { name: "Remove Bank Leumi" }));
    fireEvent.click(screen.getByRole("button", { name: "Yes, remove" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect((await api.holdings(1)).length).toBe(before - 1);
  });
});
