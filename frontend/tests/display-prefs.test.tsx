import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
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

import { api } from "@/lib/api";
import { formatMoney, formatNumber, mainFirst, resetFormatPrefs, setFormatPrefs } from "@/lib/format";
import { resetMockSettings } from "@/lib/mock-settings";
import { MainPage } from "@/components/MainPage";
import { PrivacyScreen } from "@/components/PrivacySettings";
import { AboutGroup } from "@/components/SettingsPage";

afterEach(() => { cleanup(); resetFormatPrefs(); resetMockSettings(); vi.restoreAllMocks(); });

describe("shared formatter follows the saved preferences", () => {
  it("full by default; compact shortens big numbers only", () => {
    expect(formatMoney(1234567.5, "ILS", "en")).toBe("₪1,234,567.50");
    expect(formatNumber(12500, "en")).toBe("12,500");
    setFormatPrefs({ numberFormat: "compact" });
    expect(formatMoney(1234567.5, "ILS", "en")).toBe("₪1.2M");
    expect(formatMoney(-2500, "USD", "en", { signed: true })).toBe("-$2.5K");
    expect(formatMoney(62.4, "ILS", "en")).toBe("₪62.40");
    expect(formatNumber(12500, "en")).toBe("12.5K");
    expect(formatNumber(0.25, "en")).toBe("0.25");
    expect(formatMoney(1234567, "ILS", "he")).toContain("₪");
  });
  it("mainFirst puts the saved main currency first", () => {
    expect(mainFirst(370, 100).main).toEqual({ cur: "ILS", v: 370 });
    setFormatPrefs({ mainCurrency: "USD" });
    expect(mainFirst(370, 100)).toEqual({ main: { cur: "USD", v: 100 }, other: { cur: "ILS", v: 370 } });
  });
});

const renderMain = (locale: "en" | "he") =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}><MainPage /></NextIntlClientProvider>
    </SWRConfig>,
  );

describe("main page uses the saved main currency and number format", () => {
  it("shows the shekel large by default and the dollar large after the setting changes", async () => {
    renderMain("en");
    await waitFor(() => expect(screen.getByTestId("total-main")).toHaveTextContent("₪"));
    expect(screen.getByTestId("total-other")).toHaveTextContent("$");
    cleanup();
    await api.patchSettings({ main_currency: "USD", number_format: "compact" });
    renderMain("en");
    await waitFor(() => expect(screen.getByTestId("total-main")).toHaveTextContent("$"));
    expect(screen.getByTestId("total-other")).toHaveTextContent("₪");
    expect(screen.getByTestId("total-main").textContent).toMatch(/[KM]/);
  });
});

describe.each([["en", en], ["he", he]] as const)("privacy and system screens (%s)", (locale, msgs) => {
  const wrap = (ui: ReactNode) => render(<NextIntlClientProvider locale={locale} messages={msgs}><div dir={locale === "he" ? "rtl" : "ltr"}>{ui}</div></NextIntlClientProvider>);
  it("privacy: says what the AI sees, never sees, and that screenshots are not kept", () => {
    wrap(<PrivacyScreen />);
    expect(screen.getByText(msgs.prefs.privacy.aiLead)).toBeInTheDocument();
    expect(within(screen.getByTestId("ai-never")).getAllByRole("listitem")).toHaveLength(msgs.prefs.privacy.aiNever.length);
    expect(screen.getByText(msgs.prefs.privacy.shotsLead)).toBeInTheDocument();
    expect(screen.getAllByRole("link").map((a) => a.getAttribute("href"))).toContain("/settings?section=account");
    expect(screen.queryAllByRole("switch")).toHaveLength(0); // nothing to toggle: fixed rules, no fake controls
  });
  it("about: version, data sources credit and disclaimer", () => {
    wrap(<AboutGroup />);
    expect(screen.getByTestId("web-version")).toHaveTextContent(/^\d+\.\d+\.\d+/);
    expect(screen.getByTestId("data-sources")).toHaveTextContent("Yahoo Finance");
    expect(screen.getByText(msgs.disclaimer.footer)).toBeInTheDocument();
  });
});
