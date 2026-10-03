"use client";
import { useLocale, useTranslations } from "next-intl";
import type { Summary } from "@/lib/api";
import { formatDate, formatMoney, formatTime } from "@/lib/format";
import { MarketBadges } from "./MarketBadges";
import { PnlText } from "./Pnl";

function Change({ label, ils, usd, pct, locale }: { label: string; ils: number; usd: number; pct: number; locale: string }) {
  return (
    <div className="rounded-xl bg-surface-2 p-3">
      <dt className="text-caption text-muted">{label}</dt>
      <dd className="mt-1 font-semibold">
        <PnlText value={ils} pct={pct} currency="ILS" locale={locale} />
        <span className="block"><PnlText value={usd} currency="USD" locale={locale} className="text-sm font-normal" /></span>
      </dd>
    </div>
  );
}

/** Top of the home screen: total value (₪ large, $ small), today's change, and the change since the user started. */
export function LiveHeader({ s, anyPriceStale = false }: { s: Summary; anyPriceStale?: boolean }) {
  const t = useTranslations("header");
  const locale = useLocale();
  const sinceLabel = s.since_start_date ? t("sinceStart", { date: formatDate(s.since_start_date) }) : t("sinceStartNoDate");
  return (
    <section className="card space-y-3" aria-label={t("totalValue")}>
      <div>
        <p className="text-sm text-muted">{t("totalValue")}</p>
        <p className="flex flex-wrap items-baseline gap-x-3">
          <span className="text-hero tabular-nums" dir="ltr">{formatMoney(s.value.ils, "ILS", locale, { compact: true })}</span>
          <span className="text-base text-muted tabular-nums" dir="ltr">{formatMoney(s.value.usd, "USD", locale, { compact: true })}</span>
        </p>
      </div>
      <dl className="grid gap-2 sm:grid-cols-2">
        <Change label={t("dayPnl")} ils={s.day_pnl.ils} usd={s.day_pnl.usd} pct={s.day_pnl.pct} locale={locale} />
        <Change label={sinceLabel} ils={s.since_start_pnl.ils} usd={s.since_start_pnl.usd} pct={s.since_start_pnl.pct} locale={locale} />
      </dl>
      <MarketBadges markets={s.markets} />
      {s.fx_stale && <p role="status" className="rounded-lg bg-warn-bg px-3 py-2 text-sm font-medium text-warn-fg">{t("fxStale")}</p>}
      <p className="text-caption text-muted">
        {t("pricesAsOf", { time: formatTime(s.as_of, locale) })}
        {anyPriceStale && <> · {t("someStale")}</>}
      </p>
    </section>
  );
}
