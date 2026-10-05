import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");
const nav = vi.hoisted(() => ({ replace: vi.fn(), section: null as string | null }));
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/settings",
  useRouter: () => ({ replace: nav.replace, push: vi.fn() }),
}));
vi.mock("next/navigation", () => ({ useSearchParams: () => new URLSearchParams(nav.section ? `section=${nav.section}` : "") }));

import { api, type Settings } from "@/lib/api";
import { resetMockSettings } from "@/lib/mock-settings";
import { SettingsRoute } from "@/components/SettingsRoute";
import { WithSettings } from "@/components/SettingsControls";
import { AppearanceScreen, CurrencyScreen, LanguageScreen, NumberFormatScreen, WeekStartScreen } from "@/components/AppearanceSettings";
import { QuietHoursScreen, TelegramScreen, WeeklyReviewScreen } from "@/components/NotificationSettings";
import { AccountScreen } from "@/components/AccountSettings";
import { IdeaAlertsForm } from "@/components/IdeaAlertsForm";
import { AdminScreen } from "@/components/AdminSection";

type L = "en" | "he";
const wrap = (locale: L, ui: ReactNode) =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}><div dir={locale === "he" ? "rtl" : "ltr"}>{ui}</div></NextIntlClientProvider>
    </SWRConfig>,
  );
const withS = (render: (s: Settings) => ReactNode) => <WithSettings>{render}</WithSettings>;
const screens = {
  language: withS((s) => <LanguageScreen s={s} />),
  appearance: withS((s) => <AppearanceScreen s={s} />),
  currency: withS((s) => <CurrencyScreen s={s} />),
  numberFormat: withS((s) => <NumberFormatScreen s={s} />),
  weekStart: withS((s) => <WeekStartScreen s={s} />),
  weeklyReview: withS((s) => <WeeklyReviewScreen s={s} />),
  quietHours: withS((s) => <QuietHoursScreen s={s} />),
  telegram: <TelegramScreen />,
  ideas: withS((s) => <IdeaAlertsForm s={s} />),
};

beforeEach(() => { nav.replace.mockClear(); nav.section = null; });
afterEach(() => { cleanup(); vi.restoreAllMocks(); window.localStorage.removeItem("pm.mock"); resetMockSettings(); delete document.documentElement.dataset.theme; });

