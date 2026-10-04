"use client";
import useSWR, { type SWRConfiguration } from "swr";
import { api, type ExitReviewIn, type Holding, type Horizon, type Portfolio, type RiskPresetName } from "./api";
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
export const useAlerts = () => useSWR("alerts", () => api.alerts(), cfg);
export const useXray = (pid: number | null) => useSWR(pid === null ? null : ["xray", pid], () => api.xray(pid as number), cfg);
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
