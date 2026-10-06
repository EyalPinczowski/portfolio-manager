import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/analyze",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { api, ApiError } from "@/lib/api";
import { mockCommittee, mockCommitteeEmpty } from "@/lib/mock-ask";
import { CommitteeSection, estimateProgress } from "@/components/CommitteeSection";
import { AnalyzeResult } from "@/components/AnalyzeResult";

const wrap = (locale: "en" | "he", ui: ReactNode) =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <div dir={locale === "he" ? "rtl" : "ltr"} data-testid="root">
        <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>{ui}</NextIntlClientProvider>
      </div>
    </SWRConfig>,
  );
const VERDICT_EN = /\b(buy|sell|hold|recommend\w*|should|must)\b/i;
const VERDICT_HE = /קנה|קנו|מכור|מכרו|המלצ|כדאי/;
afterEach(() => vi.restoreAllMocks());
const openPanel = (m: typeof en) => { if (!screen.queryByRole("dialog")) fireEvent.click(screen.getByRole("button", { name: m.committee.open })); };

describe("committee section", () => {
  it("is a floating button on the analyze result; the panel opens with an explanation and closes with Escape", async () => {
    wrap("en", <AnalyzeResult symbol="AMD" />);
    const fab = await screen.findByTestId("committee-fab");
    expect(fab).toHaveAccessibleName(en.committee.open);
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(fab);
    const dlg = screen.getByRole("dialog", { name: en.committee.title });
    expect(dlg).toHaveTextContent(en.committee.explainReads);
    expect(dlg).toHaveTextContent(en.committee.explainNotAdvice);
    expect(screen.getByTestId("committee-runs-left")).toHaveTextContent("Up to 10 runs a day.");
    expect(screen.queryByTestId("committee-report")).toBeNull();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows an estimated progress bar below 100 while pending, keeps running when closed, then the result", async () => {
    let done: (o: Awaited<ReturnType<typeof api.committee>>) => void = () => {};
    vi.spyOn(api, "committee").mockReturnValue(new Promise((res) => { done = res; }));
    wrap("en", <CommitteeSection symbol="AMD" />);
    openPanel(en);
    fireEvent.click(screen.getByRole("button", { name: en.committee.run }));
    const bar = await screen.findByRole("progressbar", { name: en.committee.progressLabel });
    expect(Number(bar.getAttribute("aria-valuenow"))).toBeLessThan(100);
    expect(screen.getByRole("status")).toHaveTextContent(en.committee.stage.profile);
    fireEvent.click(screen.getByRole("button", { name: en.committee.close }));
    expect(screen.getByTestId("committee-fab-busy")).toBeInTheDocument();
    done(mockCommittee("AMD"));
    fireEvent.click(screen.getByTestId("committee-fab"));
    expect(await screen.findByTestId("committee-report")).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.queryByTestId("committee-fab-busy")).toBeNull();
  });

  it("estimateProgress rises and never reaches 100", () => {
    expect(estimateProgress(0)).toBe(0);
    expect(estimateProgress(5)).toBeLessThan(estimateProgress(20));
    expect(estimateProgress(100000)).toBeLessThanOrEqual(95);
  });

  it("shows profile, news, each Bear risk with the CIO answer and stance, and the capped nudge", async () => {
    wrap("en", <CommitteeSection symbol="AMD" />);
    openPanel(en);
    fireEvent.click(screen.getByRole("button", { name: en.committee.run }));
    const report = await screen.findByTestId("committee-report");
    expect(within(report).getByTestId("committee-profile")).toHaveTextContent("Develops and sells software");
    expect(within(report).getByTestId("committee-news")).toHaveTextContent("Quarterly revenue");
    const risks = within(report).getAllByTestId("committee-risk");
    expect(risks).toHaveLength(3);
    expect(within(risks[0]).getByTestId("stance")).toHaveTextContent(en.committee.stance.rebutted);
    expect(within(risks[0]).getByTestId("committee-answer")).toHaveTextContent("Revenue is spread");
    expect(within(risks[1]).getByTestId("stance")).toHaveTextContent(en.committee.stance.accepted);
    expect(within(risks[2]).getByTestId("stance")).toHaveTextContent(en.committee.stance.unresolved);
    const nudge = within(report).getByTestId("committee-nudge");
    expect(nudge).toHaveTextContent("at most 15 points");
    expect(nudge).toHaveTextContent("-4");
    expect(nudge).toHaveTextContent("31.5");
    expect(nudge).toHaveTextContent("27.5");
  });

  it("gate closed: shows the not yet validated notice; open: hides it", async () => {
    const { unmount } = wrap("en", <CommitteeSection symbol="AMD" />);
    openPanel(en);
    fireEvent.click(screen.getByRole("button", { name: en.committee.run }));
    expect(await screen.findByTestId("committee-gate")).toHaveTextContent(en.committee.gateTitle);
    unmount();
    vi.spyOn(api, "committee").mockResolvedValue({ ...mockCommittee("AMD"), launch_gate_open: true, launch_gate_reasons: [] });
    wrap("en", <CommitteeSection symbol="AMD" />);
    openPanel(en);
    fireEvent.click(screen.getByRole("button", { name: en.committee.run }));
    await screen.findByTestId("committee-report");
    expect(screen.queryByTestId("committee-gate")).toBeNull();
  });

  it.each([["en", en], ["he", he]] as const)("429 shows the translated limit message (%s)", async (loc, m) => {
    vi.spyOn(api, "committee").mockRejectedValue(new ApiError(429, "Too many requests", 3600));
    wrap(loc, <CommitteeSection symbol="AMD" />);
    openPanel(m);
    fireEvent.click(screen.getByRole("button", { name: m.committee.run }));
    expect(await screen.findByTestId("committee-problem-rate")).toHaveTextContent(m.committee.problem.rate);
    expect(screen.getByRole("button", { name: m.committee.run })).toBeEnabled(); // can retry later
  });

  it("shows a loading state, then a generic error for other failures", async () => {
    let fail: (e: unknown) => void = () => {};
    vi.spyOn(api, "committee").mockReturnValue(new Promise((_, rej) => { fail = rej; }));
    wrap("en", <CommitteeSection symbol="AMD" />);
    openPanel(en);
    fireEvent.click(screen.getByRole("button", { name: en.committee.run }));
    expect(await screen.findByRole("progressbar")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: en.committee.running })).toBeDisabled();
    fail(new Error("boom"));
    expect(await screen.findByTestId("committee-problem-generic")).toHaveTextContent(en.committee.problem.generic);
  });

  it("never uses verdict words, in either language, with the report open", async () => {
    for (const [loc, m, re] of [["en", en, VERDICT_EN], ["he", he, VERDICT_HE]] as const) {
      const { unmount } = wrap(loc, <CommitteeSection symbol="AMD" />);
      openPanel(m);
      fireEvent.click(screen.getByRole("button", { name: m.committee.run }));
      const report = await screen.findByTestId("committee-report");
      expect(screen.getByTestId("committee").textContent).not.toMatch(re);
      expect(report).toBeInTheDocument();
      unmount();
    }
  });
});

