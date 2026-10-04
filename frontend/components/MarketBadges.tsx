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
            className={`chip gap-1.5 border ${open ? "border-gain text-gain" : "border-line text-muted"}`}
          >
            <span aria-hidden="true" className={`inline-block h-2 w-2 rounded-full ${open ? "bg-gain" : "bg-muted"}`} />
            {t(`market.${k}`)}: {open ? t("open") : t("closed")}
          </li>
        );
      })}
    </ul>
  );
}
