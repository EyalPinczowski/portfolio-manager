"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { mutate } from "swr";
import { api, ApiError, FUND_SYMBOL_PREFIX, type FundDetail, type FundReturn, type FundSearchItem, type Portfolio } from "@/lib/api";
import { DASH, formatDate, formatNumber, formatPct, intlLocale } from "@/lib/format";
import { useFund, useFundSearch } from "@/lib/hooks";
import { parseLocaleNumber } from "@/lib/number";
import { MAX_QTY } from "@/lib/import-rows";
import { PnlText } from "./Pnl";

const fin = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);
const Txt = ({ children }: { children: string }) => <bdi dir="auto">{children}</bdi>;
const todayIso = (): string => new Date().toISOString().slice(0, 10);

/** Percentage points with an explicit sign (neutral colour: a gap to the category average is not a verdict). */
function Pts({ value }: { value: number }) {
  const t = useTranslations("funds");
  const locale = useLocale();
  const num = new Intl.NumberFormat(intlLocale(locale), { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "exceptZero" }).format(value);
  return <span className="tabular-nums" dir="ltr">{t("ptsUnit", { value: num })}</span>;
}

/** Credit line under every fund screen: source, data date and the personal non-commercial terms. */
export function FundCredit({ source, credit, asOf }: { source: string; credit: string; asOf?: string | null }) {
  const t = useTranslations("funds");
  return (
    <p className="text-caption text-muted" data-testid="fund-credit">
      {t("source", { source })}{asOf ? ` · ${t("asOf", { date: formatDate(asOf) })}` : ""} · <Txt>{credit}</Txt> · {t("personalUse")}
    </p>
  );
}

