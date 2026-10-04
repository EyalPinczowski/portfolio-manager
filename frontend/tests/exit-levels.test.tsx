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
  usePathname: () => "/holding",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { api, type ExitLevelsResult } from "@/lib/api";
import { mockRequest } from "@/lib/mock";
import { ExitLevelsPanel } from "@/components/ExitLevelsPanel";

const wrap = (locale: "en" | "he", ui: ReactNode) =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>
        <div dir={locale === "he" ? "rtl" : "ltr"}>{ui}</div>
      </NextIntlClientProvider>
    </SWRConfig>,
  );
const result = (id: number, q = "") => mockRequest("GET", `/holdings/${id}/exit-levels${q}`) as ExitLevelsResult;

describe("exit levels panel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("status levels: stop, trailing stop, take-profits, scale-out, size advice, signs, R:R and a Why? per level", async () => {
    vi.spyOn(api, "exitLevels").mockResolvedValue(result(1)); // TEVA.TA, 3m: needs a smaller size in the fixture
    wrap("en", <ExitLevelsPanel holdingId={1} portfolioId={1} />);
    expect(await screen.findByTestId("level-stop")).toBeInTheDocument();
    expect(screen.getByTestId("level-trailing_stop")).toBeInTheDocument();
    expect(screen.getAllByTestId("level-take_profit")).toHaveLength(2);
    const stop = screen.getByTestId("level-stop");
    expect(stop.textContent).toMatch(/-6\.25%/); // signed distance (2.5 x ATR of 2.5%)
    expect(stop.textContent).toContain("▼");
    const tp = screen.getAllByTestId("level-take_profit")[0];
    expect(tp.textContent).toContain("▲");
    expect(tp.textContent).toContain("1:1.5"); // R:R
    expect(tp.textContent).toContain("₪");
    expect(screen.getByText(/Scale-out plan/)).toBeInTheDocument();
    expect(screen.getByText(/A smaller position fits your limits better/)).toBeInTheDocument();
    expect(screen.queryByText(/tighten/i)).toBeNull(); // never advises tightening the stop
    // Why? is built from the level's Explanation
    fireEvent.click(within(stop).getByRole("button", { name: "Why?" }));
    expect(within(stop).getByTestId("explanation")).toBeInTheDocument();
    expect(within(stop).getByTestId("explanation").textContent).toMatch(/ATR\(14\) below the price/);
    expect(screen.getByText(/Not financial advice/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\b(buy|sell|hold)\b/i);
  });

  it("status levels: no cost -> P&L is a dash, never 0", async () => {
    const r = result(1);
    r.stop = { ...r.stop!, pnl_native: null, pnl_ils: null, pnl_usd: null };
    vi.spyOn(api, "exitLevels").mockResolvedValue(r);
    wrap("en", <ExitLevelsPanel holdingId={1} portfolioId={1} />);
    const stop = await screen.findByTestId("level-stop");
    expect(stop.textContent).toContain("Cost unknown, P&L not shown");
  });

  it("status needs_horizon: asks with NO preselected period, saves via the holding PATCH", async () => {
    const spy = vi.spyOn(api, "exitLevels");
    const patch = vi.spyOn(api, "patchHolding").mockResolvedValue({} as never);
    const onSaved = vi.fn();
    wrap("en", <ExitLevelsPanel holdingId={2} portfolioId={1} onHorizonSaved={onSaved} />);
    expect(await screen.findByText("Choose a holding period first")).toBeInTheDocument();
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(5);
    expect(radios.every((r) => r.getAttribute("aria-checked") === "false")).toBe(true);
    expect(screen.queryByTestId("level-stop")).toBeNull(); // no numbers
    fireEvent.click(screen.getByRole("radio", { name: en.holding.horizons["1m"] }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith(1, 2, { horizon: "1m" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(spy).toHaveBeenCalled();
  });

  it("status needs_horizon: 'preview only' is a what-if and does not save", async () => {
    const patch = vi.spyOn(api, "patchHolding");
    wrap("en", <ExitLevelsPanel holdingId={2} portfolioId={1} />);
    await screen.findByText("Choose a holding period first");
    fireEvent.click(screen.getByRole("checkbox", { name: en.exit.previewToggle }));
    fireEvent.click(screen.getByRole("radio", { name: en.holding.horizons["3m"] }));
    expect(await screen.findByTestId("level-stop")).toBeInTheDocument();
    expect(patch).not.toHaveBeenCalled();
    expect(screen.getByText(/Previewing 3 months; your saved period is unchanged/)).toBeInTheDocument();
  });

  it("status no_levels: shows the reason and no numbers", async () => {
    vi.spyOn(api, "exitLevels").mockResolvedValue(result(11, "?horizon=3m"));
    wrap("en", <ExitLevelsPanel holdingId={11} portfolioId={1} />);
    expect(await screen.findByText("No levels for now")).toBeInTheDocument();
    expect(screen.getByText(en.exit.reason.stale_price)).toBeInTheDocument();
    expect(screen.queryByTestId("level-stop")).toBeNull();
    expect(screen.queryByText("Scale-out plan")).toBeNull();
    expect(screen.queryByText(/Position size/)).toBeNull();
  });

  it("shows an error with retry when the API fails", async () => {
    vi.spyOn(api, "exitLevels").mockRejectedValue(new Error("boom"));
    wrap("en", <ExitLevelsPanel holdingId={1} portfolioId={1} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(en.exit.error);
    expect(screen.getByRole("button", { name: en.exit.retry })).toBeInTheDocument();
  });

  it.each([
    ["levels", 1, "", he.exit.kind.stop],
    ["needs_horizon", 2, "", he.exit.needsHorizonTitle],
    ["no_levels", 11, "?horizon=3m", he.exit.noLevelsTitle],
  ] as const)("Hebrew RTL, status %s: Hebrew strings, numbers stay LTR", async (_s, id, q, expected) => {
    vi.spyOn(api, "exitLevels").mockResolvedValue(result(id, q));
    const { container } = wrap("he", <ExitLevelsPanel holdingId={id} portfolioId={1} />);
    expect((await screen.findAllByText(expected)).length).toBeGreaterThan(0);
    expect(container.firstElementChild).toHaveAttribute("dir", "rtl");
    if (id === 1) {
      const stop = screen.getByTestId("level-stop");
      expect(stop.querySelector("dd span[dir=ltr]")).not.toBeNull();
      expect(within(stop).getByRole("button", { name: he.exit.why })).toBeInTheDocument();
      expect(screen.getByText(he.exit.scaleTitle)).toBeInTheDocument();
    }
  });
});
