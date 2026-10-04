"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import type { Dividends, SymbolDividendStatus, UpcomingDividend } from "@/lib/api";
import { formatDate, formatMoney } from "@/lib/format";
import { loadPortfolioChoice, useDividends, usePortfolios } from "@/lib/hooks";
import { Link } from "@/i18n/navigation";
import { AppShell } from "./AppShell";
import { PortfolioSwitcher } from "./PortfolioSwitcher";

const fin = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);
const Txt = ({ children }: { children: string }) => <bdi dir="auto">{children}</bdi>;

function Upcoming({ d }: { d: Dividends }) {
  const t = useTranslations("dividends");
  const locale = useLocale();
  return (
    <section className="space-y-2" aria-label={t("upcomingTitle", { days: d.window_days })}>
      <h2 className="text-heading">{t("upcomingTitle", { days: d.window_days })}</h2>
      {d.upcoming.length === 0 ? (
        <p className="card text-sm" role="status" data-testid="upcoming-none">{t("upcomingNone", { days: d.window_days })}</p>
      ) : (
        <ul className="grid gap-3 md:grid-cols-2">
          {d.upcoming.map((u: UpcomingDividend) => (
            <li key={`${u.symbol}-${u.ex_date}`} className="card space-y-1" data-testid="upcoming-row">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="break-words font-semibold"><Txt>{u.name_en}</Txt></p>
                  <p className="text-caption text-muted" dir="ltr">{u.symbol}</p>
                </div>
                {u.amount_is_estimate && <span className="chip-warn" title={t("estimateHint")} data-testid="estimate-chip">{t("estimate")}</span>}
              </div>
              <dl className="space-y-0.5 text-sm">
                <div className="flex justify-between gap-2"><dt className="text-muted">{t("exDate")}</dt><dd className="tabular-nums" dir="ltr">{formatDate(u.ex_date)}</dd></div>
                <div className="flex justify-between gap-2">
                  <dt className="text-muted">{t("payDate")}</dt>
                  <dd className="tabular-nums" dir="ltr">{u.pay_date ? formatDate(u.pay_date) : <span className="text-muted" dir="auto">{t("payUnknown")}</span>}</dd>
                </div>
                <div className="flex justify-between gap-2"><dt className="text-muted">{t("perShareLabel")}</dt><dd className="tabular-nums" dir="ltr">{formatMoney(u.amount_per_share, u.currency, locale)}</dd></div>
                <div className="flex justify-between gap-2">
                  <dt className="text-muted">{t("forHoldingLabel")}</dt>
                  <dd className="text-end tabular-nums" dir="ltr">
                    {formatMoney(u.expected_amount, u.currency, locale)}
                    {fin(u.expected_amount_ils) && u.currency !== "ILS" && <span className="block text-caption text-muted">{t("forHoldingIls", { amount: formatMoney(u.expected_amount_ils, "ILS", locale) })}</span>}
                  </dd>
                </div>
              </dl>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Income({ d }: { d: Dividends }) {
  const t = useTranslations("dividends");
  const locale = useLocale();
  const e = d.income_estimate;
  return (
    <section className="card space-y-2" aria-label={t("incomeTitle")} data-testid="income">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-heading">{t("incomeTitle")}</h2>
        {e.is_estimate && <span className="chip-warn">{t("estimate")}</span>}
      </div>
      {fin(e.total_ils) ? (
        <p className="text-3xl font-bold tabular-nums" dir="ltr" data-testid="income-total">{formatMoney(e.total_ils, "ILS", locale)}</p>
      ) : (
        <>
          <p className="text-3xl font-bold text-muted" data-testid="income-total">{t("incomeNoData")}</p>
          <p className="text-sm">{t("incomeNoDataNote")}</p>
        </>
      )}
      {e.lines.length > 0 && (
        <ul className="space-y-0.5 text-sm text-muted" data-testid="income-lines">
          {e.lines.map((l) => (
            <li key={l.symbol} dir="auto"><span dir="ltr">{t("incomeLine", { symbol: l.symbol, amount: formatMoney(l.amount, l.currency, locale), count: l.payments_counted })}</span></li>
          ))}
        </ul>
      )}
      {e.symbols_without_data.length > 0 && <p className="text-sm text-muted" data-testid="income-without">{t("withoutData", { symbols: e.symbols_without_data.join(", ") })}</p>}
      <p className="text-caption text-muted">{t("incomeEstimateNote")}</p>
    </section>
  );
}

function Statuses({ list }: { list: SymbolDividendStatus[] }) {
  const t = useTranslations("dividends");
  const locale = useLocale();
  if (list.length === 0) return null;
  return (
    <section className="card space-y-2" aria-label={t("statusTitle")}>
      <h2 className="text-heading">{t("statusTitle")}</h2>
      <ul className="divide-y divide-line">
        {list.map((s) => (
          <li key={s.symbol} className="py-2 text-sm" data-testid={`status-${s.symbol}`}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-medium" dir="ltr">{s.symbol}</span>
              <span className={s.data_status === "ok" ? "chip-brand" : s.data_status === "not_applicable" || s.data_status === "not_checked" ? "chip-neutral" : "chip-warn"} data-status={s.data_status}>{t(`status.${s.data_status}`)}</span>
            </div>
            {fin(s.last_amount_per_share) && s.last_ex_date && (
              <p className="text-caption text-muted">{t("lastPayment", { date: formatDate(s.last_ex_date), amount: formatMoney(s.last_amount_per_share, s.currency ?? "USD", locale) })}</p>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function Body() {
  const t = useTranslations("dividends");
  const c = useTranslations("common");
  const { data: portfolios, error: pErr } = usePortfolios();
  const [pid, setPid] = useState<number | null>(() => { const s = loadPortfolioChoice(); return typeof s === "number" ? s : null; });
  const id = portfolios && portfolios.length > 0 ? (portfolios.some((p) => p.id === pid) ? (pid as number) : portfolios[0].id) : null;
  const { data, error } = useDividends(id);
  const head = (
    <div>
      <Link href="/" className="text-sm text-brand-text hover:underline">← {c("back")}</Link>
      <h1 className="text-2xl font-bold">{t("title")}</h1>
      <p className="text-sm text-muted">{t("intro")}</p>
    </div>
  );
  if (pErr) return <>{head}<p role="alert">{t("error")}</p></>;
  if (!portfolios) return <>{head}<p role="status" className="text-muted">{t("loading")}</p></>;
  if (portfolios.length === 0) return <>{head}<p className="card" role="status">{t("noPortfolio")}</p></>;
  return (
    <>
      {head}
      {portfolios.length > 1 && <PortfolioSwitcher portfolios={portfolios} value={id as number} onChange={(v) => setPid(v as number)} allowCombined={false} />}
      {error ? <p role="alert" className="text-loss">{t("error")}</p> : !data ? <p role="status" className="text-muted">{t("loading")}</p> : (
        <>
          <Upcoming d={data} />
          <Income d={data} />
          <Statuses list={data.symbols} />
          <p className="text-caption text-muted" data-testid="dividends-credit">
            {t("sourceLine", { source: data.source })} · {t("asOf", { date: formatDate(data.as_of) })} · <Txt>{data.credit}</Txt> · {t("personalUse")}
          </p>
          <p className="text-xs text-muted">{t("disclaimer")}</p>
        </>
      )}
    </>
  );
}

export function DividendsPage() {
  return <AppShell><Body /></AppShell>;
}
