"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import type { ExitReviewOut, ReviewRow } from "@/lib/api";
import { DASH, formatMoney, formatPct } from "@/lib/format";
import { loadPortfolioChoice, useExitReview, usePortfolios } from "@/lib/hooks";
import { holdingHref } from "@/lib/routes";
import { Link } from "@/i18n/navigation";
import { AppShell } from "./AppShell";
import { ChevronIcon } from "./icons";
import { PnlText } from "./Pnl";
import { PortfolioSwitcher } from "./PortfolioSwitcher";

const rank = (r: ReviewRow) => (r.status === "needs_horizon" ? 0 : r.status === "no_levels" ? 1 : 2);

/** needs_horizon first, then no_levels, then the biggest risk to the stop. */
export const sortRows = (rows: ReviewRow[]): ReviewRow[] =>
  [...rows].sort((a, b) => rank(a) - rank(b) || (b.risk_ils ?? 0) - (a.risk_ils ?? 0));

function Money2({ ils, usd }: { ils: number; usd: number }) {
  const locale = useLocale();
  return (
    <span dir="ltr" className="tabular-nums">
      {formatMoney(ils, "ILS", locale)} <span className="text-caption text-muted">({formatMoney(usd, "USD", locale)})</span>
    </span>
  );
}

function SymbolList({ title, symbols }: { title: string; symbols: string[] }) {
  const t = useTranslations("review");
  return (
    <div>
      <h3 className="font-semibold">{title}</h3>
      {symbols.length === 0 ? <p className="text-sm text-muted">{t("none")}</p> : (
        <ul className="flex flex-wrap gap-1.5" aria-label={title}>
          {symbols.map((s) => <li key={s} className="chip-neutral" dir="ltr">{s}</li>)}
        </ul>
      )}
    </div>
  );
}

function Totals({ r }: { r: ExitReviewOut }) {
  const t = useTranslations("review");
  const locale = useLocale();
  const x = r.totals;
  return (
    <section className="card space-y-4" aria-label={t("totalsTitle")}>
      <h2 className="text-heading">{t("totalsTitle")}</h2>
      <div>
        <p className="text-caption text-muted">{t("totalRisk")}</p>
        <p className="text-2xl font-bold"><Money2 ils={-Math.abs(x.total_risk_ils)} usd={-Math.abs(x.total_risk_usd)} /></p>
        <p className="text-sm text-muted">
          {t("ofLimit", { pct: formatPct(x.total_risk_pct_of_portfolio, locale), limit: formatPct(x.limit_pct, locale) })} · {t("withStops", { n: x.positions_with_stop })}
        </p>
        <p className={x.over_limit ? "chip-warn mt-1" : "chip-neutral mt-1"} role="status">{x.over_limit ? t("overLimit") : t("withinLimit")}</p>
      </div>
      <div>
        <h3 className="font-semibold">{t("topContrib")}</h3>
        {x.top_contributors.length === 0 ? <p className="text-sm text-muted">{t("none")}</p> : (
          <ol className="list-decimal space-y-1 ps-5 text-sm">
            {x.top_contributors.map((c) => (
              <li key={c.symbol}>
                <span dir="ltr" className="font-medium">{c.symbol}</span> · <Money2 ils={-Math.abs(c.risk_ils)} usd={-Math.abs(r.rows.find((w) => w.symbol === c.symbol)?.risk_usd ?? 0)} />
                {" "}<span className="text-muted">({t("share", { pct: formatPct(c.share_of_total_risk_pct, locale) })})</span>
              </li>
            ))}
          </ol>
        )}
      </div>
      <div>
        <h3 className="font-semibold">{t("noStopTitle")}</h3>
        {x.positions_without_stop.length === 0 ? <p className="text-sm text-muted">{t("none")}</p> : (
          <ul className="space-y-1 text-sm">
            {x.positions_without_stop.map((m) => (
              <li key={m.symbol}><span dir="ltr" className="font-medium">{m.symbol}</span> · <bdi dir="auto" className="text-muted">{m.reason}</bdi></li>
            ))}
          </ul>
        )}
      </div>
      <SymbolList title={t("tooTight")} symbols={x.stops_too_tight} />
      <SymbolList title={t("tooWide")} symbols={x.stops_too_wide} />
      <SymbolList title={t("smallerSize")} symbols={x.smaller_size_needed} />
    </section>
  );
}

