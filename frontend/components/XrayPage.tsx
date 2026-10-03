"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { toExposure, type ExposureItem } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { useHeatmap, usePortfolios, useXray } from "@/lib/hooks";
import { AppShell } from "./AppShell";
import { Heatmap } from "./Heatmap";
import { PortfolioSwitcher } from "./PortfolioSwitcher";

function Bars({ title, items }: { title: string; items: ExposureItem[] }) {
  const locale = useLocale();
  const sorted = [...items].sort((a, b) => b.weight_pct - a.weight_pct);
  return (
    <section className="card space-y-2" aria-label={title}>
      <h3 className="font-semibold">{title}</h3>
      <ul className="space-y-2">
        {sorted.map((i) => (
          <li key={i.name}>
            <div className="flex justify-between text-sm"><span>{i.name}</span><span className="tabular-nums" dir="ltr">{formatNumber(i.weight_pct, locale, 1)}%</span></div>
            <div className="h-2 rounded bg-slate-200 dark:bg-slate-700" aria-hidden="true">
              <div className="h-2 rounded bg-blue-700 dark:bg-blue-400" style={{ width: `${Math.min(100, i.weight_pct)}%` }} />
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Body() {
  const t = useTranslations("xray");
  const c = useTranslations("common");
  const locale = useLocale();
  const { data: portfolios } = usePortfolios();
  const [pid, setPid] = useState<number | null>(null);
  const id = pid ?? portfolios?.[0]?.id ?? null;
  const xray = useXray(id);
  const heat = useHeatmap(id);
  if (!portfolios) return <p role="status">{c("loading")}</p>;
  const x = xray.data;
  const home = x ? (typeof x.home_bias === "number" ? x.home_bias : Number(x.home_bias?.israel_pct ?? 0)) : 0;
  return (
    <>
      <h1 className="text-2xl font-bold">{t("title")}</h1>
      {id !== null && <PortfolioSwitcher portfolios={portfolios} value={id} onChange={(v) => setPid(v as number)} allowCombined={false} />}
      {xray.error && <p role="alert">{c("errorLoad")}</p>}
      {x && (
        <>
          <section className="card space-y-2" aria-label={t("breaches")}>
            <h2 className="text-lg font-bold">{t("breaches")}</h2>
            {x.breaches.length === 0 ? <p className="text-sm">{t("noBreaches")}</p> : (
              <ul className="space-y-2">
                {x.breaches.map((b) => (
                  <li key={`${b.rule}-${b.value}`} className="rounded-xl border border-amber-500 bg-amber-50 p-3 text-sm dark:bg-amber-950/40">
                    <p className="font-semibold">⚠ {t.has(`rule.${b.rule}`) ? t(`rule.${b.rule}`) : b.rule}: {t("breachDetail", { value: `${formatNumber(b.value, locale, 1)}%`, limit: `${formatNumber(b.limit, locale, 1)}%` })}</p>
                    <p>{b.why}</p>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <div className="grid gap-3 md:grid-cols-2">
            <Bars title={t("concentration")} items={x.concentration.map((r) => ({ name: r.symbol, weight_pct: r.weight_pct }))} />
            <Bars title={t("currency")} items={toExposure(x.currency_exposure)} />
            <Bars title={t("country")} items={toExposure(x.country_exposure)} />
            <Bars title={t("sector")} items={toExposure(x.sector_exposure)} />
          </div>
          <p className="card text-sm"><span className="font-semibold">{t("homeBias")}: </span><span className="tabular-nums" dir="ltr">{formatNumber(home, locale, 1)}%</span></p>
        </>
      )}
      <section className="card space-y-2" aria-label={t("heatmap")}>
        <h2 className="text-lg font-bold">{t("heatmap")}</h2>
        <p className="text-sm text-slate-600 dark:text-slate-400">{t("heatmapHint")}</p>
        {heat.data ? <Heatmap items={heat.data} /> : <p role="status">{c("loading")}</p>}
      </section>
    </>
  );
}

export function XrayPage() {
  return <AppShell><Body /></AppShell>;
}
