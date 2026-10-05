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
import { mockCommittee } from "@/lib/mock-ask";
import { CommitteeSection } from "@/components/CommitteeSection";
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

describe("committee section", () => {
  it("is part of the analyze result and runs on a button press", async () => {
    wrap("en", <AnalyzeResult symbol="AMD" />);
    expect(await screen.findByTestId("committee")).toBeInTheDocument();
    expect(screen.queryByTestId("committee-report")).toBeNull();
  });

  it("shows profile, news, each Bear risk with the CIO answer and stance, and the capped nudge", async () => {
    wrap("en", <CommitteeSection symbol="AMD" />);
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
    fireEvent.click(screen.getByRole("button", { name: en.committee.run }));
    expect(await screen.findByTestId("committee-gate")).toHaveTextContent(en.committee.gateTitle);
    unmount();
    vi.spyOn(api, "committee").mockResolvedValue({ ...mockCommittee("AMD"), launch_gate_open: true, launch_gate_reasons: [] });
    wrap("en", <CommitteeSection symbol="AMD" />);
    fireEvent.click(screen.getByRole("button", { name: en.committee.run }));
    await screen.findByTestId("committee-report");
    expect(screen.queryByTestId("committee-gate")).toBeNull();
  });

  it.each([["en", en], ["he", he]] as const)("429 shows the translated limit message (%s)", async (loc, m) => {
    vi.spyOn(api, "committee").mockRejectedValue(new ApiError(429, "Too many requests", 3600));
    wrap(loc, <CommitteeSection symbol="AMD" />);
    fireEvent.click(screen.getByRole("button", { name: m.committee.run }));
    expect(await screen.findByTestId("committee-problem-rate")).toHaveTextContent(m.committee.problem.rate);
    expect(screen.getByRole("button", { name: m.committee.run })).toBeEnabled(); // can retry later
  });

  it("shows a loading state, then a generic error for other failures", async () => {
    let fail: (e: unknown) => void = () => {};
    vi.spyOn(api, "committee").mockReturnValue(new Promise((_, rej) => { fail = rej; }));
    wrap("en", <CommitteeSection symbol="AMD" />);
    fireEvent.click(screen.getByRole("button", { name: en.committee.run }));
    expect(await screen.findByText(en.committee.runningNote)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: en.committee.running })).toBeDisabled();
    fail(new Error("boom"));
    expect(await screen.findByTestId("committee-problem-generic")).toHaveTextContent(en.committee.problem.generic);
  });

  it("never uses verdict words, in either language, with the report open", async () => {
    for (const [loc, m, re] of [["en", en, VERDICT_EN], ["he", he, VERDICT_HE]] as const) {
      const { unmount } = wrap(loc, <CommitteeSection symbol="AMD" />);
      fireEvent.click(screen.getByRole("button", { name: m.committee.run }));
      const report = await screen.findByTestId("committee-report");
      expect(screen.getByTestId("committee").textContent).not.toMatch(re);
      expect(report).toBeInTheDocument();
      unmount();
    }
  });
});
