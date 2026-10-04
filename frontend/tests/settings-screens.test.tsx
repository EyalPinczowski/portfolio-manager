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

import { api } from "@/lib/api";
import { resetMockSettings } from "@/lib/mock-settings";
import { SettingsRoute } from "@/components/SettingsRoute";
import { WithSettings } from "@/components/SettingsControls";
import { AppearanceScreen, PortfolioPrefsScreen } from "@/components/AppearanceSettings";
import { NotificationsScreen } from "@/components/NotificationSettings";
import { IdeaAlertsForm } from "@/components/IdeaAlertsForm";
import { AdminScreen } from "@/components/AdminSection";

type L = "en" | "he";
const wrap = (locale: L, ui: ReactNode) =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}><div dir={locale === "he" ? "rtl" : "ltr"}>{ui}</div></NextIntlClientProvider>
    </SWRConfig>,
  );
const screens = {
  appearance: <WithSettings>{(s) => <AppearanceScreen s={s} />}</WithSettings>,
  portfolio: <WithSettings>{(s) => <PortfolioPrefsScreen s={s} />}</WithSettings>,
  notifications: <WithSettings>{(s) => <NotificationsScreen s={s} />}</WithSettings>,
  ideas: <WithSettings>{(s) => <IdeaAlertsForm s={s} />}</WithSettings>,
};

beforeEach(() => { nav.replace.mockClear(); nav.section = null; });
afterEach(() => { cleanup(); vi.restoreAllMocks(); window.localStorage.removeItem("pm.mock"); resetMockSettings(); delete document.documentElement.dataset.theme; });

describe("hub", () => {
  it("lists the sections as links, and shows Admin only to an admin", async () => {
    wrap("en", <SettingsRoute />);
    const nav1 = await screen.findByRole("navigation", { name: en.prefs.hubTitle });
    await waitFor(() => expect(within(nav1).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual([
      "/settings?section=appearance", "/settings?section=portfolio", "/settings?section=notifications", "/settings?section=ideas", "/settings?section=admin",
    ]));
    cleanup();
    window.localStorage.setItem("pm.mock", "member");
    wrap("en", <SettingsRoute />);
    const nav2 = await screen.findByRole("navigation", { name: en.prefs.hubTitle });
    await new Promise((r) => setTimeout(r, 50));
    expect(within(nav2).getAllByRole("link")).toHaveLength(4);
    expect(within(nav2).queryByText(en.prefs.sections.admin)).toBeNull();
  });

  it("a section query opens that screen with a back link", async () => {
    nav.section = "portfolio";
    wrap("en", <SettingsRoute />);
    expect(await screen.findByRole("heading", { level: 1, name: en.prefs.sections.portfolio })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: new RegExp(en.prefs.back) })).toHaveAttribute("href", "/settings");
  });
});

describe("appearance and portfolio", () => {
  it("saves theme at once, applies it, and switches the language route", async () => {
    const spy = vi.spyOn(api, "patchSettings");
    wrap("en", screens.appearance);
    fireEvent.click(await screen.findByRole("radio", { name: en.prefs.appearance.themes.dark }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ theme: "dark" }));
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("dark"));
    expect(await screen.findByText(`✓ ${en.prefs.saved}`)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: en.prefs.appearance.themes.system }));
    await waitFor(() => expect(document.documentElement.dataset.theme).toBeUndefined());
    fireEvent.click(screen.getByRole("radio", { name: "עברית" }));
    await waitFor(() => expect(nav.replace).toHaveBeenCalledWith("/settings", { locale: "he" }));
  });

  it("currency and number format send their own field", async () => {
    const spy = vi.spyOn(api, "patchSettings");
    wrap("en", screens.appearance);
    fireEvent.click(await screen.findByRole("radio", { name: en.prefs.appearance.currencies.USD }));
    fireEvent.click(screen.getByRole("radio", { name: en.prefs.appearance.formats.compact }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    expect(spy.mock.calls.map((c) => c[0])).toEqual([{ main_currency: "USD" }, { number_format: "compact" }]);
  });

  it("week start shows the server value and saves Monday; a failed save shows an error", async () => {
    wrap("en", screens.portfolio);
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
    wrap("en", screens.notifications);
    const day = (await screen.findByLabelText(en.prefs.notifications.weeklyDay)) as HTMLSelectElement;
    expect(day.value).toBe("sunday");
    expect((screen.getByLabelText(en.prefs.notifications.weeklyTime) as HTMLInputElement).value).toBe("20:00");
    expect(screen.getByText(/Asia\/Jerusalem/)).toBeInTheDocument();
    fireEvent.change(day, { target: { value: "friday" } });
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ weekly_review: { day: "friday" } }));
    fireEvent.change(screen.getByLabelText(en.prefs.notifications.weeklyTime), { target: { value: "19:30" } });
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ weekly_review: { time: "19:30" } }));
    fireEvent.click(screen.getByRole("checkbox", { name: en.prefs.notifications.weeklyOn }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ weekly_review: { enabled: false } }));
  });

  it("quiet hours: Save needs both times and different ones; Clear sends null", async () => {
    const spy = vi.spyOn(api, "patchSettings");
    wrap("en", screens.notifications);
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
    wrap("en", screens.notifications);
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
    wrap("en", screens.notifications);
    expect(await screen.findByText(en.prefs.telegram.unavailable)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: en.prefs.telegram.connect })).toBeNull();
    cleanup();
    window.localStorage.removeItem("pm.mock");
    const { ApiError } = await import("@/lib/api");
    vi.spyOn(api, "telegramLinkCode").mockRejectedValue(new ApiError(429, "slow down"));
    wrap("en", screens.notifications);
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
    expect(screen.getAllByRole("checkbox").every((c) => !(c as HTMLInputElement).checked)).toBe(true);
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
  it.each(["appearance", "portfolio", "notifications", "ideas"] as const)("%s renders Hebrew labels in a right-to-left container with LTR numeric fields", async (name) => {
    const { container } = wrap("he", screens[name]);
    await waitFor(() => expect(container.querySelector("[dir=rtl]")).not.toBeNull());
    await screen.findAllByRole("heading", { level: 2 });
    const text = container.textContent ?? "";
    expect(text).toMatch(/[֐-׿]/);
    expect(text).not.toMatch(/\bprefs\./); // no missing-key fallbacks
    container.querySelectorAll("input[type=time], input[inputmode=numeric]").forEach((i) => expect(i.getAttribute("dir")).toBe("ltr"));
  });

  it("Hebrew idea-alerts state and Telegram connect text", async () => {
    wrap("he", screens.ideas);
    expect(await screen.findByText(he.prefs.ideas.stateNotSet)).toBeInTheDocument();
    cleanup();
    wrap("he", screens.notifications);
    expect(await screen.findByRole("button", { name: he.prefs.telegram.connect })).toBeInTheDocument();
  });
});
