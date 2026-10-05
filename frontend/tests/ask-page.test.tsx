import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/ask",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { api, ApiError } from "@/lib/api";
import { resetMockAsk } from "@/lib/mock-ask";
import { AskPage, stripCites } from "@/components/AskPage";

const wrap = (locale: "en" | "he", ui: ReactNode) =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <div dir={locale === "he" ? "rtl" : "ltr"} data-testid="root">
        <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>{ui}</NextIntlClientProvider>
      </div>
    </SWRConfig>,
  );
const VERDICT_EN = /\b(buy|sell|recommend\w*|should|must)\b/i;
const VERDICT_HE = /קנה|קנו|מכור|מכרו|המלצ|כדאי/;

async function ask(q: string, label: string) {
  fireEvent.change(await screen.findByLabelText(label), { target: { value: q } });
  fireEvent.click(screen.getByRole("button", { name: label === en.ask.askLabel ? en.ask.send : he.ask.send }));
}

beforeEach(() => resetMockAsk());
afterEach(() => vi.restoreAllMocks());

describe("ask my portfolio", () => {
  it("answers with the tools used and lists the conversation", async () => {
    wrap("en", <AskPage />);
    await ask("How is my risk spread?", en.ask.askLabel);
    const answer = await screen.findByTestId("msg-answer");
    expect(answer).toHaveTextContent("Technology is your largest sector");
    expect(within(screen.getByTestId("tools-used")).getByText(en.ask.tool.get_xray)).toBeInTheDocument();
    expect(await screen.findAllByTestId("conversation")).toHaveLength(1);
    expect(screen.getByLabelText(en.ask.askLabel)).toHaveValue("");
  });

  it("caps the question box at 500 characters and rejects an empty question", async () => {
    wrap("en", <AskPage />);
    const box = await screen.findByLabelText(en.ask.askLabel);
    expect(box).toHaveAttribute("maxlength", "500");
    fireEvent.click(screen.getByRole("button", { name: en.ask.send }));
    expect(await screen.findByTestId("ask-problem-empty")).toHaveTextContent(en.ask.problem.empty);
  });

  it.each([["en", en], ["he", he]] as const)("429 shows the translated rate-limit message, not a crash (%s)", async (loc, m) => {
    vi.spyOn(api, "askPortfolio").mockRejectedValue(new ApiError(429, "Too many requests", 3600));
    wrap(loc, <AskPage />);
    await ask("anything", m.ask.askLabel);
    expect(await screen.findByTestId("ask-problem-rate")).toHaveTextContent(m.ask.problem.rate);
    expect(screen.getByLabelText(m.ask.askLabel)).toHaveValue("anything"); // the question is kept
  });

  it("a holding with no horizon gets a prompt that links to that holding page", async () => {
    wrap("en", <AskPage />);
    await ask("What are my exit levels?", en.ask.askLabel);
    const prompt = await screen.findByTestId("needs-horizon");
    expect(prompt).toHaveTextContent(en.ask.needsHorizonTitle);
    const link = within(prompt).getByTestId("needs-horizon-link");
    expect(link.getAttribute("href")).toBe("/holding?id=2");
    expect(link).toHaveTextContent("LUMI.TA");
  });

  it("reopened history shows no holding-period prompt", async () => {
    await api.askPortfolio({ question: "What are my exit levels?" });
    wrap("en", <AskPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Open conversation/ }));
    await screen.findByTestId("msg-answer");
    expect(screen.queryByTestId("needs-horizon")).toBeNull();
  });

  it("a declined question says so and gives no trade wording", async () => {
    wrap("en", <AskPage />);
    await ask("Should I buy NVDA?", en.ask.askLabel);
    expect(await screen.findByText(en.ask.declined)).toBeInTheDocument();
    const text = screen.getByTestId("msg-answer").textContent ?? "";
    expect(text).not.toMatch(VERDICT_EN);
  });

  it("opens a past conversation", async () => {
    await api.askPortfolio({ question: "How is my risk spread?" });
    wrap("en", <AskPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Open conversation/ }));
    expect(await screen.findByTestId("msg-answer")).toHaveTextContent("Technology");
    expect(screen.getByTestId("msg-user")).toHaveTextContent("How is my risk spread?");
  });

  it("deletes a conversation only after the confirm sheet", async () => {
    await api.askPortfolio({ question: "How is my risk spread?" });
    wrap("en", <AskPage />);
    fireEvent.click(await screen.findByRole("button", { name: /Delete conversation:/ }));
    const dlg = await screen.findByRole("dialog", { name: en.ask.deleteTitle });
    fireEvent.click(within(dlg).getByRole("button", { name: en.common.cancel }));
    expect(screen.getAllByTestId("conversation")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: /Delete conversation:/ }));
    fireEvent.click(within(await screen.findByRole("dialog", { name: en.ask.deleteTitle })).getByRole("button", { name: en.ask.deleteYes }));
    expect(await screen.findByTestId("no-conversations")).toBeInTheDocument();
    expect(await api.askConversations()).toEqual([]);
  });

  it("shows the disclaimer and no verdict words in either language", async () => {
    for (const [loc, m, re] of [["en", en, VERDICT_EN], ["he", he, VERDICT_HE]] as const) {
      const { unmount } = wrap(loc, <AskPage />);
      await screen.findByLabelText(m.ask.askLabel);
      expect(screen.getByText(m.disclaimer.footer)).toBeInTheDocument();
      expect(screen.getByTestId("root").textContent).not.toMatch(re);
      expect(screen.getByTestId("root")).toHaveAttribute("dir", loc === "he" ? "rtl" : "ltr");
      unmount();
    }
  });
});

describe("stripCites", () => {
  it("drops the inline [tool:...] tags; the tools are shown as chips instead", () => {
    expect(stripCites("LUMI.TA has no horizon set. [tool:get_exit_levels]")).toBe("LUMI.TA has no horizon set.");
    expect(stripCites("Top: AAPL [tool:get_holdings], then MSFT.")).toBe("Top: AAPL, then MSFT.");
  });
});
