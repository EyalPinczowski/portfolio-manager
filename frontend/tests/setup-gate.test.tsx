import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import en from "@/messages/en.json";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");
let path = "/xray";
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => path,
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { SetupGate, isSetupOpenPath } from "@/components/SetupGate";
import { api } from "@/lib/api";
import { guideActions } from "@/lib/guide";

const wrap = () =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale="en" messages={en}><SetupGate><p>the page</p></SetupGate></NextIntlClientProvider>
    </SWRConfig>,
  );

beforeEach(() => { guideActions.reset(); window.localStorage.setItem("pm.mock", "gate"); path = "/xray"; });
afterEach(() => { vi.restoreAllMocks(); window.localStorage.removeItem("pm.mock"); });

describe("setup gate", () => {
  it("shows the setup screen with a reason instead of the page while setup is unfinished (risk not chosen, periods missing)", async () => {
    wrap();
    expect(await screen.findByTestId("setup-gate")).toHaveTextContent(en.guide.gateWhy);
    expect(screen.queryByText("the page")).toBeNull();
    expect(screen.queryByRole("button", { name: en.guide.skipStep })).toBeNull();
    expect(screen.queryByRole("button", { name: en.guide.skipAll })).toBeNull();
  });

  it("keeps import, terms, settings and home reachable", () => {
    for (const p of ["/", "/import", "/terms", "/settings", "/settings/telegram"]) expect(isSetupOpenPath(p)).toBe(true);
    for (const p of ["/xray", "/analyze", "/alerts", "/holding"]) expect(isSetupOpenPath(p)).toBe(false);
    path = "/import";
    wrap();
    expect(screen.getByText("the page")).toBeInTheDocument();
  });

  it("shows the page once the server says every required step is done", async () => {
    const ps = await api.portfolios();
    vi.spyOn(api, "portfolios").mockResolvedValue(ps.map((p) => ({ ...p, risk_chosen_at: "2026-10-06T08:00:00Z" })));
    const real = api.holdings.bind(api);
    vi.spyOn(api, "holdings").mockImplementation(async (pid) => (await real(pid)).map((h) => ({ ...h, horizon: "6m" as const })));
    wrap();
    await waitFor(() => expect(screen.getByText("the page")).toBeInTheDocument());
    expect(screen.queryByTestId("setup-gate")).toBeNull();
  });

  it("is off in mock mode unless pm.mock is gate", () => {
    window.localStorage.removeItem("pm.mock");
    wrap();
    expect(screen.getByText("the page")).toBeInTheDocument();
  });
});