describe("hub", () => {
  const hrefs = (el: HTMLElement) => within(el).getAllByRole("link").map((a) => a.getAttribute("href"));
  it("shows the large title, the account row with the email, grouped rows with values, and Admin only to an admin", async () => {
    wrap("en", <SettingsRoute />);
    const hub = await screen.findByRole("navigation", { name: en.prefs.hubTitle });
    expect(screen.getByRole("heading", { level: 1, name: en.settings.title })).toBeInTheDocument();
    await waitFor(() => expect(hrefs(hub)).toEqual([
      "/settings?section=account", "/settings?section=language", "/settings?section=appearance", "/settings?section=currency", "/settings?section=numberFormat",
      "/settings?section=weekStart", "/settings?section=weeklyReview", "/settings?section=quietHours", "/settings?section=telegram", "/settings?section=ideas",
      "/settings?section=risk", "/settings?section=privacy", "/settings?section=status", "/settings?section=admin",
    ]));
    expect(await within(hub).findByText("demo@example.com")).toBeInTheDocument();
    // current values are shown as muted trailing text
    expect(within(hub).getByRole("link", { name: new RegExp(`${en.prefs.sections.language}.*English|${en.prefs.sections.language}.*עברית`) })).toBeInTheDocument();
    expect(within(hub).getByRole("link", { name: new RegExp(`${en.prefs.sections.ideaAlerts}.*${en.prefs.ideas.rowNotSet}`) })).toBeInTheDocument();
    expect(within(hub).getByRole("link", { name: new RegExp(`${en.prefs.sections.quietHours}.*${en.prefs.hub.off}`) })).toBeInTheDocument();
    cleanup();
    window.localStorage.setItem("pm.mock", "member");
    wrap("en", <SettingsRoute />);
    const hub2 = await screen.findByRole("navigation", { name: en.prefs.hubTitle });
    await waitFor(() => expect(hrefs(hub2)).toContain("/settings?section=risk"));
    await new Promise((r) => setTimeout(r, 50));
    expect(hrefs(hub2)).not.toContain("/settings?section=admin");
    expect(within(hub2).queryByText(en.prefs.sections.admin)).toBeNull();
  });

  it("price alerts and the weekly review are inline switches that save at once", async () => {
    const spy = vi.spyOn(api, "patchSettings");
    wrap("en", <SettingsRoute />);
    const price = await screen.findByRole("switch", { name: en.prefs.sections.priceAlerts });
    expect(price).toHaveAttribute("aria-checked", "true");
    fireEvent.click(price);
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ price_alerts_enabled: false }));
    await waitFor(() => expect(screen.getByRole("switch", { name: en.prefs.sections.priceAlerts })).toHaveAttribute("aria-checked", "false"));
    fireEvent.click(screen.getByRole("switch", { name: en.prefs.notifications.weeklyOn }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ weekly_review: { enabled: false } }));
  });

  it("the search field filters rows and hides empty groups; no match shows a note", async () => {
    wrap("en", <SettingsRoute />);
    const box = await screen.findByRole("searchbox", { name: en.prefs.hub.searchLabel });
    await screen.findByRole("link", { name: new RegExp(en.prefs.sections.language) });
    fireEvent.change(box, { target: { value: "tele" } });
    const links = within(screen.getByRole("main")).getAllByRole("link").map((a) => a.getAttribute("href"));
    expect(links).toEqual(["/settings?section=telegram"]);
    expect(screen.queryByRole("switch")).toBeNull();
    fireEvent.change(box, { target: { value: "zzzz" } });
    expect(screen.getByText(/No settings match/)).toBeInTheDocument();
  });

  it("a section query opens that page with a large title and a back link to the hub; sessions goes back to Account", async () => {
    nav.section = "weekStart";
    wrap("en", <SettingsRoute />);
    expect(await screen.findByRole("heading", { level: 1, name: en.prefs.sections.weekStart })).toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByRole("link", { name: en.settings.title })).toHaveAttribute("href", "/settings");
    cleanup();
    nav.section = "sessions";
    wrap("en", <SettingsRoute />);
    expect(await screen.findByRole("heading", { level: 1, name: en.prefs.sections.sessions })).toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByRole("link", { name: en.prefs.sections.account })).toHaveAttribute("href", "/settings?section=account");
  });

  it("an unknown section falls back to the hub", async () => {
    nav.section = "nope";
    wrap("en", <SettingsRoute />);
    expect(await screen.findByRole("heading", { level: 1, name: en.settings.title })).toBeInTheDocument();
  });
});

describe("every section page", () => {
  it.each(["en", "he"] as const)("opens with its own large title and keeps the bottom tab bar (%s)", async (locale) => {
    const m = locale === "en" ? en : he;
    const { SETTINGS_SECTIONS } = await import("@/lib/routes");
    const names: Record<string, string> = { ideas: "ideaAlerts" };
    for (const section of SETTINGS_SECTIONS) {
      nav.section = section;
      wrap(locale, <SettingsRoute />);
      const key = names[section] ?? section;
      expect(await screen.findByRole("heading", { level: 1, name: (m.prefs.sections as Record<string, string>)[key] })).toBeInTheDocument();
      const bar = screen.getByRole("navigation", { name: m.nav.tabs });
      expect(within(bar).getByRole("link", { name: m.nav.settings })).toHaveAttribute("aria-current", "page");
      cleanup();
    }
  });
});

