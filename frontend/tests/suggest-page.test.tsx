import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/suggest",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { api, ApiError, type BuyIdeasOut } from "@/lib/api";
import { mockRequest } from "@/lib/mock";
import { SuggestPage } from "@/components/SuggestPage";

type L = "en" | "he";
const M = { en, he };
const wrap = (locale: L) =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={M[locale]}><div dir={locale === "he" ? "rtl" : "ltr"}><SuggestPage /></div></NextIntlClientProvider>
    </SWRConfig>,
  );
const ideas = (over: object = {}) => mockRequest("POST", "/portfolios/1/buy-ideas", { amount: 5000, currency: "USD", horizon: "3m", risk: "balanced", markets: ["US", "TASE", "CRYPTO"], asset_types: ["stock", "etf", "crypto"], ...over }) as BuyIdeasOut;

async function fill(locale: L, amount = "5000") {
  const m = M[locale].suggest;
  fireEvent.change(await screen.findByLabelText(m.amount), { target: { value: amount } });
  fireEvent.click(screen.getByRole("radio", { name: "$ USD" }));
  fireEvent.click(screen.getByRole("radio", { name: m.horizons["3m"] }));
  await waitFor(() => expect((screen.getByLabelText(m.risk) as HTMLSelectElement).options.length).toBeGreaterThan(1));
  fireEvent.change(screen.getByLabelText(m.risk), { target: { value: "balanced" } });
  for (const k of ["US", "TASE", "CRYPTO"] as const) fireEvent.click(within(group(locale, "markets")).getByRole("checkbox", { name: m.marketNames[k] }));
  for (const k of ["stock", "etf", "crypto"] as const) fireEvent.click(within(group(locale, "assetTypes")).getByRole("checkbox", { name: m.typeNames[k] }));
}
const group = (locale: L, k: "markets" | "assetTypes") => screen.getByRole("group", { name: M[locale].suggest[k] });
const submitBtn = (locale: L) => screen.getByRole("button", { name: M[locale].suggest.submit });

afterEach(() => { cleanup(); vi.restoreAllMocks(); window.localStorage.removeItem("pm.mock"); });

describe("suggest new stocks: form", () => {
  it("starts with nothing chosen and the submit button disabled", async () => {
    wrap("en");
    expect(await screen.findByLabelText(en.suggest.amount)).toHaveValue("");
    expect(screen.getAllByRole("radio").every((r) => r.getAttribute("aria-checked") === "false")).toBe(true);
    expect(screen.getAllByRole("checkbox").every((c) => !(c as HTMLInputElement).checked)).toBe(true);
    expect((screen.getByLabelText(en.suggest.risk) as HTMLSelectElement).value).toBe("");
    expect(submitBtn("en")).toBeDisabled();
    expect(screen.getByText(en.suggest.fillAll)).toBeInTheDocument();
  });

  it("enables only when every field is valid; a bad amount shows an alert", async () => {
    wrap("en");
    await fill("en", "-5");
    expect(screen.getByRole("alert")).toHaveTextContent(en.suggest.invalidAmount);
    expect(submitBtn("en")).toBeDisabled();
    fireEvent.change(screen.getByLabelText(en.suggest.amount), { target: { value: "5000" } });
    expect(submitBtn("en")).toBeEnabled();
    fireEvent.click(within(group("en", "assetTypes")).getByRole("checkbox", { name: en.suggest.typeNames.crypto }));
    fireEvent.click(within(group("en", "assetTypes")).getByRole("checkbox", { name: en.suggest.typeNames.etf }));
    fireEvent.click(within(group("en", "assetTypes")).getByRole("checkbox", { name: en.suggest.typeNames.stock }));
    expect(submitBtn("en")).toBeDisabled();
  });
});

