/* Mock "Ask my portfolio" and Investment Committee responses (NEXT_PUBLIC_API_MOCK=1). Shapes follow backend/openapi.json. */
import type { AskConversation, AskConversationDetail, AskMessage, CommitteeOut, PortfolioAskOut } from "./api";
import { ASK_MAX_QUESTION_CHARS } from "./config";
import { ApiError } from "./errors";

const AT = "2026-10-03T08:55:00Z";
type Stored = AskConversationDetail;
let convs: Stored[] = [];
let nextConv = 1;
let nextMsg = 1;
/** A symbol the mock holds without a horizon (LUMI.TA in lib/mock.ts). */
const NO_HORIZON_SYMBOL = "LUMI.TA";
const NO_HORIZON_HOLDING = { symbol: NO_HORIZON_SYMBOL, holding_id: 2, portfolio_id: 1 };

export function resetMockAsk(): void { convs = []; nextConv = 1; nextMsg = 1; }

const msg = (role: "user" | "assistant", content: string, extra: Partial<AskMessage> = {}): AskMessage => ({
  id: nextMsg++, role, content, cites: [], tools_called: [], source: role === "assistant" ? "template" : null, declined: false, created_at: AT, ...extra,
});

function answerFor(q: string): Partial<AskMessage> & { content: string } {
  if (/\b(buy|sell|should i|trade)\b/i.test(q)) {
    return { content: "I can only describe your data. Your largest holding is Teva at about 14% of the portfolio.", tools_called: ["get_holdings"], cites: ["tool:get_holdings"], declined: true };
  }
  if (/exit|stop|horizon|take.?profit/i.test(q)) {
    return { content: `${NO_HORIZON_SYMBOL} has no horizon set, so no exit levels are shown. [tool:get_exit_levels]`, tools_called: ["get_exit_levels"], cites: ["tool:get_exit_levels"] };
  }
  if (/risk|exposure|sector|country|concentrat/i.test(q)) {
    return { content: "Technology is your largest sector at 38% and Israel your largest country at 61%. [tool:get_xray]", tools_called: ["get_xray"], cites: ["tool:get_xray"] };
  }
  if (/hello|hi$/i.test(q.trim())) return { content: "I answer questions about your own portfolio. Try asking about your holdings or exposure.", tools_called: [], cites: [] };
  return { content: "You hold 8 positions worth about 410,000 ILS in total. The largest is Teva. [tool:get_holdings]", tools_called: ["get_summary", "get_holdings"], cites: ["tool:get_summary", "tool:get_holdings"] };
}

export function mockAskPost(b: { question?: string; conversation_id?: number | null; portfolio_id?: number | null }): PortfolioAskOut {
  const q = String(b.question ?? "").trim();
  if (!q || q.length > ASK_MAX_QUESTION_CHARS) throw new ApiError(422, "Ask a question of 1 to 500 characters.", undefined, { code: "bad_question" });
  if (/\bratelimit\b/i.test(q)) throw new ApiError(429, "Too many requests", 3600, { code: "rate_limited" });
  let conv = b.conversation_id == null ? undefined : convs.find((c) => c.id === b.conversation_id);
  if (b.conversation_id != null && !conv) throw new ApiError(404, "Conversation not found.", undefined, { code: "conversation_not_found" });
  if (!conv) {
    conv = { id: nextConv++, title: q.slice(0, 60), portfolio_id: b.portfolio_id ?? null, created_at: AT, updated_at: AT, messages: [] };
    convs = [conv, ...convs];
  }
  const a = answerFor(q);
  const qm = msg("user", q);
  const am = msg("assistant", a.content, a);
  const needs = a.tools_called?.includes("get_exit_levels") ? [NO_HORIZON_HOLDING] : [];
  conv.messages.push(qm, am);
  return { conversation_id: conv.id, question: qm, answer: am, declined: am.declined, notes: [], needs_horizon: needs, disclaimer: "Not financial advice." };
}

export const mockAskConversations = (): AskConversation[] => convs.map((c) => ({ id: c.id, title: c.title, portfolio_id: c.portfolio_id, created_at: c.created_at, updated_at: c.updated_at }));

export function mockAskConversation(id: number): AskConversationDetail {
  const c = convs.find((x) => x.id === id);
  if (!c) throw new ApiError(404, "Conversation not found.", undefined, { code: "conversation_not_found" });
  return c;
}