describe("account page", () => {
  it("lists sessions and export, with sign out and delete in red; both ask first; delete needs the password", async () => {
    wrap("en", <AccountScreen />);
    expect(await screen.findByRole("link", { name: en.prefs.account.sessionsRow })).toHaveAttribute("href", "/settings?section=sessions");
    const out = screen.getByRole("button", { name: en.nav.logout });
    expect(out.querySelector("span.text-loss")).not.toBeNull();
    const logout = vi.spyOn(api, "logout").mockResolvedValue(undefined as never);
    fireEvent.click(out);
    expect(logout).not.toHaveBeenCalled();
    const sheet = screen.getByRole("dialog", { name: en.prefs.account.logoutTitle });
    fireEvent.click(within(sheet).getByRole("button", { name: en.common.cancel }));
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: en.settings.deleteAccount }));
    const del = within(screen.getByRole("dialog", { name: en.settings.deleteTitle }));
    expect(del.getByRole("button", { name: en.settings.deleteYes })).toBeDisabled();
    fireEvent.change(del.getByLabelText(en.settings.confirmPassword), { target: { value: "pw" } });
    expect(del.getByRole("button", { name: en.settings.deleteYes })).toBeEnabled();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("appearance pages", () => {
  it("theme is a check list: the current choice is checked, a tap saves at once and applies it", async () => {
    const spy = vi.spyOn(api, "patchSettings");
    wrap("en", screens.appearance);
    expect(await screen.findByRole("radio", { name: en.prefs.appearance.themes.system })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("radio", { name: en.prefs.appearance.themes.dark }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ theme: "dark" }));
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("dark"));
    expect(await screen.findByText(`✓ ${en.prefs.saved}`)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("radio", { name: en.prefs.appearance.themes.dark })).toHaveAttribute("aria-checked", "true"));
    fireEvent.click(screen.getByRole("radio", { name: en.prefs.appearance.themes.system }));
    await waitFor(() => expect(document.documentElement.dataset.theme).toBeUndefined());
  });

  it("language saves and switches the language route", async () => {
    wrap("en", screens.language);
    fireEvent.click(await screen.findByRole("radio", { name: "עברית" }));
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/settings", { locale: "he" }));
  });

  it("currency and number format send their own field", async () => {
    const spy = vi.spyOn(api, "patchSettings");
    wrap("en", screens.currency);
    fireEvent.click(await screen.findByRole("radio", { name: en.prefs.appearance.currencies.USD }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ main_currency: "USD" }));
    cleanup();
    wrap("en", screens.numberFormat);
    fireEvent.click(await screen.findByRole("radio", { name: en.prefs.appearance.formats.compact }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ number_format: "compact" }));
  });

  it("week start shows the server value and saves Monday; a failed save shows an error", async () => {
    wrap("en", screens.weekStart);
    const sunday = await screen.findByRole("radio", { name: en.prefs.portfolio.days.sunday });
    expect(sunday).toHaveAttribute("aria-checked", "true");
    vi.spyOn(api, "patchSettings").mockRejectedValueOnce(new Error("x"));
    fireEvent.click(screen.getByRole("radio", { name: en.prefs.portfolio.days.monday }));
    expect(await screen.findByRole("alert")).toHaveTextContent(en.prefs.saveError);
  });
});

describe("notifications", () => {
  it("weekly review shows the server's values and saves each change as a partial patch", async () => {
    const spy = vi.spyOn(api, "patchSettings");
    wrap("en", screens.weeklyReview);
    const day = (await screen.findByLabelText(en.prefs.notifications.weeklyDay)) as HTMLSelectElement;
    expect(day.value).toBe("sunday");
    expect((screen.getByLabelText(en.prefs.notifications.weeklyTime) as HTMLInputElement).value).toBe("20:00");
    expect(screen.getByText(/Asia\/Jerusalem/)).toBeInTheDocument();
    fireEvent.change(day, { target: { value: "friday" } });
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ weekly_review: { day: "friday" } }));
    fireEvent.change(screen.getByLabelText(en.prefs.notifications.weeklyTime), { target: { value: "19:30" } });
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ weekly_review: { time: "19:30" } }));
    fireEvent.click(screen.getByRole("switch", { name: en.prefs.notifications.weeklyOn }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ weekly_review: { enabled: false } }));
  });

  it("quiet hours: Save needs both times and different ones; Clear sends null", async () => {
    const spy = vi.spyOn(api, "patchSettings");
    wrap("en", screens.quietHours);
    expect(await screen.findByTestId("quiet-state")).toHaveTextContent(en.prefs.notifications.quietNone);
    const save = screen.getByRole("button", { name: en.prefs.notifications.quietSave });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText(en.prefs.notifications.quietFrom), { target: { value: "22:00" } });
    fireEvent.change(screen.getByLabelText(en.prefs.notifications.quietTo), { target: { value: "22:00" } });
    expect(save).toBeDisabled();
    expect(screen.getByText(en.prefs.notifications.quietSame)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(en.prefs.notifications.quietTo), { target: { value: "07:00" } });
    fireEvent.click(save);
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ quiet_hours: { start: "22:00", end: "07:00" } }));
    await waitFor(() => expect(screen.getByTestId("quiet-state")).toHaveTextContent("22:00"));
    fireEvent.click(screen.getByRole("button", { name: en.prefs.notifications.quietClear }));
    await waitFor(() => expect(spy).toHaveBeenLastCalledWith({ quiet_hours: null }));
    await waitFor(() => expect(screen.getByTestId("quiet-state")).toHaveTextContent(en.prefs.notifications.quietNone));
  });
});

