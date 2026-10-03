import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";
import type { Health, Summary } from "@/lib/api";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/settings",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { api } from "@/lib/api";
import { mockRequest } from "@/lib/mock";
import { SystemStatus } from "@/components/SettingsPage";

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();
const health = (over: Partial<Health> = {}): Health => ({ status: "ok", scheduler: "leader", leader: true, last_quotes_at: ago(3), last_snapshot_at: "2026-10-02", ...over });
const setup = (h: Health, open: boolean, fxStale = false, locale: "en" | "he" = "en") => {
  vi.spyOn(api, "health").mockResolvedValue(h);
  const base = mockRequest("GET", "/portfolios/1/summary") as Summary;
  vi.spyOn(api, "summary").mockResolvedValue({ ...base, fx_stale: fxStale, markets: { US: { open }, TASE: { open: false }, CRYPTO: { open: true } } });
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}><SystemStatus /></NextIntlClientProvider>
    </SWRConfig>,
  );
};

describe("system status", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows leader, relative last quotes and snapshot, and no warning when fresh", async () => {
    setup(health(), true);
    expect(await screen.findByText(en.settings.schedulerStates.leader)).toBeInTheDocument();
    expect(screen.getByTestId("last-quotes")).toHaveTextContent("3 min ago");
    expect(screen.getByTestId("last-snapshot")).toHaveTextContent(/\d+ d ago/);
    expect(screen.queryByText(en.settings.staleWarning)).toBeNull();
    expect(await screen.findByText(en.settings.fxOk)).toBeInTheDocument();
  });

  it("warns when quotes are older than 15 minutes while a market is open, but not when markets are closed", async () => {
    setup(health({ last_quotes_at: ago(40) }), true);
    expect(await screen.findByText(en.settings.staleWarning)).toBeInTheDocument();
    expect(screen.getByTestId("last-quotes")).toHaveTextContent("40 min ago");
  });

  it("does not warn about old quotes when no stock market is open", async () => {
    setup(health({ last_quotes_at: ago(300) }), false);
    expect(await screen.findByText(en.settings.schedulerStates.leader)).toBeInTheDocument();
    await screen.findByText(en.settings.fxOk);
    expect(screen.queryByText(en.settings.staleWarning)).toBeNull();
  });

  it("warns when the scheduler is unavailable, and shows never for missing times and the FX note", async () => {
    setup(health({ scheduler: "unavailable", leader: false, last_quotes_at: null, last_snapshot_at: null }), false, true);
    expect(await screen.findByText(en.settings.schedulerDown)).toBeInTheDocument();
    expect(screen.getByTestId("last-quotes")).toHaveTextContent("never");
    expect(await screen.findByText(en.settings.fxNow)).toBeInTheDocument();
  });

  it("covers standby and external in Hebrew", async () => {
    setup(health({ scheduler: "standby", leader: false }), false, false, "he");
    expect(await screen.findByText(he.settings.schedulerStates.standby)).toBeInTheDocument();
  });
});