describe("committee readable output", () => {
  const runWith = async (loc: "en" | "he", m: typeof en, out: Awaited<ReturnType<typeof api.committee>>) => {
    vi.spyOn(api, "committee").mockResolvedValue(out);
    wrap(loc, <CommitteeSection symbol="SPCX" />);
    openPanel(m);
    fireEvent.click(screen.getByRole("button", { name: m.committee.run }));
    return screen.findByTestId("committee-report");
  };

  it.each([["en", en], ["he", he]] as const)("nothing to review: one short card with the percentage, no empty sections (%s)", async (loc, m) => {
    const report = await runWith(loc, m, mockCommitteeEmpty("SPCX"));
    const card = within(report).getByTestId("committee-empty");
    expect(card).toHaveTextContent(m.committee.emptyTitle);
    expect(card).toHaveTextContent("19%");
    expect(card).toHaveTextContent(m.committee.emptyDo);
    for (const id of ["committee-profile", "committee-news", "committee-risks", "committee-nudge", "committee-completeness"]) expect(within(report).queryByTestId(id)).toBeNull();
    expect(within(report).getByTestId("committee-banner")).toHaveTextContent(m.committee.templateOnly); // one banner, no per-section chips
    expect(report.textContent).not.toMatch(/Template|תבנית/);
  });

  it("basic summary with some content: banner once, completeness line once, empty profile and news hidden, no echo reply", async () => {
    const empty = mockCommitteeEmpty("SPCX");
    const out = { ...empty, report: { ...empty.report, profile: { ...empty.report.profile, status: "ok" as const } } };
    const report = await runWith("en", en, out);
    expect(within(report).queryByTestId("committee-empty")).toBeNull();
    expect(within(report).getByTestId("committee-completeness")).toHaveTextContent("Only 19% of the checks had data.");
    expect(within(report).queryByTestId("committee-profile")).toBeNull();
    expect(within(report).queryByTestId("committee-news")).toBeNull();
    const risks = within(report).getAllByTestId("committee-risk");
    expect(risks).toHaveLength(1);
    expect(risks[0]).toHaveTextContent(en.committee.risk.no_chart_signal);
    expect(risks[0]).toHaveTextContent("Importance 2 of 5");
    expect(within(risks[0]).queryByTestId("committee-answer")).toBeNull();
    expect(within(report).getByTestId("committee-nudge")).toHaveTextContent(en.committee.nudgeNone);
  });

  it.each([["en", en], ["he", he]] as const)("renamed labels and translated gate reasons, no raw English in Hebrew (%s)", async (loc, m) => {
    const report = await runWith(loc, m, mockCommittee("AMD"));
    expect(within(report).getByTestId("committee-risks")).toHaveTextContent(m.committee.risksTitle);
    expect(within(report).getAllByTestId("committee-answer")[0]).toHaveTextContent(m.committee.cioAnswer);
    expect(within(report).getAllByTestId("committee-risk")[0]).toHaveTextContent(m.committee.severity.replace("{n}", "3"));
    expect(within(report).getByTestId("committee-nudge")).toHaveTextContent(m.committee.nudgeTitle);
    const gate = within(report).getByTestId("committee-gate");
    expect(gate).toHaveTextContent(m.committee.gateReason.no_backtest);
    expect(gate).not.toHaveTextContent("No backtest has been run for the active weights");
    if (loc === "he") expect(gate.textContent).not.toMatch(/[A-Za-z]{4,}/);
  });

  it("English labels read plainly", () => {
    expect(en.committee.risksTitle).toBe("Risks to watch");
    expect(en.committee.cioAnswer).toBe("Reviewer's reply");
    expect(en.committee.severity).toBe("Importance {n} of 5");
    expect(en.committee.nudgeTitle).toBe("Effect on the score");
    expect(en.committee.nudgeNone).toBe("The review did not change the score");
  });
});

describe("committee run limit", () => {
  it("shows the cap before a run and the runs left after it", async () => {
    wrap("en", <CommitteeSection symbol="AAPL" />);
    openPanel(en);
    expect(screen.getByTestId("committee-runs-left")).toHaveTextContent("Up to 10 runs a day.");
    fireEvent.click(screen.getByRole("button", { name: en.committee.run }));
    expect(await screen.findByTestId("committee-risks")).toBeInTheDocument();
    expect(screen.getByTestId("committee-runs-left")).toHaveTextContent("9 of 10 runs left today.");
  });

  it("a 429 shows the reset message and 0 runs left", async () => {
    wrap("en", <CommitteeSection symbol="RATELIMIT" />);
    openPanel(en);
    fireEvent.click(screen.getByRole("button", { name: en.committee.run }));
    expect(await screen.findByTestId("committee-problem-rate")).toHaveTextContent("resets within 24 hours");
    expect(screen.getByTestId("committee-runs-left")).toHaveTextContent("0 of 10 runs left today.");
  });
});
