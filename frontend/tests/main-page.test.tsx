import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
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
    expect(screen.getAllByText("Since you started using the app (06/07/2026)").length).toBeGreaterThanOrEqual(2); // tile + chart
    expect(screen.getByText(/TASE: Open/)).toBeInTheDocument();
    expect(screen.getByText(/US: Closed/)).toBeInTheDocument();
    expect(screen.getByText("This week")).toBeInTheDocument();
    // three disabled action buttons
    const nav = screen.getByRole("navigation", { name: "Main actions" });
    const buttons = within(nav).getAllByRole("button");
    expect(buttons).toHaveLength(3);
    buttons.forEach((b) => expect(b).toBeDisabled());
    // holdings link to the holding page
    await waitFor(() => expect(screen.getAllByRole("link", { name: "Open Teva" }).length).toBeGreaterThan(0));
    expect(screen.getAllByRole("link", { name: "Open Teva" })[0]).toHaveAttribute("href", "/holding/1");
    expect(screen.getByText(/Not financial advice/)).toBeInTheDocument();
  });

  it("shows Hebrew names and signed P&L in he", async () => {
    renderMain("he");
    await waitFor(() => expect(screen.getAllByRole("link", { name: "פתיחת טבע" }).length).toBeGreaterThan(0));
    expect(screen.getAllByText(/\+/).length).toBeGreaterThan(0);
  });
});
