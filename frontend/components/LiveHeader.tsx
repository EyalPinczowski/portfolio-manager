"use client";
import { useLocale, useTranslations } from "next-intl";
import type { Summary } from "@/lib/api";
import { formatDate, formatMoney, formatTime } from "@/lib/format";
import { MarketBadges } from "./MarketBadges";
import { PnlText } from "./Pnl";

export function LiveHeader({ s }: { s: Summary }) {
  const t = useTranslations("header");
  const locale = useLocale();
  const sinceLabel = s.since_start_date ? t("sinceStart", { date: formatDate(s.since_start_date) }) : t("sinceStartNoDate");
  return (
    <section className="card space-y-3" aria-label={t("totalValue")}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-sm text-slate-600 dark:text-slate-400">{t("totalValue")}</p>
          <p className="text-3xl font-bold tabular-nums" dir="ltr">{formatMoney(s.value.ils, "ILS", locale)}</p>
          <p className="text-lg text-slate-700 tabular-nums dark:text-slate-300" dir="ltr">{formatMoney(s.value.usd, "USD", locale)}</p>
        </div>
        <MarketBadges markets={s.markets} />
      </div>
      <dl className="grid gap-3 sm:grid-cols-2">
        <div>
          <dt className="text-sm text-slate-600 dark:text-slate-400">{t("dayPnl")}</dt>
          <dd className="font-semibold">
            <PnlText value={s.day_pnl.ils} pct={s.day_pnl.pct} currency="ILS" locale={locale} />
            <br />
            <PnlText value={s.day_pnl.usd} currency="USD" locale={locale} className="text-sm font-normal" />
          </dd>
        </div>
        <div>
          <dt className="text-sm text-slate-600 dark:text-slate-400">{sinceLabel}</dt>
          <dd className="font-semibold">
            <PnlText value={s.since_start_pnl.ils} pct={s.since_start_pnl.pct} currency="ILS" locale={locale} />
            <br />
            <PnlText value={s.since_start_pnl.usd} currency="USD" locale={locale} className="text-sm font-normal" />
          </dd>
        </div>
      </dl>
      {s.fx_stale && <p role="status" className="rounded-lg bg-amber-100 px-3 py-2 text-sm font-medium text-amber-900 dark:bg-amber-950 dark:text-amber-200">{t("fxStale")}</p>}
      <p className="text-xs text-slate-600 dark:text-slate-400">{t("lastUpdated", { time: formatTime(s.as_of, locale) })}</p>
    </section>
  );
}