describe("telegram", () => {
  it("connect shows the one-time code, the command and the bot link; linking replaces it with Connected; Disconnect asks first", async () => {
    wrap("en", screens.telegram);
    expect(await screen.findByTestId("telegram-state")).toHaveTextContent(en.prefs.telegram.notConnected);
    fireEvent.click(screen.getByRole("button", { name: en.prefs.telegram.connect }));
    expect(await screen.findByTestId("link-command")).toHaveTextContent("/start K7Q2M9XP");
    expect(screen.getByRole("link", { name: en.prefs.telegram.openBot })).toHaveAttribute("href", "https://t.me/PortfolioManagerBot?start=K7Q2M9XP");
    expect(screen.getByText(/@PortfolioManagerBot/)).toBeInTheDocument();
    expect(screen.getByText(/15 minutes/)).toBeInTheDocument();
    vi.spyOn(api, "telegramStatus").mockResolvedValue({ configured: true, webhook_ready: true, linked: true, bot_username: "PortfolioManagerBot" });
    await waitFor(() => expect(screen.getByTestId("telegram-state")).toHaveTextContent(en.prefs.telegram.connected), { timeout: 5000 });
    expect(screen.queryByTestId("link-code")).toBeNull();
    const del = vi.spyOn(api, "telegramUnlink");
    fireEvent.click(screen.getByRole("button", { name: en.prefs.telegram.disconnect }));
    expect(del).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: en.prefs.telegram.disconnectYes }));
    await waitFor(() => expect(del).toHaveBeenCalled());
  }, 10000);

  it("no bot on the server: explains it and offers no Connect button; a 429 on the code shows a message", async () => {
    window.localStorage.setItem("pm.mock", "no-bot");
    wrap("en", screens.telegram);
    expect(await screen.findByText(en.prefs.telegram.unavailable)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: en.prefs.telegram.connect })).toBeNull();
    cleanup();
    window.localStorage.removeItem("pm.mock");
    const { ApiError } = await import("@/lib/api");
    vi.spyOn(api, "telegramLinkCode").mockRejectedValue(new ApiError(429, "slow down"));
    wrap("en", screens.telegram);
    fireEvent.click(await screen.findByRole("button", { name: en.prefs.telegram.connect }));
    expect(await screen.findByText(en.prefs.telegram.errRate)).toBeInTheDocument();
  });
});

