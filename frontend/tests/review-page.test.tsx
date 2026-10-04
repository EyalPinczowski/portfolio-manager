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
  usePathname: () => "/review",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { api, type ExitReviewOut } from "@/lib/api";
import { mockRequest } from "@/lib/mock";
import { ReviewPage, sortRows } from "@/components/ReviewPage";

const wrap = (locale: "en" | "he") =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}><ReviewPage /></NextIntlClientProvider>
    </SWRConfig>,
  );
const review = (body: object = {}) => mockRequest("POST", "/portfolios/1/exit-review", body) as ExitReviewOut;

describe("review my portfolio", () => {
  afterEach(() => vi.restoreAllMocks());

  it("lists needs_horizon holdings first, then totals", async () => {
    wrap("en");
    expect(await screen.findByText(/Needs a holding period \(\d\)/)).toBeInTheDocument();
    const rows = await screen.findAllByTestId(/^row-/);
    const statuses = rows.map((r) => r.getAttribute("data-testid"));
    const firstLevels = statuses.indexOf("row-levels");
    expect(statuses.slice(0, firstLevels).every((s) => s === "row-needs_horizon")).toBe(true);
    expect(statuses.lastIndexOf("row-needs_horizon")).toBeLessThan(firstLevels);
    const totals = screen.getByRole("region", { name: en.review.totalsTitle });
    expect(within(totals).getByText(en.review.totalRisk)).toBeInTheDocument();
    expect(within(totals).getByText(en.review.topContrib)).toBeInTheDocument();
    expect(within(totals).getByText(en.review.noStopTitle)).toBeInTheDocument();
    expect(within(totals).getByText(en.review.tooTight)).toBeInTheDocument();
    expect(within(totals).getByText(en.review.tooWide)).toBeInTheDocument();
    expect(within(totals).getByText(en.review.smallerSize)).toBeInTheDocument();
    expect(totals.textContent).toContain("₪");
    expect(totals.textContent).toMatch(/-₪/); // risk is a loss: explicit sign
    expect(within(totals).getAllByText("LUMI.TA").length).toBeGreaterThan(0); // listed as a position with no stop
    expect(document.body.textContent).not.toMatch(/\b(buy|sell)\b/i);
  });

  it("rows link to the holding page where the exit levels open", async () => {
    wrap("en");
    const link = await screen.findByRole("link", { name: /Open Teva/ });
    expect(link).toHaveAttribute("href", "/holding?id=1");
  });

  it("sortRows puts needs_horizon, then no_levels, then the biggest risk", () => {
    const r = review({ horizon: "3m" }).rows; // ARNA is stale -> no_levels
    const sorted = sortRows([...r].reverse());
    expect(sorted[0].status).toBe("no_levels");
    const levels = sorted.filter((x) => x.status === "levels").map((x) => x.risk_ils ?? 0);
    expect(levels).toEqual([...levels].sort((a, b) => b - a));
    const mixed = sortRows(review().rows);
    expect(mixed[0].status).toBe("needs_horizon");
  });

  it("no_levels rows show the reason and no numbers", async () => {
    vi.spyOn(api, "exitReview").mockResolvedValue(review({ horizon: "3m" }));
    wrap("en");
    const row = await screen.findByTestId("row-no_levels");
    expect(row.textContent).toContain(en.exit.reason.stale_price);
    expect(row.textContent).not.toMatch(/\d{2,}\.\d{2}/);
    expect(screen.queryByText(/Needs a holding period \(/)).toBeNull();
  });

  it("empty portfolio and API error", async () => {
    vi.spyOn(api, "exitReview").mockResolvedValue({ ...review(), rows: [] });
    wrap("en");
    expect(await screen.findByText(en.review.empty)).toBeInTheDocument();
  });

  it("API error shows a message", async () => {
    vi.spyOn(api, "exitReview").mockRejectedValue(new Error("x"));
    wrap("en");
    expect(await screen.findByText(en.review.error)).toBeInTheDocument();
  });

  it("Hebrew: Hebrew labels, Hebrew names, LTR numbers", async () => {
    wrap("he");
    expect(await screen.findByText(he.review.title)).toBeInTheDocument();
    expect(await screen.findByText(he.review.totalRisk)).toBeInTheDocument();
    expect(screen.getAllByText(he.review.badge.needs_horizon).length).toBeGreaterThan(0);
    expect(screen.getByText("טבע")).toBeInTheDocument();
    const totals = screen.getByRole("region", { name: he.review.totalsTitle });
    expect(totals.querySelector("span[dir=ltr]")).not.toBeNull();
  });
});
