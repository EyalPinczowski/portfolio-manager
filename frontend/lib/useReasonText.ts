"use client";
import { useTranslations } from "next-intl";
import { INPUT_KEYS, matchReason } from "./reasonText";

/** Returns translators for the backend's fixed English reasons. Unknown text comes back unchanged. */
const BENCH: Record<string, string> = { "^GSPC": "S&P 500", "^IXIC": "NASDAQ", "^TA125.TA": "TA-125", "TA35.TA": "TA-35" };

export function useReasonText() {
  const t = useTranslations("reason");
  const h = useTranslations("holding");
  const a = useTranslations("analyze");
  const sig = (n: string) => (h.has(`signal.${n}`) ? h(`signal.${n}`) : n);
  const text = (raw: string): string => {
    const ms = matchReason(raw);
    if (!ms) return raw;
    return ms
      .map(({ key, values }) => {
        const v = { ...values };
        if (key === "techSummary") v.cats = v.cats.replace(/\b(trend|momentum|volatility|volume)\b/g, (w) => t(`cat.${w as "trend"}`));
        if (key === "totalSummary") v.names = v.names === "none" ? t("none") : v.names.split(", ").map(sig).join(", ");
        if (key === "noNearLevelParts") v.parts = v.parts.replace(/\b(support|resistance)\b/g, (w) => a(w as "support"));
        if (v.bench) v.bench = BENCH[v.bench] ?? v.bench;
        return t(key as "priceVsSma", v);
      })
      .join(" ");
  };
  const input = (k: string): string => (INPUT_KEYS.has(k) ? t(`input.${k as "close"}`) : k);
  return { text, input };
}
