"use client";
import useSWR, { type SWRConfiguration } from "swr";
import { api, type AnalyzeQuery, type ExitReviewIn, type Holding, type Horizon, type Portfolio, type RiskPresetName } from "./api";
import { PORTFOLIO_CHOICE_KEY } from "./session";

/** Polling interval for live data (spec: SWR polling every 60 s). */
export const POLL_MS = 60_000;
const cfg: SWRConfiguration = { refreshInterval: POLL_MS, revalidateOnFocus: true };

export type PortfolioRef = number | "combined";

export const useMe = () => useSWR("me", () => api.me(), { revalidateOnFocus: false });
export const usePortfolios = () => useSWR("portfolios", () => api.portfolios(), cfg);
export const useSummary = (id: PortfolioRef | null) =>
  useSWR(id === null ? null : ["summary", id], () => api.summary(id as PortfolioRef), cfg);

/** Holdings for one portfolio, or all portfolios merged (weights recomputed) for "combined". */
export function useHoldings(id: PortfolioRef | null, portfolios: Portfolio[] | undefined) {
  const ids = id === "combined" ? (portfolios ?? []).map((p) => p.id) : id === null ? null : [id];
  return useSWR<Holding[]>(
    ids && ids.length > 0 ? ["holdings", ...ids] : null,
    async () => {
      const lists = await Promise.all((ids ?? []).map((i) => api.holdings(i)));
      const all = lists.flat();
      if (id !== "combined") return all;
      const total = all.reduce((a, h) => a + h.value_ils, 0) || 1;
      return all.map((h) => ({ ...h, weight_pct: (h.value_ils / total) * 100 }));
    },
    cfg,
  );
}
export const useScorecard = (hid: number) => useSWR(["scorecard", hid], () => api.scorecard(hid), cfg);
/** `horizon` set = a what-if override; null = the holding's own (the server answers needs_horizon if it has none). */
export const useExitLevels = (hid: number, horizon: Horizon | null, risk: RiskPresetName | null = null) =>
  useSWR(["exit-levels", hid, horizon, risk], () => api.exitLevels(hid, { horizon, risk }), cfg);
export const useExitReview = (pid: number | null, body: ExitReviewIn = {}) =>
  useSWR(pid === null ? null : ["exit-review", pid, body.horizon ?? null, body.risk ?? null], () => api.exitReview(pid as number, body), cfg);
/** Analysis of any ticker. Results live only in the SWR memory cache (never saved to storage). Not polled: it is on demand. */
export const useAnalyze = (symbol: string | null, q: AnalyzeQuery) =>
  useSWR(
    symbol ? ["analyze", symbol, q.portfolioId ?? null, q.amount ?? null, q.currency ?? null, q.horizon ?? null, q.risk ?? null] : null,
    () => api.analyze(symbol as string, q),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );
export const useSearchHistory = () => useSWR("search-history", () => api.searchHistory(), { revalidateOnFocus: true });
export const useWatchlist = () => useSWR("watchlist", () => api.watchlist(), cfg);
export const useSettings = () => useSWR("settings", () => api.settings(), { revalidateOnFocus: false, shouldRetryOnError: false });
export const useTelegramStatus = () => useSWR("telegram-status", () => api.telegramStatus(), { revalidateOnFocus: true, shouldRetryOnError: false });
/** Admin probe: a member gets 403 (data undefined, error set), so the Admin section stays hidden. */
export const useAdminUsers = () => useSWR("admin-users", () => api.adminUsers(), { revalidateOnFocus: false, shouldRetryOnError: false });
export const useAdminInvites = (enabled: boolean) => useSWR(enabled ? "admin-invites" : null, () => api.adminInvites(), { revalidateOnFocus: false, shouldRetryOnError: false });
export const useAdminLlmUsage = () => useSWR("admin-llm-usage", () => api.adminLlmUsage(), { revalidateOnFocus: false, shouldRetryOnError: false });
export const useAlerts = () => useSWR("alerts", () => api.alerts(), cfg);
/** Post-mortem of one portfolio and period (dates are ISO yyyy-mm-dd or null). On demand, not polled. */
export const usePostmortem = (pid: number | null, start: string | null, end: string | null) =>
  useSWR(pid === null ? null : ["post-mortem", pid, start, end], () => api.postmortem(pid as number, { start, end }), { revalidateOnFocus: false, shouldRetryOnError: false });
export const useXray = (pid: number | null) => useSWR(pid === null ? null : ["xray", pid], () => api.xray(pid as number), cfg);
export const useXrayRules = (pid: number | null) => useSWR(pid === null ? null : ["xray-rules", pid], () => api.xrayRules(pid as number), cfg);
/** Members-only track record: on demand, not polled. */
export const useTrackRecord = () => useSWR("track-record", () => api.trackRecord(), { revalidateOnFocus: false, shouldRetryOnError: false });
export const useHeatmap = (pid: number | null) => useSWR(pid === null ? null : ["heatmap", pid], () => api.heatmap(pid as number), cfg);

const KEY = PORTFOLIO_CHOICE_KEY;
export function loadPortfolioChoice(): PortfolioRef | null {
  try {
    const v = window.localStorage.getItem(KEY);
    if (v === "combined") return "combined";
    if (v && /^\d+$/.test(v)) return Number(v);
  } catch { /* storage unavailable */ }
  return null;
}
export function savePortfolioChoice(v: PortfolioRef): void {
  try { window.localStorage.setItem(KEY, String(v)); } catch { /* ignore */ }
}

/** Dividend calendar of one portfolio. Slow data: on demand, not polled. */
export const useDividends = (pid: number | null) =>
  useSWR(pid === null ? null : ["dividends", pid], () => api.dividends(pid as number), { revalidateOnFocus: false, shouldRetryOnError: false });
/** Fund search (needs at least 2 characters). Not polled. */
export const useFundSearch = (q: string) =>
  useSWR(q.trim().length >= 2 ? ["fund-search", q.trim()] : null, () => api.searchFunds(q.trim()), { revalidateOnFocus: false, shouldRetryOnError: false });
export const useFund = (fundId: string | null) =>
  useSWR(fundId ? ["fund", fundId] : null, () => api.fund(fundId as string), { revalidateOnFocus: false, shouldRetryOnError: false });
