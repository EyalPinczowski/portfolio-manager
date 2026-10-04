import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/xray",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { api } from "@/lib/api";
import { resetMockXrayRules } from "@/lib/mock-trackrecord";
import { XrayPage } from "@/components/XrayPage";

const wrap = (locale: "en" | "he", ui: ReactNode) =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <div dir={locale === "he" ? "rtl" : "ltr"} data-testid="root">
        <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>{ui}</NextIntlClientProvider>
      </div>
    </SWRConfig>,
  );
const NO_BLOCK_EN = /\b(blocked?|buy|sell|hold|recommend\w*)\b/i;
const NO_BLOCK_HE = /נחסמ|חסימ|קנה|קנו|מכור|מכרו|מומלץ|המלצה/;

describe("X-ray rules", () => {
  afterEach(() => { resetMockXrayRules(); vi.restoreAllMocks(); });

  it("shows each rule as breach, ok or off with items and a Why?", async () => {
    wrap("en", <XrayPage />);
    const c = await screen.findByTestId("rule-concentration");
    expect(c).toHaveAttribute("data-state", "breach");
    expect(c).toHaveTextContent(en.xray.state.breach);
    expect(within(c).getByRole("list", { name: en.xray.above })).toBeInTheDocument();
    expect(within(c).getByText(en.xray.why)).toBeInTheDocument();
    const states = ["concentration", "currency", "country_home", "sector"].map((r) => screen.getByTestId(`rule-${r}`).getAttribute("data-state"));
    expect(states).toContain("ok");
    expect(states).toContain("breach");
    expect(screen.getByText(en.xray.rulesIntro)).toBeInTheDocument();
    expect(screen.getByTestId("xray-rules").textContent).not.toMatch(NO_BLOCK_EN);
  });

  it("toggling a rule off PATCHes enabled=false and shows the off state", async () => {
    const spy = vi.spyOn(api, "patchXrayRules");
    wrap("en", <XrayPage />);
    const sector = await screen.findByTestId("rule-sector");
    const sw = await within(sector).findByRole("switch");
    expect(sw).toBeChecked();
    fireEvent.click(sw);
    await waitFor(() => expect(screen.getByTestId("rule-sector")).toHaveAttribute("data-state", "off"));
    expect(spy).toHaveBeenCalledWith(1, { rules: [{ rule: "sector", enabled: false }] });
    expect(within(screen.getByTestId("rule-sector")).getByRole("switch")).not.toBeChecked();
  });

  it("saves a threshold override, then clears it with null", async () => {
    const spy = vi.spyOn(api, "patchXrayRules");
    wrap("en", <XrayPage />);
    const card = await screen.findByTestId("rule-currency");
    const input = await within(card).findByLabelText(en.xray.overrideLabel);
    expect(within(card).queryByRole("button", { name: en.xray.clear })).toBeNull();
    fireEvent.change(input, { target: { value: "95" } });
    fireEvent.click(within(card).getByRole("button", { name: en.xray.save }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(1, { rules: [{ rule: "currency", threshold_pct: 95 }] }));
    const clear = await within(await screen.findByTestId("rule-currency")).findByRole("button", { name: en.xray.clear });
    expect(screen.getByTestId("rule-currency")).toHaveTextContent(en.xray.source.override);
    fireEvent.click(clear);
    await waitFor(() => expect(spy).toHaveBeenCalledWith(1, { rules: [{ rule: "currency", threshold_pct: null }] }));
    await waitFor(() => expect(within(screen.getByTestId("rule-currency")).queryByRole("button", { name: en.xray.clear })).toBeNull());
  });

  it("an out-of-range value is stopped in the form; a server 422 shows the bounds it sent", async () => {
    const spy = vi.spyOn(api, "patchXrayRules");
    wrap("en", <XrayPage />);
    const card = await screen.findByTestId("rule-concentration");
    const input = await within(card).findByLabelText(en.xray.overrideLabel);
    fireEvent.change(input, { target: { value: "99" } });
    expect(within(card).getByRole("button", { name: en.xray.save })).toBeDisabled();
    expect(within(card).getAllByText(/between 1% and 50%/).length).toBeGreaterThan(0);
    expect(spy).not.toHaveBeenCalled();
    // server-side bounds differ from the ones the form knew: the 422 body wins
    const { ApiError } = await import("@/lib/api");
    spy.mockRejectedValueOnce(new ApiError(422, "x", undefined, { code: "threshold_out_of_bounds", min_pct: 2, max_pct: 40 }));
    fireEvent.change(input, { target: { value: "45" } });
    fireEvent.click(within(card).getByRole("button", { name: en.xray.save }));
    expect(await within(card).findByRole("alert")).toHaveTextContent("between 2% and 40%");
  });

  it("Hebrew RTL renders the rules with no blocking wording", async () => {
    wrap("he", <XrayPage />);
    const c = await screen.findByTestId("rule-concentration");
    expect(screen.getByTestId("root")).toHaveAttribute("dir", "rtl");
    expect(c).toHaveTextContent(he.xray.ruleName.concentration);
    expect(c).toHaveTextContent(he.xray.state.breach);
    expect(screen.getByTestId("xray-rules").textContent).not.toMatch(NO_BLOCK_HE);
    expect(JSON.stringify(he.xray)).not.toMatch(NO_BLOCK_HE);
    expect(JSON.stringify({ ...en.xray, breachDetail: "", breaches: "" })).not.toMatch(NO_BLOCK_EN);
  });
});