function Row({ row }: { row: ReviewRow }) {
  const t = useTranslations("review");
  const e = useTranslations("exit");
  const locale = useLocale();
  const name = locale === "he" ? row.name_he : row.name_en;
  const has = row.status === "levels" && row.stop_price != null;
  return (
    <li>
      <Link href={holdingHref(row.holding_id)} aria-label={t("open", { name })} className="card flex flex-col gap-2 hover:border-brand" data-testid={`row-${row.status}`}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="break-words font-semibold">{name}</p>
            <p className="text-caption text-muted" dir="ltr">{row.symbol}</p>
          </div>
          <ul className="flex flex-wrap justify-end gap-1.5" aria-label={t("rowsTitle")}>
            {row.status === "needs_horizon" && <li className="chip-warn">{t("badge.needs_horizon")}</li>}
            {row.status === "no_levels" && <li className="chip-warn">{t("badge.no_levels")}</li>}
            {has && <li className="chip-brand">{t("badge.levels")}</li>}
            {row.smaller_size_needed && <li className="chip-warn">{t("badge.smaller")}</li>}
            {row.stop_fit === "too_tight" && <li className="chip-neutral">{t("badge.tight")}</li>}
            {row.stop_fit === "too_wide" && <li className="chip-neutral">{t("badge.wide")}</li>}
          </ul>
        </div>
        {has ? (
          <dl className="grid grid-cols-3 gap-2 border-t border-line pt-2 text-sm">
            <div>
              <dt className="text-caption text-muted">{t("colStop")}</dt>
              <dd className="tabular-nums" dir="ltr">{formatMoney(row.stop_price as number, row.currency, locale)}</dd>
              <dd>{row.stop_distance_pct != null && <PnlText pct={row.stop_distance_pct} locale={locale} />}</dd>
            </div>
            <div>
              <dt className="text-caption text-muted">{t("colTp")}</dt>
              <dd className="tabular-nums" dir="ltr">{(row.take_profit_prices ?? []).length ? (row.take_profit_prices ?? []).map((p) => formatMoney(p, row.currency, locale)).join(" / ") : DASH}</dd>
            </div>
            <div>
              <dt className="text-caption text-muted">{t("colRisk")}</dt>
              <dd>{row.risk_ils != null ? <PnlText value={-Math.abs(row.risk_ils)} currency="ILS" locale={locale} /> : DASH}</dd>
              {row.risk_pct_of_portfolio != null && <dd className="text-caption text-muted" dir="ltr">{formatPct(row.risk_pct_of_portfolio, locale)}</dd>}
            </div>
          </dl>
        ) : (
          <p className="border-t border-line pt-2 text-sm text-muted">
            {row.reason_code && e.has(`reason.${row.reason_code}`) ? e(`reason.${row.reason_code}`) : t("noNumbers")}
          </p>
        )}
        <span className="flex justify-end text-muted"><ChevronIcon className="h-4 w-4" /></span>
      </Link>
    </li>
  );
}

function Body() {
  const t = useTranslations("review");
  const c = useTranslations("common");
  const { data: portfolios, error: pErr } = usePortfolios();
  const [pid, setPid] = useState<number | null>(null);
  const saved = loadPortfolioChoice();
  const fallback = typeof saved === "number" && portfolios?.some((p) => p.id === saved) ? saved : portfolios?.[0]?.id ?? null;
  const id = pid ?? fallback;
  const review = useExitReview(id);

  if (pErr) return <p role="alert">{c("errorLoad")}</p>;
  if (!portfolios) return <p role="status" className="text-muted">{c("loading")}</p>;
  return (
    <>
      <div>
        <Link href="/" className="text-sm text-brand-text hover:underline">← {c("back")}</Link>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-sm text-muted">{t("intro")}</p>
      </div>
      {id === null ? <p className="card">{t("noPortfolio")}</p> : (
        <>
          {portfolios.length > 1 && <PortfolioSwitcher portfolios={portfolios} value={id} onChange={(v) => setPid(v as number)} allowCombined={false} />}
          {review.error ? <p role="alert">{t("error")}</p> : !review.data ? <p role="status" className="text-muted">{t("loading")}</p> : review.data.rows.length === 0 ? <p className="card">{t("empty")}</p> : (
            <>
              {review.data.rows.some((r) => r.status === "needs_horizon") && (
                <p className="rounded-xl bg-warn-bg p-3 text-warn-fg" role="status">
                  <span className="font-semibold">{t("needsHorizonTitle", { n: review.data.rows.filter((r) => r.status === "needs_horizon").length })}</span>{" "}
                  {t("needsHorizonHint")}
                </p>
              )}
              <section aria-label={t("rowsTitle")} className="space-y-2">
                <h2 className="text-heading">{t("rowsTitle")}</h2>
                <ul className="grid gap-3 md:grid-cols-2">{sortRows(review.data.rows).map((r) => <Row key={r.holding_id} row={r} />)}</ul>
              </section>
              <Totals r={review.data} />
              <p className="text-xs text-muted">{review.data.disclaimer}</p>
            </>
          )}
        </>
      )}
    </>
  );
}

export function ReviewPage() {
  return <AppShell><Body /></AppShell>;
}
