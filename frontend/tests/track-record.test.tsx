import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/track-record",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { api } from "@/lib/api";
import { setMockTrackState } from "@/lib/mock-trackrecord";
import { TrackRecordPage } from "@/components/TrackRecordPage";
import { ActionsSheet } from "@/components/ActionsSheet";

const wrap = (locale: "en" | "he", ui: ReactNode) =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <div dir={locale === "he" ? "rtl" : "ltr"} data-testid="root">
        <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>{ui}</NextIntlClientProvider>
      </div>
    </SWRConfig>,
  );

const NO_ADVICE_EN = /\b(buy|sell|hold|recommend\w*|should|must|consider|outperform\w*)\b/i;
const NO_ADVICE_HE = /קנה|קנו|מכור|מכרו|מומלץ|המלצה|כדאי|עליכם|עליך|שקלו|שקול/;

describe("track record page", () => {
  afterEach(() => { setMockTrackState("ready"); vi.restoreAllMocks(); });

  it("not started: says paper trading has not started, no table, no aggregates", async () => {
    setMockTrackState("not_started");
    wrap("en", <TrackRecordPage />);
    expect(await screen.findByTestId("state-not-started")).toHaveTextContent(/Paper trading has not started yet/);
    expect(screen.queryByTestId("call-row")).toBeNull();
    expect(screen.queryByTestId("aggregate")).toBeNull();
    expect(screen.getByTestId("methodology")).toBeInTheDocument();
    expect(screen.getByTestId("gate-weeks")).toHaveTextContent("0 of 4");
  });

  it("none ended: honest message, gate progress and counts still shown", async () => {
    setMockTrackState("none_ended");
    wrap("en", <TrackRecordPage />);
    expect(await screen.findByTestId("state-none-ended")).toBeInTheDocument();
    expect(screen.queryByTestId("call-row")).toBeNull();
    expect(screen.getByTestId("gate-resolved")).toHaveTextContent("0 of 50");
  });

  it("ready: aggregates with n, per-call table, other-weights flag, awaiting and excluded counts, gate, methodology", async () => {
    wrap("en", <TrackRecordPage />);
    const aggs = await screen.findAllByTestId("aggregate");
    expect(aggs).toHaveLength(2);
    expect(within(aggs[0]).getByText("S&P 500")).toBeInTheDocument();
    expect(aggs[0]).toHaveTextContent("Based on 3 calls");
    expect(aggs[0]).toHaveTextContent("66.7%");
    expect(aggs[0]).toHaveTextContent("+1.40 pp");
    expect(aggs[1]).toHaveTextContent("-0.80 pp");
    // never green/red in aggregates
    expect(aggs[1].querySelector(".text-gain,.text-loss")).toBeNull();
    const rows = screen.getAllByTestId("call-row");
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("AAPL");
    expect(rows[0]).toHaveTextContent("+6.20%");
    expect(rows[0]).toHaveTextContent(en.trackRecord.outcome.reached_goal);
    expect(rows[1].querySelector(".text-loss")).not.toBeNull(); // negative excess has sign and arrow too
    expect(rows[1]).toHaveTextContent("-5.00 pp");
    expect(screen.getAllByTestId("other-weights")).toHaveLength(1);
    expect(rows[2]).toContainElement(screen.getByTestId("other-weights"));
    expect(screen.getByTestId("awaiting")).toHaveTextContent("2");
    expect(screen.getByTestId("excluded")).toHaveTextContent("1");
    expect(screen.getByTestId("gate-weeks")).toHaveTextContent("5.2 of 4");
    expect(within(screen.getByTestId("methodology")).getAllByRole("listitem").length).toBeGreaterThanOrEqual(5);
  });

  it("shows an error when the request fails", async () => {
    vi.spyOn(api, "trackRecord").mockRejectedValue(new Error("x"));
    wrap("en", <TrackRecordPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent(en.trackRecord.error);
  });

  it("Hebrew RTL: renders every state with LTR numbers and no advice wording", async () => {
    for (const st of ["not_started", "none_ended", "ready"] as const) {
      setMockTrackState(st);
      const { unmount } = wrap("he", <TrackRecordPage />);
      await screen.findByTestId("methodology");
      expect(screen.getByTestId("root")).toHaveAttribute("dir", "rtl");
      expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(he.trackRecord.title);
      if (st === "ready") {
        expect(screen.getAllByTestId("call-row")).toHaveLength(3);
        expect(screen.getAllByTestId("aggregate")[0].querySelector('[dir="ltr"]')).not.toBeNull();
        expect(screen.getByText(he.trackRecord.otherWeights)).toBeInTheDocument();
      }
      const own = document.body.textContent ?? "";
      expect(own).not.toMatch(NO_ADVICE_HE);
      unmount();
    }
  });

  it("no advice wording in English UI text for any state, nor in either locale's strings", async () => {
    for (const st of ["not_started", "none_ended", "ready"] as const) {
      setMockTrackState(st);
      const { unmount } = wrap("en", <TrackRecordPage />);
      await screen.findByTestId("methodology");
      expect(document.body.textContent).not.toMatch(NO_ADVICE_EN);
      unmount();
    }
    expect(JSON.stringify(en.trackRecord) + JSON.stringify(en.actions.trackRecord) + en.actions.trackRecordNote).not.toMatch(NO_ADVICE_EN);
    expect(JSON.stringify(he.trackRecord) + he.actions.trackRecord + he.actions.trackRecordNote).not.toMatch(NO_ADVICE_HE);
  });

  it("is reachable from the actions menu", () => {
    wrap("en", <ActionsSheet onClose={() => {}} />);
    expect(screen.getByRole("link", { name: new RegExp(en.actions.trackRecord) })).toHaveAttribute("href", "/track-record");
  });
});
