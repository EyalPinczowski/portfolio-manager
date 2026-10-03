"use client";
import { useMemo } from "react";
import { useLocale, useTranslations } from "next-intl";
import type { Summary } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { PnlBarChart, SinceStartChart } from "./charts";
import { PnlText } from "./Pnl";

export function PnlStrip({ s }: { s: Summary }) {
  const t = useTranslations("pnl");
  const locale = useLocale();
  const weekly = useMemo(() => s.weekly_bars.map((b) => ({ time: b.week_start, value: b.pct })), [s.weekly_bars]);
  const monthly = useMemo(() => s.monthly_bars.map((b) => ({ time: `${b.month}-01`, value: b.pct })), [s.monthly_bars]);
  const series = useMemo(() => s.since_start_series.map((p) => ({ time: p.date, you: p.pct, sp500: p.sp500_pct, ta125: p.ta125_pct })), [s.since_start_series]);
  const names = useMemo(() => ({ you: t("you"), sp500: t("sp500"), ta125: t("ta125") }), [t]);
  const sinceLabel = s.since_start_date ? t("sinceStartChart", { date: formatDate(s.since_start_date) }) : t("sinceStartChartNoDate");
  const hasSp = s.since_start_series.some((p) => typeof p.sp500_pct === "number");
  const hasTa = s.since_start_series.some((p) => typeof p.ta125_pct === "number");
  const weekLabel = s.week_start ? t("weekFrom", { date: formatDate(s.week_start) }) : t("weekSundayStart");
  const tile = (label: string, p: Summary["week_pnl"], sub?: string) => (
    <div className="card">
      <p className="text-sm text-slate-600 dark:text-slate-400">{label}</p>
      {sub && <p className="text-xs text-slate-600 dark:text-slate-400">{sub}</p>}
      <p className="text-lg font-bold"><PnlText value={p.ils} currency="ILS" locale={locale} /></p>
      <p className="text-sm font-semibold"><PnlText pct={p.pct} locale={locale} /></p>
    </div>
  );
  return (
    <section aria-label={t("title")} className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        {tile(t("week"), s.week_pnl, weekLabel)}
        {tile(t("month"), s.month_pnl)}
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <div className="card">
          <h3 className="mb-2 text-sm font-semibold">{t("weekly")}</h3>
          <p className="mb-2 text-xs text-slate-600 dark:text-slate-400">{t("weeksStartSunday")}</p>
          <PnlBarChart data={weekly} label={t("weekly")} />
        </div>
        <div className="card">
          <h3 className="mb-2 text-sm font-semibold">{t("monthly")}</h3>
          <PnlBarChart data={monthly} label={t("monthly")} />
        </div>
      </div>
      <div className="card">
        <h3 className="mb-2 text-sm font-semibold">{sinceLabel}</h3>
        {series.length === 0 ? (
          <p className="text-sm text-slate-600 dark:text-slate-400" role="status">{t("noHistory")}</p>
        ) : (
          <>
            <SinceStartChart data={series} label={sinceLabel} names={names} />
            <ul className="mt-2 flex flex-wrap gap-3 text-xs" dir="ltr">
              <li><span className="font-bold text-blue-700 dark:text-blue-400">━</span> {names.you}</li>
              {hasSp && <li><span className="font-bold text-amber-700 dark:text-amber-400">━</span> {names.sp500}</li>}
              {hasTa && <li><span className="font-bold text-purple-700 dark:text-purple-400">━</span> {names.ta125}</li>}
            </ul>
          </>
        )}
      </div>
    </section>
  );
}