export function mockAskDelete(id: number): undefined {
  if (!convs.some((x) => x.id === id)) throw new ApiError(404, "Conversation not found.", undefined, { code: "conversation_not_found" });
  convs = convs.filter((x) => x.id !== id);
  return undefined;
}

const role = <T,>(name: string, value: T, source: "llm" | "cache" | "template" = "llm", status: "ok" | "no_coverage" = "ok") => ({ role: name, value, source, status, confidence: 0.5, citations: [], prompt_tokens: 0, budget: 0, notes: [] });

/** The basic-summary case: no AI model, no profile, no news, few checks with data (an ETF, for example). */
export function mockCommitteeEmpty(symbol: string): CommitteeOut {
  const base = mockCommittee(symbol);
  const empty = { adjustment: 0, adjustment_reason: "", adjustment_code: "no_adjustment", responses: [] };
  return {
    ...base, llm_used: false, data_completeness_pct: 19, data_completeness_low: true,
    report: {
      symbol: base.symbol,
      profile: role("company_profile", { claims: [] }, "template", "no_coverage"),
      news: role("news", { items: [] }, "template", "no_coverage"),
      bear: role("bear", { risks: [{ code: "no_chart_signal", text: "", severity: 2, chunk_ids: [], fact_refs: ["score"], what_would_invalidate: "" }] }, "template"),
      cio: role("cio", empty, "template"),
      cio_score: { base_score: null, adjustment: 0, adjusted_score: null, assessment: empty },
    },
  } as CommitteeOut;
}

export function mockCommittee(symbol: string): CommitteeOut {
  const sym = symbol.toUpperCase();
  if (sym === "RATELIMIT") throw new ApiError(429, "Too many requests", 3600, { code: "rate_limited" });
  if (sym === "NOTFOUNDX") throw new ApiError(404, "Symbol not found");
  const assessment = {
    adjustment: -4,
    adjustment_code: "", adjustment_reason: "Two of the three risks stay open, so the chart score is lowered a little.",
    responses: [
      { code: "", risk_index: 0, stance: "rebutted" as const, reason: "Revenue is spread over several regions, which limits the effect of one market.", chunk_ids: [] },
      { code: "", risk_index: 1, stance: "accepted" as const, reason: "Debt is above the sector median and rates are still high.", chunk_ids: [] },
      { code: "", risk_index: 2, stance: "unresolved" as const, reason: "The filing does not say how much of the revenue comes from the largest customer.", chunk_ids: [] },
    ],
  };
  return {
    symbol: sym, generated_at: AT, cached: false, llm_used: true, runs_left_today: 9, runs_per_day: 10, launch_gate_open: false,
    launch_gate_reasons: ["No backtest has been run for the active weights configuration.", "Paper trading has run 1.0 of 4 required weeks."],
    launch_gate_codes: [{ code: "no_backtest", params: {} }, { code: "paper_weeks", params: { weeks_running: 1, weeks: 4, recorded: 3 } }],
    data_completeness_pct: 82, data_completeness_low: false,
    disclaimer: "Not financial advice.",
    report: {
      symbol: sym,
      profile: role("company_profile", { claims: [
        { topic: "business" as const, text: "Develops and sells software and services to enterprise customers.", chunk_ids: [] },
        { topic: "segments" as const, text: "Two segments: subscriptions and professional services.", chunk_ids: [] },
        { topic: "competitors" as const, text: "Competes with several larger global vendors.", chunk_ids: [] },
      ] }),
      news: role("news", { items: [
        { text: "Quarterly revenue came in above the prior year.", tone: "positive" as const, chunk_ids: [] },
        { text: "A new product line was announced for next year.", tone: "neutral" as const, chunk_ids: [] },
      ] }),
      bear: role("bear", { risks: [
        { code: "", text: "Revenue depends on a small number of markets.", severity: 3, chunk_ids: [], fact_refs: [], what_would_invalidate: "Growth outside the main markets above 20% for two quarters." },
        { code: "", text: "Debt is high compared with the sector.", severity: 4, chunk_ids: [], fact_refs: [], what_would_invalidate: "" },
        { code: "", text: "One customer may account for a large share of revenue.", severity: 2, chunk_ids: [], fact_refs: [], what_would_invalidate: "" },
      ] }),
      cio: role("cio", assessment),
      cio_score: { base_score: 31.5, adjustment: -4, adjusted_score: 27.5, assessment },
    },
  } as CommitteeOut;
}
