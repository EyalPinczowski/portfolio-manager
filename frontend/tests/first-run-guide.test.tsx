import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { deriveStates, FirstRunGuide, GuideSteps, type GuideStep, type StepId } from "@/components/FirstRunGuide";
import { api } from "@/lib/api";
import { GUIDE_KEY, guideActions } from "@/lib/guide";

const wrap = (locale: "en" | "he", ui: ReactNode) =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>{ui}</NextIntlClientProvider>
    </SWRConfig>,
  );

beforeEach(() => { guideActions.reset(); try { window.localStorage.clear(); } catch { /* ignore */ } });

const none: Record<StepId, boolean> = { disclaimer: false, portfolio: false, import: false, risk: false, horizon: false, telegram: false };

describe("deriveStates", () => {
  it("marks done from facts, the first open step as current and the rest as todo", () => {
    expect(deriveStates({ ...none, disclaimer: true }, [])).toEqual({ disclaimer: "done", portfolio: "current", import: "todo", risk: "todo", horizon: "todo" });
  });
  it("a skipped step is not current; the next open one is", () => {
    expect(deriveStates({ ...none, disclaimer: true }, ["portfolio"])).toMatchObject({ portfolio: "skipped", import: "current" });
  });
  it("real state wins over a skip", () => {
    expect(deriveStates({ ...none, portfolio: true }, ["portfolio"]).portfolio).toBe("done");
  });
});

describe.each(["en", "he"] as const)("GuideSteps (%s)", (locale) => {
  const m = locale === "en" ? en : he;
  const build = (done: Partial<Record<StepId, boolean>>, skipped: StepId[] = []) => {
    const order: StepId[] = ["disclaimer", "portfolio", "import", "risk", "horizon", "telegram"];
    const states = deriveStates({ ...none, ...done }, skipped, order);
    return order.map((id): GuideStep => ({ id, state: states[id], body: <p>{`body-${id}`}</p> }));
  };
  const item = (id: string) => document.querySelector(`[data-step="${id}"]`) as HTMLElement;

  it("shows each step's state and only opens the current one", () => {
    wrap(locale, <GuideSteps steps={build({ disclaimer: true, portfolio: true })} onSkip={vi.fn()} onDismiss={vi.fn()} />);
    expect(["disclaimer", "portfolio", "import", "risk", "horizon", "telegram"].map((id) => item(id).dataset.state)).toEqual(["done", "done", "current", "todo", "todo", "todo"]);
    expect(within(item("import")).getByText(m.guide.state.current)).toBeInTheDocument();
    expect(screen.getByText("body-import")).toBeInTheDocument();
    expect(screen.queryByText("body-horizon")).toBeNull();
  });

  it("required steps have no skip button and the setup cannot be closed", () => {
    wrap(locale, <GuideSteps steps={build({ disclaimer: true, portfolio: true })} onSkip={vi.fn()} onDismiss={vi.fn()} />);
    expect(screen.queryByRole("button", { name: m.guide.skipStep })).toBeNull();
    expect(screen.queryByRole("button", { name: m.guide.skipTelegram })).toBeNull();
    expect(screen.queryByRole("button", { name: m.guide.skipAll })).toBeNull();
    expect(screen.queryByRole("button", { name: m.guide.finish })).toBeNull();
  });

  it("when only Telegram is left, it can be skipped and the setup can be finished", () => {
    const onSkip = vi.fn();
    const onDismiss = vi.fn();
    wrap(locale, <GuideSteps steps={build({ disclaimer: true, portfolio: true, import: true, risk: true, horizon: true })} onSkip={onSkip} onDismiss={onDismiss} />);
    expect(item("telegram").dataset.state).toBe("current");
    fireEvent.click(screen.getByRole("button", { name: m.guide.skipTelegram }));
    expect(onSkip).toHaveBeenCalledWith("telegram");
    fireEvent.click(screen.getByRole("button", { name: m.guide.finish }));
    expect(onDismiss).toHaveBeenCalled();
  });
});

describe("FirstRunGuide in mock mode", () => {
  it("never pre-selects a risk level or a holding period, and reflects real state", async () => {
    const portfolios = await api.portfolios();
    wrap("en", <FirstRunGuide portfolios={portfolios} />);
    const item = (id: string) => document.querySelector(`[data-step="${id}"]`) as HTMLElement;
    await waitFor(() => expect(item("import").dataset.state).toBe("done"));
    expect(item("disclaimer").dataset.state).toBe("done");
    expect(item("portfolio").dataset.state).toBe("done");
    expect(item("risk").dataset.state).toBe("current"); // the server stores a default, but the user has not chosen
    await waitFor(() => expect(screen.getAllByRole("radio").length).toBeGreaterThan(0));
    screen.getAllByRole("radio").forEach((r) => expect(r).not.toBeChecked());
    expect(screen.getByRole("button", { name: en.guide.riskSave })).toBeDisabled();
    // holding periods: some holdings have none, and the select starts on the placeholder
    fireEvent.click(within(item("horizon")).getByRole("button", { name: new RegExp(en.guide.steps.horizon.title) }));
    const selects = await screen.findAllByRole("combobox");
    expect(selects.some((s) => (s as HTMLSelectElement).value === "")).toBe(true);
    expect(item("horizon").dataset.state).toBe("todo");
  });

  it("shows a Telegram step that can be skipped, and hides it when the server has no bot", async () => {
    const portfolios = await api.portfolios();
    const { unmount } = wrap("en", <FirstRunGuide portfolios={portfolios} />);
    const item = (id: string) => document.querySelector(`[data-step="${id}"]`) as HTMLElement;
    await waitFor(() => expect(item("telegram")).not.toBeNull());
    fireEvent.click(within(item("telegram")).getByRole("button", { name: new RegExp(en.guide.steps.telegram.title) }));
    expect(within(item("telegram")).getByRole("button", { name: en.guide.tgConnect })).toBeInTheDocument();
    fireEvent.click(within(item("telegram")).getByRole("button", { name: en.guide.skipTelegram }));
    expect(item("telegram").dataset.state).toBe("skipped");
    expect(JSON.parse(window.localStorage.getItem(GUIDE_KEY) ?? "{}").telegramSkipped).toBe(true);
    unmount();
    window.localStorage.setItem("pm.mock", "no-bot");
    wrap("en", <FirstRunGuide portfolios={portfolios} />);
    await waitFor(() => expect(item("import").dataset.state).toBe("done"));
    expect(item("telegram")).toBeNull();
    window.localStorage.removeItem("pm.mock");
  });

  it("dismissal is stored in localStorage only", () => {
    guideActions.dismiss();
    expect(JSON.parse(window.localStorage.getItem(GUIDE_KEY) ?? "{}").dismissed).toBe(true);
    guideActions.reopen();
    expect(JSON.parse(window.localStorage.getItem(GUIDE_KEY) ?? "{}").dismissed).toBe(false);
  });
});
