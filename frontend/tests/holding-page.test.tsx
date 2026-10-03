import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
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

import { api, type ScoreCardDetail } from "@/lib/api";
import { HoldingPage } from "@/components/HoldingPage";

const render1 = (id: number, locale: "en" | "he" = "en") =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}><HoldingPage id={id} /></NextIntlClientProvider>
    </SWRConfig>,
  );

/** What the real backend returns for a security with no price history: weights are PERCENTS (0..100), confidence 0..1. */
const realNoData = (): ScoreCardDetail => {
  const sig = (name: string, nominal: number) => ({
    name, score: 0, confidence: 0, weight: 0, nominal_weight: nominal, reasons: ["Not available yet (planned for Phase 2)."],
    data_as_of: "2026-10-03T15:16:29Z", explanation: { version: 1, summary: "Not available yet", inputs: {}, rules_applied: [] },
  });
  return {
    holding_id: 3, portfolio_id: 2, symbol: "NICE.TA", name_en: "NICE Ltd", name_he: "נייס", horizon: null, total: 0, confidence: 0,
    available: false, validated: false, disclaimer: "Not financial advice.",
    signals: [sig("technical", 25), sig("patterns", 10), sig("fundamentals", 20), sig("analysts", 20), sig("geo_news", 12.5), sig("sentiment", 12.5)],
    explanation: { version: 1, summary: "No signal has data for this security yet.", inputs: {}, rules_applied: [] },
  } as ScoreCardDetail;
};

describe("holding page", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the not-validated label, no verdict, and signal weights as percents", async () => {
    render1(1);
    expect(await screen.findByText("Not yet validated")).toBeInTheDocument();
    expect(screen.getByText(/Weight 67%/)).toBeInTheDocument();
    expect(screen.queryByText(/^(strong )?(buy|sell|hold)$/i)).toBeNull(); // no verdict anywhere
  });

  it("real-backend no-data card: nominal weights read 25%, not 2,500%, and every signal has a translated name", async () => {
    vi.spyOn(api, "scorecard").mockResolvedValue(realNoData());
    render1(3);
    expect(await screen.findByText("No score data yet for this security.")).toBeInTheDocument();
    const text = document.body.textContent ?? "";
    expect(text).toContain("nominal 25%");
    expect(text).not.toMatch(/\d,\d{3}%/);
    for (const raw of ["fundamentals", "analysts", "geo_news", "sentiment"]) expect(screen.queryByText(raw)).toBeNull();
    expect(screen.getByText("Analyst consensus")).toBeInTheDocument();
  });
});
