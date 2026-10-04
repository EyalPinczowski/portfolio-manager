import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");
const nav = vi.hoisted(() => ({ path: "/" }));
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => nav.path,
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { activeTab, AppShell, TabBar } from "@/components/AppShell";
import { ActionsSheet } from "@/components/ActionsSheet";

const wrap = (locale: "en" | "he", ui: ReactNode) =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>{ui}</NextIntlClientProvider>
    </SWRConfig>,
  );

beforeEach(() => { nav.path = "/"; });

describe("activeTab", () => {
  it("maps paths to tabs, tolerating a trailing slash", () => {
    expect(activeTab("/")).toBe("home");
    expect(activeTab("/settings/")).toBe("settings");
    expect(activeTab("/xray")).toBe("portfolio");
    expect(activeTab("/import")).toBe("portfolio");
    expect(activeTab("/holding")).toBe("portfolio");
    expect(activeTab("/analyze")).toBe("analyze");
    expect(activeTab("/alerts")).toBe("alerts");
    expect(activeTab("/login")).toBeNull();
  });
});

describe.each(["en", "he"] as const)("tab bar (%s)", (locale) => {
  const m = locale === "en" ? en : he;
  it("renders all five tabs and marks only the active one", () => {
    nav.path = "/settings";
    wrap(locale, <TabBar />);
    const bar = screen.getByRole("navigation", { name: m.nav.tabs });
    const links = within(bar).getAllByRole("link");
    expect(links.map((l) => l.textContent)).toEqual([m.nav.home, m.nav.portfolio, m.nav.analyze, m.nav.alerts, m.nav.settings]);
    expect(links.map((l) => l.getAttribute("aria-current"))).toEqual([null, null, null, null, "page"]);
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["/", "/xray", "/analyze", "/alerts", "/settings"]);
  });
});

describe("actions sheet", () => {
  it("links the review, analyze and import pages and disables the unbuilt action", () => {
    const onClose = vi.fn();
    wrap("en", <ActionsSheet onClose={onClose} />);
    expect(screen.getByRole("link", { name: new RegExp(en.actions.review) })).toHaveAttribute("href", "/review");
    expect(screen.getByRole("button", { name: new RegExp(en.actions.suggest) })).toBeDisabled();
    expect(screen.getByRole("link", { name: new RegExp(en.actions.analyze) })).toHaveAttribute("href", "/analyze");
    expect(screen.queryByRole("button", { name: new RegExp(en.actions.analyze) })).toBeNull();
    const link = screen.getByRole("link", { name: new RegExp(en.actions.update) });
    expect(link).toHaveAttribute("href", "/import");
    fireEvent.click(link);
    expect(onClose).toHaveBeenCalled();
  });

  it("closes on Escape and keeps focus inside (trap)", () => {
    const onClose = vi.fn();
    wrap("he", <ActionsSheet onClose={onClose} />);
    const dialog = screen.getByRole("dialog", { name: he.actions.menuTitle });
    const close = within(dialog).getByRole("button", { name: he.common.close });
    const link = within(dialog).getAllByRole("link").at(-1)!;
    link.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(close).toHaveFocus(); // wraps from the last focusable to the first
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(link).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("opens from the + button in the shell and closes again", async () => {
    wrap("en", <AppShell><p>content</p></AppShell>);
    await waitFor(() => expect(screen.getByText("content")).toBeInTheDocument());
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: en.actions.open })[0]);
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: en.common.close }));
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getAllByRole("button", { name: en.actions.open })[1]);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText(/Not financial advice/)).toBeInTheDocument();
  });
});