describe("suggest new stocks: results", () => {
  it("sends exactly what was entered and lists neutral candidates with levels, size, Why? and an Analyze link", async () => {
    const spy = vi.spyOn(api, "buyIdeas");
    wrap("en");
    await fill("en");
    fireEvent.click(submitBtn("en"));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(1, { amount: 5000, currency: "USD", horizon: "3m", risk: "balanced", markets: ["US", "TASE", "CRYPTO"], asset_types: ["stock", "etf", "crypto"] }));
    const cards = await screen.findAllByTestId("candidate");
    expect(cards.length).toBe(ideas().candidates.length);
    const first = cards[0];
    expect(within(first).getByText(en.suggest.score)).toBeInTheDocument();
    expect(within(first).getByText(en.suggest.confidence)).toBeInTheDocument();
    expect(within(first).getByText(en.suggest.stop)).toBeInTheDocument();
    expect(within(first).getByText(en.suggest.takeProfits)).toBeInTheDocument();
    expect(first.textContent).toMatch(/\$|₪/);
    expect(first.textContent).toMatch(/[+-]\d/); // distances carry an explicit sign
    expect(within(first).getByRole("link", { name: /Analyze XOM/ })).toHaveAttribute("href", "/analyze?symbol=XOM");
    expect(within(first).getByRole("meter").className).not.toMatch(/gain|loss/);
    expect(first.querySelector(".bg-gain, .bg-loss")).toBeNull(); // neutral while the gate is closed
    fireEvent.click(within(first).getByRole("button", { name: en.exit.why }));
    expect(await within(first).findByTestId("explanation")).toBeInTheDocument();
  });

  it("shows the launch-gate 'not yet validated' notice and its reasons", async () => {
    wrap("en");
    await fill("en");
    fireEvent.click(submitBtn("en"));
    const gate = await screen.findByTestId("gate-notice");
    expect(gate).toHaveTextContent(en.suggest.gateTitle);
    expect(gate).toHaveTextContent("Paper-trading gate");
  });

  it("hides the gate notice when the gate is open", async () => {
    vi.spyOn(api, "buyIdeas").mockResolvedValue({ ...ideas(), launch_gate_open: true, launch_gate_reasons: [] });
    wrap("en");
    await fill("en");
    fireEvent.click(submitBtn("en"));
    await screen.findAllByTestId("candidate");
    expect(screen.queryByTestId("gate-notice")).toBeNull();
  });

  it("'Skipped and why' is collapsed, with plain-language reasons per code (country cap, stale price)", async () => {
    wrap("en");
    await fill("en");
    fireEvent.click(submitBtn("en"));
    const skipped = (await screen.findByTestId("skipped")) as HTMLDetailsElement;
    expect(skipped.open).toBe(false);
    expect(within(skipped).getByText(/Skipped and why \(\d+\)/)).toBeInTheDocument();
    fireEvent.click(within(skipped).getByText(/Skipped and why/));
    expect(skipped.textContent).toContain(en.suggest.skip.country_cap);
    expect(skipped.textContent).toContain("already near the country cap");
    expect(skipped.textContent).toContain(en.suggest.skip.stale_price);
    expect(skipped.textContent).toContain(en.suggest.skip.sector_cap);
    expect(skipped.textContent).toContain("MSFT");
  });

  it("every skip code has a plain-language text in both languages", () => {
    const codes = ["excluded_by_user", "no_score", "low_score", "low_confidence", "stale_score", "volatility_cap", "no_quote", "stale_price", "no_levels", "min_rr", "position_cap", "sector_cap", "country_cap", "size_too_small", "duplicate_listing", "ranked_lower"];
    expect(Object.keys(en.suggest.skip).sort()).toEqual([...codes].sort());
    expect(Object.keys(he.suggest.skip).sort()).toEqual([...codes].sort());
  });

  it("empty result, API error, and 'change the inputs' keep the entered values", async () => {
    vi.spyOn(api, "buyIdeas").mockResolvedValueOnce({ ...ideas(), candidates: [] });
    wrap("en");
    await fill("en");
    fireEvent.click(submitBtn("en"));
    expect(await screen.findByText(en.suggest.empty)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: en.suggest.edit }));
    expect(await screen.findByLabelText(en.suggest.amount)).toHaveValue("5000");
    vi.spyOn(api, "buyIdeas").mockRejectedValueOnce(new ApiError(500, "boom"));
    fireEvent.click(submitBtn("en"));
    expect(await screen.findByRole("alert")).toHaveTextContent(en.suggest.error);
    expect(screen.getByLabelText(en.suggest.amount)).toHaveValue("5000");
  });

  it("no portfolio yet", async () => {
    window.localStorage.setItem("pm.mock", "empty");
    wrap("en");
    expect(await screen.findByText(en.suggest.noPortfolio)).toBeInTheDocument();
  });

  it("uses no verdict wording in English", async () => {
    wrap("en");
    await fill("en");
    fireEvent.click(submitBtn("en"));
    await screen.findAllByTestId("candidate");
    fireEvent.click(within(screen.getByTestId("skipped")).getByText(/Skipped and why/));
    expect(document.body.textContent).not.toMatch(/\b(buy|sell|recommend\w*|strong buy|outperform)\b/i);
    expect(JSON.stringify(en.suggest) + JSON.stringify(en.prefs)).not.toMatch(/\b(buy|sell)\b/i);
  });
});

describe("suggest new stocks: Hebrew, RTL", () => {
  it("Hebrew form and results, Hebrew names, LTR numbers, no verdict wording", async () => {
    const { container } = wrap("he");
    await fill("he");
    expect(container.querySelector("[dir=rtl]")).not.toBeNull();
    expect((container.querySelector("#sg-amount") as HTMLElement).getAttribute("dir")).toBe("ltr");
    fireEvent.click(submitBtn("he"));
    expect(await screen.findByTestId("gate-notice")).toHaveTextContent(he.suggest.gateTitle);
    const cards = await screen.findAllByTestId("candidate");
    expect(within(cards[0]).getByText("אקסון מוביל")).toBeInTheDocument();
    expect(within(cards[0]).getByText(he.suggest.stop)).toBeInTheDocument();
    expect(cards[0].querySelector("[dir=ltr]")).not.toBeNull();
    fireEvent.click(within(screen.getByTestId("skipped")).getByText(/דילגנו ולמה/));
    expect(screen.getByTestId("skipped").textContent).toContain(he.suggest.skip.country_cap);
    expect(document.body.textContent).not.toMatch(/\b(buy|sell)\b/i);
    expect(document.body.textContent).not.toMatch(/קנה|קנו|קנייה|לקנות|מכור|מכירה|למכור|המלצ|מומלצ/);
    expect(JSON.stringify(he.suggest) + JSON.stringify(he.prefs)).not.toMatch(/קנה|קנו|קנייה|לקנות|מכור|מכירה|למכור|המלצ|מומלצ/);
  });
});
