import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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

import { MainPage } from "@/components/MainPage";

function renderMain(locale: "en" | "he") {
  return render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>
        <MainPage />
      </NextIntlClientProvider>
    </SWRConfig>,
  );
}

describe("main page (mock mode)", () => {
  it("renders header, P&L strip, buttons, holdings and disclaimer", async () => {
    renderMain("en");
    await waitFor(() => expect(screen.getByText("Portfolio value")).toBeInTheDocument());
    expect(screen.getAllByText("Since you started using the app (06/07/2026)").length).toBeGreaterThanOrEqual(1); // chart title
    expect(screen.getByText(/TASE: Open/)).toBeInTheDocument();
    expect(screen.getByText(/US: Closed/)).toBeInTheDocument();
    expect(screen.getByText("This week")).toBeInTheDocument();
    // the tab bar replaces the three big buttons; the "+" menu is tested in app-shell.test.tsx
    expect(screen.getByRole("navigation", { name: "Main tabs" })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Main actions" })).toBeNull();
    // holdings link to the holding page
    await waitFor(() => expect(screen.getAllByRole("link", { name: "Open Teva" }).length).toBeGreaterThan(0));
    expect(screen.getAllByRole("link", { name: "Open Teva" })[0]).toHaveAttribute("href", "/holding?id=1");
    expect(screen.getByText(/Not financial advice/)).toBeInTheDocument();
    // freshness line, and no raw placeholders anywhere on the home screen
    expect(screen.getByText(/Prices as of/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/NaN|undefined|\bnull\b|1970|Invalid/);
    // the holding without a cost price shows a dash and a hint, not 0
    expect(screen.getAllByText(en.holdings.pnlUnavailable, { exact: false }).length).toBeGreaterThan(0);
  });

  it("links from the performance area to the post-mortem (en and he)", async () => {
    const { unmount } = renderMain("en");
    expect(await screen.findByRole("link", { name: en.pnl.postmortemLink })).toHaveAttribute("href", "/postmortem");
    unmount();
    renderMain("he");
    expect(await screen.findByRole("link", { name: he.pnl.postmortemLink })).toHaveAttribute("href", "/postmortem");
  });

  it("shows Hebrew names and signed P&L in he", async () => {
    renderMain("he");
    await waitFor(() => expect(screen.getAllByRole("link", { name: "פתיחת טבע" }).length).toBeGreaterThan(0));
    expect(screen.getAllByText(/\+/).length).toBeGreaterThan(0);
  });
});
