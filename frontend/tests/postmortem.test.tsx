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
  usePathname: () => "/postmortem",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { api, ApiError, type Postmortem } from "@/lib/api";
import { mockRequest } from "@/lib/mock";
import { PostmortemPage, rankItems } from "@/components/PostmortemPage";
import { ActionsSheet } from "@/components/ActionsSheet";

const wrap = (locale: "en" | "he", ui: ReactNode) =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <div dir={locale === "he" ? "rtl" : "ltr"} data-testid="root">
        <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>{ui}</NextIntlClientProvider>
      </div>
    </SWRConfig>,
  );
const setExpectation = (pct: number | null, months: number | null) =>
  mockRequest("PATCH", "/portfolios/1", { expected_return_pct: pct, expected_return_horizon_months: months });
const pmData = () => mockRequest("GET", "/portfolios/1/post-mortem") as Postmortem;

/** Advice wording must never appear (English and Hebrew). The backend disclaimer "Not financial advice" is allowed. */
const NO_ADVICE_EN = /\b(buy|sell|hold|recommend\w*|should|must|consider)\b/i;
const NO_ADVICE_HE = /קנה|קנו|מכור|מכרו|מומלץ|המלצה|כדאי|עליכם|עליך|שקלו|שקול/;

describe("post-mortem page", () => {
  afterEach(() => { vi.restoreAllMocks(); setExpectation(null, null); });

  it("no expectation: shows the form empty (no default) and a benchmark-only comparison", async () => {
    wrap("en", <PostmortemPage />);
    const form = await screen.findByRole("form", { name: en.postmortem.expTitle });
    expect(within(form).getByLabelText(en.postmortem.expPct)).toHaveValue("");
    expect(within(form).getByLabelText(en.postmortem.expMonths)).toHaveValue("");
    expect(within(form).getByRole("button", { name: en.postmortem.expSave })).toBeDisabled();
    expect(within(screen.getByTestId("tile-expectation")).getByText(en.postmortem.notSet)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: en.postmortem.refBenchmark })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: en.postmortem.refExpectation }));
    expect(screen.getByText(en.postmortem.needsExpectationRef)).toBeInTheDocument();
  });

  it("the form needs both values; saving PATCHes and refreshes the page", async () => {
    const spy = vi.spyOn(api, "patchPortfolio");
    wrap("en", <PostmortemPage />);
    const form = await screen.findByRole("form", { name: en.postmortem.expTitle });
    const save = within(form).getByRole("button", { name: en.postmortem.expSave });
    fireEvent.change(within(form).getByLabelText(en.postmortem.expPct), { target: { value: "12" } });
    expect(save).toBeDisabled();
    expect(within(form).getByText(en.postmortem.expPair)).toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText(en.postmortem.expMonths), { target: { value: "0" } });
    expect(save).toBeDisabled();
    expect(within(form).getByText(en.postmortem.expRange)).toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText(en.postmortem.expMonths), { target: { value: "12" } });
    expect(save).toBeEnabled();
    fireEvent.click(save);
    await waitFor(() => expect(spy).toHaveBeenCalledWith(1, { expected_return_pct: 12, expected_return_horizon_months: 12 }));
    expect(await screen.findByTestId("reconciliation")).toBeInTheDocument(); // result with the expectation now available
    expect(within(screen.getByTestId("tile-expectation")).getByText(/12/)).toBeInTheDocument();
    expect(screen.queryByTestId("exp-needed")).toBeNull();
  });

  it("clear sends both null and brings the empty form back", async () => {
    setExpectation(12, 12);
    const spy = vi.spyOn(api, "patchPortfolio");
    wrap("en", <PostmortemPage />);
    fireEvent.click(await screen.findByText(en.postmortem.expEdit));
    expect(screen.getByTestId("exp-current")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: en.postmortem.expClear }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(1, { expected_return_pct: null, expected_return_horizon_months: null }));
    expect(await screen.findByTestId("exp-needed")).toBeInTheDocument();
  });

  it("a save error is shown and nothing changes", async () => {
    vi.spyOn(api, "patchPortfolio").mockRejectedValue(new ApiError(422, "bad"));
    wrap("en", <PostmortemPage />);
    const form = await screen.findByRole("form", { name: en.postmortem.expTitle });
    fireEvent.change(within(form).getByLabelText(en.postmortem.expPct), { target: { value: "-5" } });
    fireEvent.change(within(form).getByLabelText(en.postmortem.expMonths), { target: { value: "6" } });
    fireEvent.click(within(form).getByRole("button", { name: en.postmortem.expSave }));
    expect(await screen.findByText(en.postmortem.expSaveError)).toBeInTheDocument();
  });

  it("normal result: headline, ranked gap items, reconciliation with the residual shown, signs and arrows", async () => {
    setExpectation(12, 12);
    wrap("en", <PostmortemPage />);
    expect(await screen.findByTestId("tile-return")).toHaveTextContent("+1.90%");
    expect(screen.getByTestId("tile-expectation")).toHaveTextContent("2.80%");
    expect(screen.getByTestId("tile-benchmark")).toHaveTextContent("4.10%");
    expect(document.body.textContent).toContain("-0.90 pp"); // gap vs expectation, explicit sign
    expect(document.body.textContent).toContain("-2.20 pp"); // gap vs benchmark
    const items = screen.getAllByTestId("gap-item");
    const pp = items.map((i) => Math.abs(Number(/([+-]\d+\.\d+) pp/.exec(i.textContent ?? "")?.[1])));
    expect(pp).toEqual([...pp].sort((a, b) => b - a)); // ranked by size
    expect(items[0].textContent).toContain("₪");
    expect(screen.getByTestId("reconciliation").textContent).toMatch(/-0\.05 pp.*-₪50/);
    expect(screen.getByTestId("reconcile-flag")).toHaveTextContent(en.postmortem.reconciles);
    // the benchmark comparison does not fully reconcile: the residual is shown, not hidden
    fireEvent.click(screen.getByRole("button", { name: en.postmortem.refBenchmark }));
    expect(screen.getByTestId("reconciliation").textContent).toMatch(/-1\.35 pp.*-₪1,350/);
    expect(screen.getByTestId("reconcile-flag")).toHaveTextContent(en.postmortem.notReconcile);
    expect(document.body.textContent).not.toMatch(/NaN|undefined|\bnull\b|1970|Invalid/);
  });

  it("per holding: realized vs unrealized, local vs currency effect, contributors and detractors", async () => {
    setExpectation(12, 12);
    wrap("en", <PostmortemPage />);
    const rows = await screen.findAllByTestId("pm-holding");
    expect(rows).toHaveLength(4);
    expect(rows[0]).toHaveTextContent("NVIDIA"); // biggest total first
    for (const label of [en.postmortem.realized, en.postmortem.unrealized, en.postmortem.localEffect, en.postmortem.fxEffect]) expect(within(rows[0]).getByText(label)).toBeInTheDocument();
    expect(within(rows[2]).getByText(en.postmortem.sold)).toBeInTheDocument();
    expect(screen.getByRole("list", { name: en.postmortem.detractors })).toHaveTextContent("NICE.TA");
  });

  it("findings: each has a Why? from its Explanation and evidence rows; not_recorded ones show no numbers", async () => {
    setExpectation(12, 12);
    wrap("en", <PostmortemPage />);
    const fx = await screen.findByTestId("finding-fx");
    expect(within(fx).getByText(en.postmortem.why)).toBeInTheDocument();
    expect(within(fx).getByLabelText(en.postmortem.evidence)).toHaveTextContent("-₪300");
    expect(within(fx).getByTestId("explanation")).toHaveTextContent("Return in local currency compared with the return in shekels.");
    for (const kind of ["cash", "costs", "stops"]) {
      const card = screen.getByTestId(`finding-${kind}`);
      expect(card).toHaveAttribute("data-status", "not_recorded");
      expect(within(card).getByText(en.postmortem.notRecorded)).toBeInTheDocument();
      expect(within(card).queryByLabelText(en.postmortem.evidence)).toBeNull();
      const outside = card.cloneNode(true) as HTMLElement;
      outside.querySelector("details")?.remove(); // the Why? text may carry a timestamp
      expect(outside.textContent).not.toMatch(/[₪$%]|\d/); // no numbers anywhere else
    }
    for (const kind of ["performance", "contribution", "timing", "concentration", "fx", "flows"]) expect(screen.getByTestId(`finding-${kind}`)).toHaveAttribute("data-status", "ok");
  });

  it("not_enough_history: shows the days remaining and no numbers or gap", async () => {
    const pm = { ...pmData(), ...(mockRequest("GET", "/portfolios/2/post-mortem") as Postmortem) };
    vi.spyOn(api, "postmortem").mockResolvedValue(pm);
    wrap("en", <PostmortemPage />);
    const box = await screen.findByTestId("not-enough-history");
    expect(box).toHaveTextContent("19 more days");
    expect(box).toHaveTextContent("9 of 28");
    expect(screen.queryByTestId("tile-return")).toBeNull();
    expect(screen.queryByTestId("reconciliation")).toBeNull();
    expect(screen.getByTestId("exp-needed")).toBeInTheDocument(); // the expectation can still be entered
  });

  it("custom period: start and end go to the API; a bad range is blocked", async () => {
    setExpectation(12, 12);
    const spy = vi.spyOn(api, "postmortem");
    wrap("en", <PostmortemPage />);
    await screen.findByTestId("tile-return");
    expect(spy).toHaveBeenLastCalledWith(1, { start: null, end: null }); // since I started using the app
    fireEvent.click(screen.getByLabelText(en.postmortem.periodCustom));
    fireEvent.change(screen.getByLabelText(en.postmortem.from), { target: { value: "2026-09-10" } });
    fireEvent.change(screen.getByLabelText(en.postmortem.to), { target: { value: "2026-09-01" } });
    expect(screen.getByText(en.postmortem.badRange)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: en.postmortem.apply })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(en.postmortem.from), { target: { value: "2026-09-01" } });
    fireEvent.change(screen.getByLabelText(en.postmortem.to), { target: { value: "2026-10-03" } });
    fireEvent.click(screen.getByRole("button", { name: en.postmortem.apply }));
    await waitFor(() => expect(spy).toHaveBeenLastCalledWith(1, { start: "2026-09-01", end: "2026-10-03" }));
    expect(await screen.findByTestId("period-shown")).toHaveTextContent("01/09/2026");
  });

  it("an API error stops with a message (no partial numbers)", async () => {
    vi.spyOn(api, "postmortem").mockRejectedValue(new ApiError(500, "x"));
    wrap("en", <PostmortemPage />);
    expect(await screen.findByText(en.postmortem.error)).toBeInTheDocument();
  });

  it("Hebrew: right-to-left, localized text, signed numbers, same states", async () => {
    setExpectation(12, 12);
    wrap("he", <PostmortemPage />);
    expect(await screen.findByRole("heading", { name: he.postmortem.title })).toBeInTheDocument();
    expect(screen.getByTestId("root")).toHaveAttribute("dir", "rtl");
    expect(screen.getByTestId("tile-return")).toHaveTextContent("+1.90%");
    expect(screen.getByTestId("tile-return").querySelector("[dir=ltr]")).not.toBeNull(); // numbers stay left-to-right
    expect(document.body.textContent).toContain("נק׳ אחוז");
    expect(screen.getByTestId("finding-cash")).toHaveTextContent(he.postmortem.notRecorded);
    expect(screen.getByTestId("finding-fx")).toHaveTextContent(he.postmortem.kind.fx);
    expect(screen.getByTestId("reconcile-flag")).toHaveTextContent(he.postmortem.reconciles);
  });

  it("Hebrew: no expectation shows the form; not_enough_history shows days", async () => {
    wrap("he", <PostmortemPage />);
    const form = await screen.findByRole("form", { name: he.postmortem.expTitle });
    expect(within(form).getByLabelText(he.postmortem.expPct)).toHaveValue("");
  });

  it("Hebrew: not_enough_history shows the days remaining", async () => {
    vi.spyOn(api, "postmortem").mockResolvedValue(mockRequest("GET", "/portfolios/2/post-mortem") as Postmortem);
    wrap("he", <PostmortemPage />);
    const box = await screen.findByTestId("not-enough-history");
    expect(box).toHaveTextContent("19");
    expect(box).toHaveTextContent(he.postmortem.notEnoughHistoryTitle);
  });

  it("descriptive wording only: no advice or buy/sell language in en or he (page and messages)", async () => {
    setExpectation(12, 12);
    for (const [loc, re] of [["en", NO_ADVICE_EN], ["he", NO_ADVICE_HE]] as const) {
      const { unmount } = wrap(loc, <PostmortemPage />);
      await screen.findByTestId("tile-return");
      const text = (document.body.textContent ?? "").replace(/Not financial advice\.?/g, "");
      expect(text).not.toMatch(re);
      unmount();
    }
    for (const m of [JSON.stringify(en.postmortem), JSON.stringify(he.postmortem), en.actions.postmortemNote, he.actions.postmortemNote, en.pnl.postmortemLink, he.pnl.postmortemLink]) {
      expect(m).not.toMatch(NO_ADVICE_EN);
      expect(m).not.toMatch(NO_ADVICE_HE);
    }
  });

  it("rankItems orders by absolute pp", () => {
    const g = pmData().gap_vs_benchmark!;
    const r = rankItems(g).map((i) => Math.abs(i.pp));
    expect(r).toEqual([...r].sort((a, b) => b - a));
  });
});

describe("post-mortem entry points", () => {
  it("the + menu links to the post-mortem (en and he)", () => {
    for (const [loc, msgs] of [["en", en], ["he", he]] as const) {
      const { unmount } = wrap(loc, <ActionsSheet onClose={vi.fn()} />);
      expect(screen.getByRole("link", { name: new RegExp(msgs.actions.postmortem) })).toHaveAttribute("href", "/postmortem");
      unmount();
    }
  });
});
