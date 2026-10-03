"use client";
import { useTranslations } from "next-intl";
import type { MarketKey, Summary } from "@/lib/api";

export function MarketBadges({ markets }: { markets: Summary["markets"] }) {
  const t = useTranslations("header");
  return (
    <ul className="flex flex-wrap gap-2" aria-label={t("markets")}>
      {(["TASE", "US", "CRYPTO"] as MarketKey[]).map((k) => {
        const open = markets[k]?.open;
        return (
          <li
            key={k}
            className={`chip gap-1.5 border ${open ? "border-emerald-600 text-emerald-800 dark:text-emerald-300" : "border-slate-400 text-slate-600 dark:text-slate-400"}`}
          >
            <span aria-hidden="true" className={`inline-block h-2 w-2 rounded-full ${open ? "bg-emerald-600" : "bg-slate-400"}`} />
            {t(`market.${k}`)}: {open ? t("open") : t("closed")}
          </li>
        );
      })}
    </ul>
  );
}
