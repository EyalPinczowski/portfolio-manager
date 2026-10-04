"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { toExposure, type ExposureItem } from "@/lib/api";
import { formatWeight } from "@/lib/format";
import { holdingHref } from "@/lib/routes";
import { Link } from "@/i18n/navigation";
import { useHeatmap, useHoldings, usePortfolios, useXray } from "@/lib/hooks";
import { AppShell } from "./AppShell";
import { Heatmap } from "./Heatmap";
import { XrayRules } from "./XrayRules";
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
            <div className="flex justify-between text-sm"><span>{i.name}</span><span className="tabular-nums" dir="ltr">{formatWeight(i.weight_pct, locale)}</span></div>
            <div className="h-2 rounded bg-surface-2" aria-hidden="true">
              <div className="h-2 rounded bg-brand" style={{ width: `${Math.min(100, i.weight_pct)}%` }} />
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
  const holdings = useHoldings(id, portfolios);
  if (!portfolios) return <p role="status">{c("loading")}</p>;
  const x = xray.data;
  const home = x ? Number(x.home_bias?.israel_pct ?? 0) : 0;
  const heldIds = new Map((holdings.data ?? []).map((h) => [h.symbol, h.id]));
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
                  <li key={`${b.rule}-${b.symbol ?? ""}-${b.value}`} className="rounded-xl border border-amber-500 bg-amber-50 p-3 text-sm dark:bg-amber-950/40">
                    <p className="font-semibold">⚠ {t.has(`rule.${b.rule}`) ? t(`rule.${b.rule}`) : b.rule}: {t("breachDetail", { value: formatWeight(b.value, locale), limit: formatWeight(b.limit, locale) })}</p>
                    <p>{b.why}</p>
                    {b.symbol && (
                      heldIds.has(b.symbol)
                        ? <Link href={holdingHref(heldIds.get(b.symbol)!)} className="font-semibold text-brand-text underline" dir="ltr">{b.symbol}</Link>
                        : <span className="font-semibold" dir="ltr">{b.symbol}</span>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </section>
          {id !== null && <XrayRules pid={id} rules={x.rules} onChanged={() => void xray.mutate()} />}
          <div className="grid gap-3 md:grid-cols-2">
            <Bars title={t("concentration")} items={x.concentration.map((r) => ({ name: r.symbol, weight_pct: r.weight_pct }))} />
            <Bars title={t("currency")} items={toExposure(x.currency_exposure)} />
            <Bars title={t("country")} items={toExposure(x.country_exposure)} />
            <Bars title={t("sector")} items={toExposure(x.sector_exposure)} />
          </div>
          <p className="card text-sm"><span className="font-semibold">{t("homeBias")}: </span><span className="tabular-nums" dir="ltr">{formatWeight(home, locale)}</span></p>
        </>
      )}
      <section className="card space-y-2" aria-label={t("heatmap")}>
        <h2 className="text-lg font-bold">{t("heatmap")}</h2>
        <p className="text-sm text-muted">{t("heatmapHint")}</p>
        {heat.data ? <Heatmap items={heat.data} /> : <p role="status">{c("loading")}</p>}
      </section>
    </>
  );
}

export function XrayPage() {
  return <AppShell><Body /></AppShell>;
}