describe("idea alerts filter", () => {
  it("starts empty with the 'not set' state, Save disabled, and nothing preselected", async () => {
    wrap("en", screens.ideas);
    expect(await screen.findByTestId("idea-state")).toHaveTextContent(en.prefs.ideas.stateNotSet);
    expect(screen.getByRole("button", { name: en.prefs.ideas.save })).toBeDisabled();
    expect((screen.getByLabelText(en.prefs.ideas.minConfidence) as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText(en.prefs.ideas.risk) as HTMLSelectElement).value).toBe("");
    expect((screen.getByLabelText(en.prefs.ideas.maxPerDay) as HTMLInputElement).value).toBe("");
    expect(screen.getAllByRole("radio").every((r) => r.getAttribute("aria-checked") === "false")).toBe(true);
    expect(screen.getAllByRole("checkbox").every((c) => c.getAttribute("aria-checked") === "false")).toBe(true);
    expect(screen.queryAllByRole("switch")).toHaveLength(0);
    expect(screen.getByRole("button", { name: en.prefs.ideas.clear })).toBeDisabled();
    expect(await screen.findByText(en.prefs.ideas.gateClosed)).toBeInTheDocument();
  });

  it("Save enables only when every required field is valid, sends the exact filter, then shows the active state; Clear sends null", async () => {
    const spy = vi.spyOn(api, "patchSettings");
    wrap("en", screens.ideas);
    const save = await screen.findByRole("button", { name: en.prefs.ideas.save });
    fireEvent.change(screen.getByLabelText(en.prefs.ideas.minConfidence), { target: { value: "60" } });
    fireEvent.click(screen.getByRole("radio", { name: en.suggest.horizons["3m"] }));
    fireEvent.change(await screen.findByLabelText(en.prefs.ideas.risk), { target: { value: "balanced" } });
    await waitFor(() => expect((screen.getByLabelText(en.prefs.ideas.risk) as HTMLSelectElement).value).toBe("balanced"));
    fireEvent.click(screen.getByRole("checkbox", { name: en.prefs.ideas.marketNames.US }));
    fireEvent.click(screen.getByRole("checkbox", { name: en.prefs.ideas.types.stock }));
    fireEvent.change(screen.getByLabelText(en.prefs.ideas.maxPerDay), { target: { value: "3" } });
    expect(save).toBeDisabled(); // quiet-hours choice still open
    fireEvent.click(screen.getByRole("radio", { name: en.prefs.ideas.quietNone }));
    expect(save).toBeEnabled();
    fireEvent.change(screen.getByLabelText(en.prefs.ideas.maxPerDay), { target: { value: "21" } });
    expect(save).toBeDisabled();
    fireEvent.change(screen.getByLabelText(en.prefs.ideas.maxPerDay), { target: { value: "3" } });
    fireEvent.click(save);
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ idea_alerts: { min_confidence: 0.6, horizon: "3m", risk_preset: "balanced", markets: ["US"], asset_types: ["stock"], max_per_day: 3, quiet_hours: null } }));
    await waitFor(() => expect(screen.getByTestId("idea-state")).toHaveTextContent(en.prefs.ideas.stateSet));
    fireEvent.click(screen.getByRole("button", { name: en.prefs.ideas.clear }));
    await waitFor(() => expect(spy).toHaveBeenLastCalledWith({ idea_alerts: null }));
    await waitFor(() => expect(screen.getByTestId("idea-state")).toHaveTextContent(en.prefs.ideas.stateNotSet));
    expect(screen.getByRole("button", { name: en.prefs.ideas.save })).toBeDisabled();
    expect((screen.getByLabelText(en.prefs.ideas.minConfidence) as HTMLInputElement).value).toBe("");
  });

  it("quiet hours for the filter need both times", async () => {
    wrap("en", screens.ideas);
    fireEvent.click(await screen.findByRole("radio", { name: en.prefs.ideas.quietUse }));
    expect(screen.getByLabelText(en.prefs.notifications.quietFrom)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: en.prefs.ideas.save })).toBeDisabled();
  });
});

describe("admin", () => {
  it("members see only the admins-only note and no data; the API call is the 403", async () => {
    window.localStorage.setItem("pm.mock", "member");
    wrap("en", <AdminScreen />);
    expect(await screen.findByText(en.prefs.admin.notAllowed)).toBeInTheDocument();
    expect(screen.queryByText("dana@example.com")).toBeNull();
  });

  it("admins see today's AI usage totals and that tokens and cache hits are not recorded", async () => {
    wrap("en", <AdminScreen />);
    const row = await screen.findByTestId("ai-usage-row");
    expect(row).toHaveTextContent("gemini");
    expect(row).toHaveTextContent("120 of 900 requests");
    expect(screen.getByText(en.prefs.admin.aiUsage.notRecorded)).toBeInTheDocument();
  });

  it("admins create, copy and revoke invites, and disable then enable a user, without any portfolio data", async () => {
    wrap("en", <AdminScreen />);
    const invites = await screen.findByRole("list", { name: en.prefs.admin.invites });
    expect(within(invites).getByText("INV-AAAA1111")).toBeInTheDocument();
    expect(within(invites).getAllByText(en.prefs.admin.status.used).length).toBe(1);
    fireEvent.change(screen.getByLabelText(en.prefs.admin.days), { target: { value: "0" } });
    expect(screen.getByRole("button", { name: en.prefs.admin.create })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(en.prefs.admin.days), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: en.prefs.admin.create }));
    await waitFor(() => expect(within(screen.getByRole("list", { name: en.prefs.admin.invites })).getByText(/INV-NEW/)).toBeInTheDocument());
    const first = within(screen.getByRole("list", { name: en.prefs.admin.invites })).getByText("INV-AAAA1111").closest("li") as HTMLElement;
    fireEvent.click(within(first).getByRole("button", { name: en.prefs.admin.revoke }));
    await waitFor(() => expect(screen.queryByText("INV-AAAA1111")).toBeNull());

    const users = screen.getByRole("list", { name: en.prefs.admin.users });
    const dana = within(users).getByText("dana@example.com").closest("li") as HTMLElement;
    fireEvent.click(within(dana).getByRole("button", { name: en.prefs.admin.disable }));
    fireEvent.click(within(dana).getByRole("button", { name: en.prefs.admin.disableYes }));
    await waitFor(() => expect(within(dana).getByRole("button", { name: en.prefs.admin.enable })).toBeInTheDocument());
    expect(within(users).getByText("demo@example.com").closest("li")!.querySelector("button")).toBeNull(); // an admin has no Disable button
    expect(document.body.textContent).not.toMatch(/portfolio value|holdings/i);
  });
});

