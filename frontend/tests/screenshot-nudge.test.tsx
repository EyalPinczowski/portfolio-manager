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

import { ScreenshotNudge } from "@/components/ScreenshotNudge";
import { MainPage } from "@/components/MainPage";

const wrap = (locale: "en" | "he", ui: ReactNode) =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>{ui}</NextIntlClientProvider>
    </SWRConfig>,
  );

describe("last screenshot update", () => {
  it("shows the date and no nudge when the update is recent", () => {
    wrap("en", <ScreenshotNudge at="2026-10-01T09:00:00Z" stale={false} />);
    expect(screen.getByTestId("screenshot-update")).toHaveTextContent("Last updated from a screenshot: 01/10/2026");
    expect(screen.queryByTestId("screenshot-nudge")).toBeNull();
  });

  it.each(["en", "he"] as const)("a stale update gets a gentle nudge with a link to the import page (%s)", (locale) => {
    wrap(locale, <ScreenshotNudge at="2026-09-01T09:00:00Z" stale />);
    const m = (locale === "en" ? en : he).screenshotUpdate;
    expect(screen.getByTestId("screenshot-nudge")).toHaveTextContent(m.nudge);
    expect(screen.getByRole("link", { name: m.cta })).toHaveAttribute("href", "/import");
  });

  it("shows nothing before the first screenshot update (no date to report, never an epoch date)", () => {
    const { container } = wrap("en", <ScreenshotNudge at={null} stale />);
    expect(container).toBeEmptyDOMElement();
  });

  it("the main page shows it (mock mode)", async () => {
    wrap("en", <MainPage />);
    await waitFor(() => expect(screen.getByTestId("screenshot-update")).toHaveTextContent("Last updated from a screenshot: 01/10/2026"));
  });
});