/** Returns per horizon. Each missing value says why (never 0). The category average is only there when the server sent one. */
export function FundReturns({ d }: { d: FundDetail }) {
  const t = useTranslations("funds");
  const locale = useLocale();
  const hasCat = d.returns.some((r) => fin(r.category_avg_pct));
  const cell = (r: FundReturn) => {
    if (fin(r.return_pct)) return <PnlText pct={r.return_pct} locale={locale} />;
    return <span className="text-muted" data-testid={`missing-${r.horizon}`}>{DASH} <span className="text-caption">{t(`missing.${r.missing_reason ?? "no_value"}`)}</span></span>;
  };
  return (
    <div className="relative overflow-x-auto">
      <table className="w-full text-sm" data-testid="fund-returns">
        <thead>
          <tr className="border-b border-line text-start text-caption text-muted">
            <th className="p-2 text-start font-medium">{t("period")}</th>
            <th className="p-2 text-start font-medium">{t("return")}</th>
            {hasCat && <th className="p-2 text-start font-medium">{t("categoryAvg")}</th>}
          </tr>
        </thead>
        <tbody>
          {d.returns.map((r) => (
            <tr key={r.horizon} className="border-b border-line last:border-0" data-testid={`return-${r.horizon}`}>
              <td className="p-2 font-medium">{t(`horizon.${r.horizon}`)}</td>
              <td className="p-2">
                {cell(r)}
                {fin(r.annualised_pct) && r.horizon === "3y" && (
                  <span className="block text-caption text-muted" dir="ltr">{t("annualised", { value: formatPct(r.annualised_pct, locale, { signed: true }) })}</span>
                )}
              </td>
              {hasCat && (
                <td className="p-2 tabular-nums" dir="ltr" data-testid={`cat-${r.horizon}`}>
                  {fin(r.category_avg_pct) ? (
                    <>{formatPct(r.category_avg_pct, locale)}{fin(r.vs_category_pts) && <span className="block text-caption text-muted"><Pts value={r.vs_category_pts} /></span>}</>
                  ) : DASH}
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {!hasCat && <p className="mt-1 text-caption text-muted" data-testid="no-category">{t("noCategory", { count: d.category_peer_count })}</p>}
    </div>
  );
}

function FundDetailView({ d }: { d: FundDetail }) {
  const t = useTranslations("funds");
  const locale = useLocale();
  return (
    <div className="space-y-3" data-testid="fund-detail">
      <div>
        <h3 className="font-semibold"><Txt>{d.name}</Txt></h3>
        <p className="text-caption text-muted">
          {d.classification && <Txt>{d.classification}</Txt>}{d.classification && d.managing_corporation && " · "}{d.managing_corporation && <Txt>{d.managing_corporation}</Txt>}
        </p>
        <p className="text-caption text-muted" dir="ltr">{d.symbol}</p>
      </div>
      {d.data_stale && <p className="chip-warn" role="status" data-testid="fund-stale">{t("stale", { period: d.latest_period })}</p>}
      <FundReturns d={d} />
      <ul className="space-y-0.5 text-sm text-muted">
        {fin(d.management_fee_pct) && <li>{t("fee", { value: formatNumber(d.management_fee_pct, locale) })}</li>}
        <li>{t("latestPeriod", { period: d.latest_period })}</li>
      </ul>
      <p className="rounded-xl bg-surface-2 p-3 text-sm" data-testid="fund-no-price">{t("noPrice")}</p>
      <FundCredit source={d.source} credit={d.credit} asOf={d.data_as_of} />
    </div>
  );
}

type Problem = "value" | "date" | "duplicate" | "rate" | "generic" | null;

/**
 * Add a fund: search (GemelNet) -> returns -> manual value. The dataset has returns, not a unit price, so the value
 * is what the user enters from the fund statement: no default value, and the date it refers to is required.
 */
export function FundAddFlow({ portfolios, pid, onPid, onClose, onAdded }: {
  portfolios: Portfolio[]; pid: number; onPid: (id: number) => void; onClose: () => void; onAdded?: () => void;
}) {
  const t = useTranslations("funds");
  const c = useTranslations("common");
  const a = useTranslations("addHolding");
  const [draft, setDraft] = useState("");
  const [q, setQ] = useState("");
  const [picked, setPicked] = useState<FundSearchItem | null>(null);
  const [entering, setEntering] = useState(false);
  const [value, setValue] = useState("");
  const [asOf, setAsOf] = useState("");
  const [track, setTrack] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem>(null);
  const search = useFundSearch(q);
  const detail = useFund(picked?.fund_id ?? null);

  const submitSearch = (e: React.FormEvent) => { e.preventDefault(); setPicked(null); setEntering(false); setQ(draft.trim()); };
  const pick = (f: FundSearchItem) => { setPicked(f); setEntering(false); setTrack(f.classification ?? ""); setProblem(null); };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!picked) return;
    const v = parseLocaleNumber(value);
    if (v === null || !(v > 0) || v > MAX_QTY) return setProblem("value");
    if (!/^\d{4}-\d{2}-\d{2}$/.test(asOf) || asOf > todayIso()) return setProblem("date");
    setBusy(true); setProblem(null);
    try {
      await api.addHolding(pid, {
        symbol: `${FUND_SYMBOL_PREFIX}${picked.fund_id}`, quantity: 1, manual_value_ils: v, manual_value_as_of: asOf,
        fund_name: picked.name.slice(0, 120), track: track.trim() === "" ? null : track.trim().slice(0, 120),
      });
      await mutate(() => true);
      onAdded?.();
      onClose();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setProblem("duplicate");
      else if (err instanceof ApiError && err.status === 422) setProblem(err.validation.some((d) => d.loc.includes("manual_value_as_of")) ? "date" : "value");
      else if (err instanceof ApiError && err.status === 429) setProblem("rate");
      else setProblem("generic");
    } finally { setBusy(false); }
  };

  const status = search.data?.data_status;
  return (
    <div className="space-y-3" data-testid="fund-flow">
      <p className="text-sm text-muted">{t("intro")}</p>
      {portfolios.length > 1 && (
        <div>
          <label htmlFor="fa-portfolio" className="label">{a("portfolio")}</label>
          <select id="fa-portfolio" className="input" value={pid} onChange={(e) => onPid(Number(e.target.value))}>
            {portfolios.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
      )}
      <form onSubmit={submitSearch} className="space-y-2" role="search" noValidate>
        <label htmlFor="fa-q" className="label">{t("searchLabel")}</label>
        <div className="flex gap-2">
          <input id="fa-q" className="input flex-1" dir="auto" maxLength={80} value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={t("searchPlaceholder")} />
          <button type="submit" className="btn-secondary" disabled={draft.trim().length < 2}>{t("search")}</button>
        </div>
        <p className="text-caption text-muted">{t("searchHint")}</p>
      </form>

      {q && search.isLoading && <p role="status" className="text-muted">{c("loading")}</p>}
      {q && search.error && <p role="alert" className="text-sm text-loss">{t("status.generic")}</p>}
      {status && status !== "ok" && (
        <div className="rounded-xl bg-warn-bg p-3 text-sm text-warn-fg" role="status" data-testid={`search-${status}`}>
          <p>{t(`status.${status}`)}</p>
          {(status === "unavailable" || status === "rate_limited") && <p className="mt-1">{t("manualStillWorks")}</p>}
        </div>
      )}
      {status === "ok" && search.data && (
        <ul className="divide-y divide-line rounded-xl border border-line" aria-label={t("results")} data-testid="fund-results">
          {search.data.results.map((f) => (
            <li key={f.fund_id}>
              <button type="button" onClick={() => pick(f)} aria-pressed={picked?.fund_id === f.fund_id} aria-label={t("openFund", { name: f.name })} className="flex min-h-11 w-full flex-col items-start p-2 text-start hover:bg-surface-2">
                <span className="font-medium"><Txt>{f.name}</Txt></span>
                <span className="text-caption text-muted">
                  {f.classification && <Txt>{f.classification}</Txt>}{f.classification && f.managing_corporation && " · "}{f.managing_corporation && <Txt>{f.managing_corporation}</Txt>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {search.data && <FundCredit source={search.data.source} credit={search.data.credit} />}

      {picked && (
        detail.error ? <p role="alert" className="text-sm text-loss">{t("status.generic")}</p>
        : !detail.data ? <p role="status" className="text-muted">{c("loading")}</p>
        : <FundDetailView d={detail.data} />
      )}

      {picked && detail.data && !entering && (
        <button type="button" className="btn-primary" onClick={() => setEntering(true)}>{t("addThis")}</button>
      )}
      {picked && entering && (
        <form onSubmit={submit} className="space-y-3 border-t border-line pt-3" noValidate data-testid="fund-value-form">
          <p className="text-sm">{t("valueExplain")}</p>
          <div>
            <label htmlFor="fa-value" className="label">{t("value")}</label>
            <input id="fa-value" className="input" dir="ltr" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} aria-invalid={problem === "value" || undefined} />
          </div>
          <div>
            <label htmlFor="fa-asof" className="label">{t("valueAsOf")}</label>
            <input id="fa-asof" type="date" className="input" dir="ltr" max={todayIso()} value={asOf} onChange={(e) => setAsOf(e.target.value)} aria-invalid={problem === "date" || undefined} />
          </div>
          <div>
            <label htmlFor="fa-track" className="label">{t("track")} <span className="text-muted">({c("optional")})</span></label>
            <input id="fa-track" className="input" dir="auto" maxLength={120} value={track} onChange={(e) => setTrack(e.target.value)} />
          </div>
          {problem && <p role="alert" className="text-sm font-medium text-loss">{t(`error.${problem}`)}</p>}
          <div className="flex flex-wrap gap-2">
            <button type="submit" className="btn-primary" disabled={busy}>{busy ? a("adding") : t("add")}</button>
            <button type="button" className="btn-secondary" onClick={() => setEntering(false)}>{c("cancel")}</button>
          </div>
        </form>
      )}
    </div>
  );
}
