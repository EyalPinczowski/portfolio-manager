"use client";
import { useLocale, useTranslations } from "next-intl";
import type { Holding, Summary } from "@/lib/api";
import { formatMoney, mainFirst } from "@/lib/format";
import { PnlText } from "./Pnl";

const finite = (n: number | null | undefined): n is number => typeof n === "number" && Number.isFinite(n);

export interface TotalPnl { ils: number; usd: number; pct: number }

/**
 * Total P&L across the holdings that have a cost basis. Holdings without a P&L (funds, no cost price) are left out
 * instead of being counted as zero. Returns null when no holding has one.
 */
export function totalPnl(holdings: Holding[]): TotalPnl | null {
  let ils = 0, usd = 0, valueIls = 0, n = 0;
  for (const h of holdings) {
    const p = h.pnl;
    if (!p || !finite(p.ils) || !finite(p.usd) || !finite(h.value_ils)) continue;
    ils += p.ils; usd += p.usd; valueIls += h.value_ils; n += 1;
  }
  const cost = valueIls - ils;
  if (n === 0 || cost <= 0) return null;
  return { ils, usd, pct: (ils / cost) * 100 };
}

/** Top of Home: total value, total P&L (amount and %) and today's change, the saved main currency first. */
export function PortfolioSummary({ s, holdings }: { s: Summary; holdings: Holding[] }) {
  const t = useTranslations("overview");
  const locale = useLocale();
  const total = mainFirst(s.value.ils, s.value.usd);
  const pnl = totalPnl(holdings);
  const tot = pnl ? mainFirst(pnl.ils, pnl.usd) : null;
  const day = mainFirst(s.day_pnl.ils, s.day_pnl.usd);
  return (
    <section className="card space-y-3" aria-label={t("summary")}>
      <div>
        <p className="text-sm text-muted">{t("totalValue")}</p>
        <p className="flex flex-wrap items-baseline gap-x-3">
          <span className="text-hero tabular-nums" dir="ltr" data-testid="total-main">{formatMoney(total.main.v, total.main.cur, locale, { compact: true })}</span>
          <span className="text-base text-muted tabular-nums" dir="ltr" data-testid="total-other">{formatMoney(total.other.v, total.other.cur, locale, { compact: true })}</span>
        </p>
      </div>
      <dl className="grid grid-cols-2 gap-2">
        <div className="rounded-xl bg-surface-2 p-3" data-testid="summary-total-pnl">
          <dt className="text-caption text-muted">{t("totalPnl")}</dt>
          <dd className="mt-1 font-semibold">
            {tot && pnl ? <PnlText value={tot.main.v} pct={pnl.pct} currency={tot.main.cur} locale={locale} /> : <span className="text-muted">—<span className="sr-only"> {t("noPnl")}</span></span>}
          </dd>
        </div>
        <div className="rounded-xl bg-surface-2 p-3" data-testid="summary-day">
          <dt className="text-caption text-muted">{t("today")}</dt>
          <dd className="mt-1 font-semibold"><PnlText value={day.main.v} pct={s.day_pnl.pct} currency={day.main.cur} locale={locale} /></dd>
        </div>
      </dl>
    </section>
  );
}
