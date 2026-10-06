import type { Horizon } from "./api";

/** Why a period is suggested. Each key has a plain sentence in `guide.suggest.reason.*`. */
export type SuggestReason = "fund" | "crypto" | "stockLong" | "stockMid" | "stockShort";
export interface HorizonSuggestion { horizon: Horizon; reason: SuggestReason }

const STOCK_BY_PRESET: Record<string, HorizonSuggestion> = {
  very_conservative: { horizon: "1y", reason: "stockLong" },
  conservative: { horizon: "1y", reason: "stockLong" },
  balanced: { horizon: "6m", reason: "stockMid" },
  balanced_aggressive: { horizon: "6m", reason: "stockMid" },
  aggressive: { horizon: "3m", reason: "stockShort" },
  very_aggressive: { horizon: "3m", reason: "stockShort" },
};

/**
 * A starting point for the holding period, display only. It is never applied by itself: the user taps to use it.
 * Fund, ETF, bond -> 1y; crypto -> 3m; stock -> follows the portfolio's risk preset. Anything else (cash, unknown): no suggestion.
 */
export function suggestHorizon(assetType: string | null | undefined, preset: string | null | undefined): HorizonSuggestion | null {
  switch (assetType) {
    case "etf":
    case "fund":
    case "bond":
      return { horizon: "1y", reason: "fund" };
    case "crypto":
      return { horizon: "3m", reason: "crypto" };
    case "stock":
      return (preset && STOCK_BY_PRESET[preset]) || null;
    default:
      return null;
  }
}