describe("Hebrew, RTL", () => {
  it.each(["language", "appearance", "currency", "numberFormat", "weekStart", "weeklyReview", "quietHours", "telegram", "ideas"] as const)("%s renders Hebrew labels in a right-to-left container with LTR numeric fields", async (name) => {
    const { container } = wrap("he", screens[name]);
    await waitFor(() => expect(container.querySelector("[dir=rtl]")).not.toBeNull());
    await waitFor(() => expect(container.querySelector("section")).not.toBeNull());
    const text = container.textContent ?? "";
    expect(text).toMatch(/[֐-׿]/);
    expect(text).not.toMatch(/\bprefs\./); // no missing-key fallbacks
    container.querySelectorAll("input[type=time], input[inputmode=numeric]").forEach((i) => expect(i.getAttribute("dir")).toBe("ltr"));
  });

  it("the Hebrew hub shows the Hebrew title, mirrors every chevron and the switch knob, and keeps the email left-to-right", async () => {
    const { container } = wrap("he", <SettingsRoute />);
    expect(await screen.findByRole("heading", { level: 1, name: he.settings.title })).toBeInTheDocument();
    const email = await screen.findByText("demo@example.com");
    expect(email.getAttribute("dir")).toBe("ltr");
    const main = screen.getByRole("main");
    expect(main.querySelectorAll("a > svg").length).toBeGreaterThan(8);
    expect(Array.from(main.querySelectorAll("a > svg")).every((c) => (c.getAttribute("class") ?? "").includes("rtl:-scale-x-100"))).toBe(true);
    const knob = container.querySelector("[role=switch] span span");
    expect(knob?.getAttribute("class")).toContain("rtl:-translate-x-5");
    expect(container.textContent).not.toMatch(/\bprefs\./);
    expect(screen.getByRole("switch", { name: he.prefs.sections.priceAlerts })).toBeInTheDocument();
  });

  it("a Hebrew sub-page has a Hebrew back link and title", async () => {
    nav.section = "quietHours";
    wrap("he", <SettingsRoute />);
    expect(await screen.findByRole("heading", { level: 1, name: he.prefs.sections.quietHours })).toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByRole("link", { name: he.settings.title })).toHaveAttribute("href", "/settings");
  });

  it("Hebrew idea-alerts state and Telegram connect text", async () => {
    wrap("he", screens.ideas);
    expect(await screen.findByText(he.prefs.ideas.stateNotSet)).toBeInTheDocument();
    cleanup();
    wrap("he", screens.telegram);
    expect(await screen.findByRole("button", { name: he.prefs.telegram.connect })).toBeInTheDocument();
  });
});

describe("no trade wording", () => {
  const flat = (o: unknown): string[] => (typeof o === "string" ? [o] : Object.values(o as object).flatMap(flat));
  it("the settings message groups never use buy/sell/hold wording in English or Hebrew", () => {
    const EN = /\b(buy|sell|hold|trade now)\b/i;
    const HE = /(קנה|קנייה|קניה|מכור|מכירה)/;
    for (const s of flat(en.prefs)) expect(s).not.toMatch(EN);
    for (const s of flat(he.prefs)) expect(s).not.toMatch(HE);
  });

  it("the rendered hub and sub-pages show none either", async () => {
    const EN = /\b(buy|sell|hold)\b/i;
    const { container } = wrap("en", <SettingsRoute />);
    await screen.findByRole("switch", { name: en.prefs.sections.priceAlerts });
    expect(container.textContent).not.toMatch(EN);
    cleanup();
    const r = wrap("en", screens.ideas);
    await screen.findByTestId("idea-state");
    expect(r.container.textContent).not.toMatch(EN);
  });
});
