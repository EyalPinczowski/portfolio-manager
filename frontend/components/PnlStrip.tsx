"use client";
import { useMemo } from "react";
import { useLocale, useTranslations } from "next-intl";
import type { Summary } from "@/lib/api";
import { formatDate, mainFirst } from "@/lib/format";
import { palette, PnlBarChart, SinceStartChart } from "./charts";
import { Link } from "@/i18n/navigation";
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
  const pal = palette();
  const tile = (label: string, p: Summary["week_pnl"], sub?: string) => (
    <div className="card">
      <p className="text-sm text-muted">{label}</p>
      {sub && <p className="text-caption text-muted">{sub}</p>}
      <p className="whitespace-nowrap text-base font-bold sm:text-lg"><PnlText value={mainFirst(p.ils, p.usd).main.v} currency={mainFirst(p.ils, p.usd).main.cur} locale={locale} /></p>
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
          <p className="mb-2 text-caption text-muted">{t("weeksStartSunday")}</p>
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
          <p className="text-sm text-muted" role="status">{t("noHistory")}</p>
        ) : (
          <>
            <SinceStartChart data={series} label={sinceLabel} names={names} />
            <ul className="mt-2 flex flex-wrap gap-3 text-xs" dir="ltr">
              <li><span className="font-bold" style={{ color: pal.you }}>━</span> {names.you}</li>
              {hasSp && <li><span className="font-bold" style={{ color: pal.sp }}>━</span> {names.sp500}</li>}
              {hasTa && <li><span className="font-bold" style={{ color: pal.ta }}>━</span> {names.ta125}</li>}
            </ul>
          </>
        )}
      </div>
      <Link href="/postmortem" className="inline-flex min-h-11 items-center text-sm font-medium text-brand-text hover:underline">{t("postmortemLink")}</Link>
    </section>
  );
}
