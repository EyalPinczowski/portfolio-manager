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
  usePathname: () => "/holding",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { api, type ExitLevelsResult } from "@/lib/api";
import { mockRequest } from "@/lib/mock";
import { ExitLevelsPanel } from "@/components/ExitLevelsPanel";

const wrap = (locale: "en" | "he", ui: ReactNode) =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>
        <div dir={locale === "he" ? "rtl" : "ltr"}>{ui}</div>
      </NextIntlClientProvider>
    </SWRConfig>,
  );
const result = (id: number, q = "") => mockRequest("GET", `/holdings/${id}/exit-levels${q}`) as ExitLevelsResult;

describe("exit levels panel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("status levels: stop, trailing stop, take-profits, scale-out, size advice, signs, R:R and a Why? per level", async () => {
    vi.spyOn(api, "exitLevels").mockResolvedValue(result(1)); // TEVA.TA, 3m: needs a smaller size in the fixture
    wrap("en", <ExitLevelsPanel holdingId={1} portfolioId={1} />);
    expect(await screen.findByTestId("level-stop")).toBeInTheDocument();
    expect(screen.getByTestId("level-trailing_stop")).toBeInTheDocument();
    expect(screen.getAllByTestId("level-take_profit")).toHaveLength(2);
    const stop = screen.getByTestId("level-stop");
    expect(stop.textContent).toMatch(/-6\.25%/); // signed distance (2.5 x ATR of 2.5%)
    expect(stop.textContent).toContain("▼");
    const tp = screen.getAllByTestId("level-take_profit")[0];
    expect(tp.textContent).toContain("▲");
    expect(tp.textContent).toContain("1:1.5"); // R:R
    expect(tp.textContent).toContain("₪");
    expect(screen.getByTestId("scale-plan")).toBeInTheDocument();
    expect(screen.queryByTestId("level-card")).toBeNull(); // one compact table, no per-level cards
    expect(screen.getAllByRole("row").length).toBeGreaterThanOrEqual(6); // header + stop, trailing, break-even, price, TP1, TP2
    expect(screen.getByText(/A smaller position fits your limits better/)).toBeInTheDocument();
    expect(screen.queryByText(/tighten/i)).toBeNull(); // never advises tightening the stop
    // Why? is built from the level's Explanation, behind the collapsed "Where each level comes from"
    expect(screen.queryByTestId("reason-stop")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: en.exit.levelReasons }));
    const why = screen.getByTestId("reason-stop");
    fireEvent.click(within(why).getByRole("button", { name: "Why?" }));
    expect(within(why).getByTestId("explanation").textContent).toMatch(/ATR\(14\) below the price/);
    expect(screen.getByText(/Not financial advice/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\b(buy|sell|hold)\b/i);
  });

  it("status levels: no cost -> P&L is a dash, never 0", async () => {
    const r = result(1);
    r.stop = { ...r.stop!, pnl_native: null, pnl_ils: null, pnl_usd: null };
    vi.spyOn(api, "exitLevels").mockResolvedValue(r);
    wrap("en", <ExitLevelsPanel holdingId={1} portfolioId={1} />);
    const stop = await screen.findByTestId("level-stop");
    expect(stop.textContent).toContain("Cost unknown, P&L not shown");
  });

  it("status needs_horizon: asks with NO preselected period, saves via the holding PATCH", async () => {
    const spy = vi.spyOn(api, "exitLevels");
    const patch = vi.spyOn(api, "patchHolding").mockResolvedValue({} as never);
    const onSaved = vi.fn();
    wrap("en", <ExitLevelsPanel holdingId={2} portfolioId={1} onHorizonSaved={onSaved} />);
    expect(await screen.findByText("Choose a holding period first")).toBeInTheDocument();
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(5);
    expect(radios.every((r) => r.getAttribute("aria-checked") === "false")).toBe(true);
    expect(screen.queryByTestId("level-stop")).toBeNull(); // no numbers
    fireEvent.click(screen.getByRole("radio", { name: en.holding.horizons["1m"] }));
    await waitFor(() => expect(patch).toHaveBeenCalledWith(1, 2, { horizon: "1m" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(spy).toHaveBeenCalled();
  });

  it("status needs_horizon: 'preview only' is a what-if and does not save", async () => {
    const patch = vi.spyOn(api, "patchHolding");
    wrap("en", <ExitLevelsPanel holdingId={2} portfolioId={1} />);
    await screen.findByText("Choose a holding period first");
    fireEvent.click(screen.getByRole("checkbox", { name: en.exit.previewToggle }));
    fireEvent.click(screen.getByRole("radio", { name: en.holding.horizons["3m"] }));
    expect(await screen.findByTestId("level-stop")).toBeInTheDocument();
    expect(patch).not.toHaveBeenCalled();
    expect(screen.getByText(/Previewing 3 months; your saved period is unchanged/)).toBeInTheDocument();
  });

  it("status no_levels: shows the reason and no numbers", async () => {
    vi.spyOn(api, "exitLevels").mockResolvedValue(result(11, "?horizon=3m"));
    wrap("en", <ExitLevelsPanel holdingId={11} portfolioId={1} />);
    expect(await screen.findByText("No levels for now")).toBeInTheDocument();
    expect(screen.getByText(en.exit.reason.stale_price)).toBeInTheDocument();
    expect(screen.queryByTestId("level-stop")).toBeNull();
    expect(screen.queryByText("Scale-out plan")).toBeNull();
    expect(screen.queryByText(/Position size/)).toBeNull();
  });

  it("shows an error with retry when the API fails", async () => {
    vi.spyOn(api, "exitLevels").mockRejectedValue(new Error("boom"));
    wrap("en", <ExitLevelsPanel holdingId={1} portfolioId={1} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(en.exit.error);
    expect(screen.getByRole("button", { name: en.exit.retry })).toBeInTheDocument();
  });

  it.each([
    ["levels", 1, "", he.exit.kind.stop],
    ["needs_horizon", 2, "", he.exit.needsHorizonTitle],
    ["no_levels", 11, "?horizon=3m", he.exit.noLevelsTitle],
  ] as const)("Hebrew RTL, status %s: Hebrew strings, numbers stay LTR", async (_s, id, q, expected) => {
    vi.spyOn(api, "exitLevels").mockResolvedValue(result(id, q));
    const { container } = wrap("he", <ExitLevelsPanel holdingId={id} portfolioId={1} />);
    expect((await screen.findAllByText(expected)).length).toBeGreaterThan(0);
    expect(container.firstElementChild).toHaveAttribute("dir", "rtl");
    if (id === 1) {
      const stop = screen.getByTestId("level-stop");
      expect(stop.querySelector("td[dir=ltr]")).not.toBeNull();
      fireEvent.click(screen.getByRole("button", { name: he.exit.levelReasons }));
      expect(within(screen.getByTestId("reason-stop")).getByRole("button", { name: he.exit.why })).toBeInTheDocument();
      expect(screen.getByTestId("scale-plan")).toBeInTheDocument();
    }
  });

  describe("scale-out plan", () => {
    const BAD_EN = /\b(buy|sell|hold|recommend\w*)\b/i;
    const BAD_HE = /(קנ[הו]|מכור|למכור|תמכור|להחזיק|ממליצ)/;
    const plan = async (locale: "en" | "he", q: string) => {
      vi.spyOn(api, "exitLevels").mockResolvedValue(result(1, q));
      wrap(locale, <ExitLevelsPanel holdingId={1} portfolioId={1} />);
      return screen.findByTestId("scale-plan");
    };

    it.each([["conservative", "45%", "25%"], ["aggressive", "25%", "50%"]])("%s profile: shares, rules, note, Why?", async (risk, first, trail) => {
      const el = await plan("en", `?risk=${risk}`);
      expect(el.textContent).toContain(en.settings.presets[risk as "conservative"]);
      expect(within(el).getByTestId("plan-planFirst").textContent).toContain(first);
      expect(within(el).getByTestId("plan-planFirst").textContent).toContain("₪");
      expect(within(el).getByTestId("plan-planTrail").textContent).toContain(trail);
      expect(within(el).getByTestId("plan-planTrail").textContent).not.toContain("₪"); // no level price for the trailing part
      expect(el.textContent).toMatch(/suggestion to review and change, not an instruction/);
      expect(within(el).queryByText(/No profile set/)).toBeNull();
      expect(screen.queryByTestId("plan-rules")).toBeNull(); // the rules are collapsed
      fireEvent.click(screen.getByRole("button", { name: en.holding.more.explain }));
      const rules = screen.getByTestId("plan-rules");
      expect(rules.textContent).toMatch(/trailing part uses a stop distance of/);
      expect(rules.textContent).toMatch(/Break-even trigger/);
      fireEvent.click(within(rules).getByRole("button", { name: "Why?" }));
      expect(within(rules).getByTestId("explanation").textContent).toMatch(/profile keeps/);
      expect(el.textContent).not.toMatch(BAD_EN);
    });

    it("fallback profile: says so plainly", async () => {
      const el = await plan("en", "");
      expect(el.textContent).toContain("No profile set: showing the Balanced plan");
      expect(el.textContent).not.toMatch(BAD_EN);
    });

    it("no plan in the response: the block is absent", async () => {
      const r = result(1);
      delete r.scale_out_plan;
      vi.spyOn(api, "exitLevels").mockResolvedValue(r);
      wrap("en", <ExitLevelsPanel holdingId={1} portfolioId={1} />);
      await screen.findByTestId("level-stop");
      expect(screen.queryByTestId("scale-plan")).toBeNull();
    });

    it("Hebrew RTL: same keys, no trade wording, fallback line", async () => {
      const el = await plan("he", "");
      expect(el.closest("[dir=rtl]")).not.toBeNull();
      expect(el.textContent).toContain("לא הוגדר פרופיל: מוצגת התכנית של הפרופיל מאוזן");
      expect(el.textContent).toContain(he.exit.planNote);
      expect(el.textContent).not.toMatch(BAD_HE);
      expect(el.textContent).not.toMatch(BAD_EN);
      const el2 = (cleanup(), await plan("he", "?risk=aggressive"));
      expect(el2.textContent).toContain(he.settings.presets.aggressive);
      expect(el2.textContent).not.toMatch(BAD_HE);
    });

    it("en and he define identical plan keys", () => {
      const keys = (o: object) => Object.keys(o).filter((k) => k.startsWith("plan")).sort();
      expect(keys(he.exit)).toEqual(keys(en.exit));
      expect(keys(en.exit).length).toBeGreaterThan(8);
    });
  });
});

describe("he exit levels show no English server sentences", () => {
  it("translates plan note, reason, source and horizon", async () => {
    vi.spyOn(api, "exitLevels").mockResolvedValue(result(1, "?risk=balanced"));
    wrap("he", <ExitLevelsPanel holdingId={1} portfolioId={1} />);
    const el = await screen.findByTestId("scale-plan");
    expect(el.textContent).not.toMatch(/not an instruction/);
    screen.getByTestId("level-stop");
    fireEvent.click(screen.getByRole("button", { name: he.exit.levelReasons }));
    const stop = screen.getByTestId("reason-stop");
    expect(stop.textContent).not.toMatch(/ATR\(14\) below|Source:/);
    expect(stop.textContent).toContain(he.exit.sources.atr);
    expect(document.body.textContent).not.toMatch(/3 months/);
  });
});
