"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import type { EvidenceRow, GapBreakdown, HoldingResult, Postmortem, PostmortemFinding } from "@/lib/api";
import { DASH, formatDate, formatMoney, formatPct, intlLocale } from "@/lib/format";
import { loadPortfolioChoice, usePortfolios, usePostmortem } from "@/lib/hooks";
import { Link } from "@/i18n/navigation";
import { AppShell } from "./AppShell";
import { ExpectationForm } from "./ExpectationForm";
import { ExplanationView } from "./ExplanationView";
import { PnlText } from "./Pnl";
import { PortfolioSwitcher } from "./PortfolioSwitcher";

const fin = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);
const Txt = ({ children }: { children: string }) => <bdi dir="auto">{children}</bdi>;
const ISO = /^\d{4}-\d{2}-\d{2}$/;

/** Percentage points with an explicit sign and arrow, like PnlText (never colour alone). */
function PpText({ value, className = "" }: { value: number | null | undefined; className?: string }) {
  const t = useTranslations("postmortem");
  const locale = useLocale();
  if (!fin(value)) return <span className={`text-muted ${className}`}>{DASH}</span>;
  const num = new Intl.NumberFormat(intlLocale(locale), { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "exceptZero" }).format(value);
  const tone = value > 0 ? "text-gain" : value < 0 ? "text-loss" : "text-muted";
  return (
    <span className={`${tone} tabular-nums ${className}`} dir="ltr">
      {value !== 0 && <span aria-hidden="true" className="me-1 text-[0.7em]">{value > 0 ? "▲" : "▼"}</span>}
      {t("ppUnit", { value: num })}
    </span>
  );
}

function Evidence({ row }: { row: EvidenceRow }) {
  const locale = useLocale();
  const v = row.value;
  const unit = row.unit ?? "";
  let body: React.ReactNode = DASH;
  if (typeof v === "string") body = <Txt>{v}</Txt>;
  else if (fin(v)) {
    if (unit === "ILS" || unit === "USD") body = <PnlText value={v} currency={unit} locale={locale} />;
    else if (unit === "%") body = <PnlText pct={v} locale={locale} />;
    else if (unit === "pp") body = <PpText value={v} />;
    else body = <span dir="ltr" className="tabular-nums">{new Intl.NumberFormat(intlLocale(locale), { maximumFractionDigits: 2 }).format(v)}{unit ? ` ${unit}` : ""}</span>;
  }
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <dt className="text-muted"><Txt>{row.label}</Txt></dt>
      <dd>{body}{row.note && <span className="text-caption text-muted"> · <Txt>{row.note}</Txt></span>}</dd>
    </div>
  );
}

function FindingCard({ f }: { f: PostmortemFinding }) {
  const t = useTranslations("postmortem");
  const locale = useLocale();
  const none = f.status === "not_recorded";
  const showNumbers = f.status === "ok";
  return (
    <li className="card space-y-2" data-testid={`finding-${f.kind}`} data-status={f.status}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">{t(`kind.${f.kind}`)}</h3>
        {none && <span className="chip-neutral">{t("notRecorded")}</span>}
        {!none && f.status !== "ok" && <span className="chip-warn">{t(`status.${f.status}`)}</span>}
      </div>
      {none ? <p className="text-sm text-muted">{t("notRecordedHint")}</p> : (
        <>
          <p className="text-sm" dir="auto"><Txt>{f.headline}</Txt></p>
          {showNumbers && (fin(f.amount_ils) || fin(f.gap_contribution_pp)) && (
            <p className="flex flex-wrap gap-x-4 text-sm font-semibold">
              {fin(f.amount_ils) && <PnlText value={f.amount_ils} pct={f.amount_pct} currency="ILS" locale={locale} />}
              {fin(f.gap_contribution_pp) && <span><span className="font-normal text-muted">{t("gapShare")}: </span><PpText value={f.gap_contribution_pp} /></span>}
            </p>
          )}
          {showNumbers && (f.evidence ?? []).length > 0 && (
            <dl className="space-y-1 border-t border-line pt-2 text-sm" aria-label={t("evidence")}>
              {(f.evidence ?? []).map((r, i) => <Evidence key={`${r.label}-${i}`} row={r} />)}
            </dl>
          )}
        </>
      )}
      <details className="border-t border-line pt-2">
        <summary className="cursor-pointer text-sm font-medium text-brand-text">{t("why")}</summary>
        <div className="mt-2"><ExplanationView e={f.explanation} /></div>
      </details>
    </li>
  );
}

/** Ranked by size of the effect (biggest first), and the residual shown as it is. */
export const rankItems = (g: GapBreakdown) => [...(g.items ?? [])].sort((a, b) => Math.abs(b.pp) - Math.abs(a.pp));

function Explain({ pm }: { pm: Postmortem }) {
  const t = useTranslations("postmortem");
  const locale = useLocale();
  const hasExp = pm.gap_vs_expectation?.status === "ok";
  const [ref, setRef] = useState<"expectation" | "benchmark" | null>(null);
  const which = ref ?? (hasExp ? "expectation" : "benchmark");
  const g = which === "expectation" ? pm.gap_vs_expectation : pm.gap_vs_benchmark;
  return (
    <section className="card space-y-3" aria-label={t("explainTitle")}>
      <h2 className="text-heading">{t("explainTitle")}</h2>
      <div role="group" aria-label={t("compareWith")} className="flex flex-wrap gap-2">
        {(["expectation", "benchmark"] as const).map((k) => (
          <button key={k} type="button" aria-pressed={which === k} onClick={() => setRef(k)} className={which === k ? "btn-primary" : "btn-secondary"}>
            {k === "expectation" ? t("refExpectation") : t("refBenchmark")}
          </button>
        ))}
      </div>
      {!g || g.status === "needs_expectation" ? <p className="text-sm text-muted" role="status">{t("needsExpectationRef")}</p>
        : g.status === "not_enough_data" ? <p className="text-sm text-muted" role="status">{t("gapNotEnough")}</p> : (
          <>
            {rankItems(g).length === 0 ? <p className="text-sm text-muted">{t("itemsNone")}</p> : (
              <ol className="space-y-2" aria-label={t("explainTitle")}>
                {rankItems(g).map((it) => (
                  <li key={it.key} className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line pb-1 text-sm" data-testid="gap-item">
                    <span><Txt>{it.label}</Txt>{it.symbol && <span className="text-caption text-muted" dir="ltr"> · {it.symbol}</span>}</span>
                    <span className="flex gap-3"><PnlText value={it.amount_ils} currency="ILS" locale={locale} /><PpText value={it.pp} /></span>
                  </li>
                ))}
              </ol>
            )}
            <p className="rounded-xl bg-surface-2 p-3 text-sm" data-testid="reconciliation">
              {t("reconcile", {
                attributed: fin(g.attributed_pp) ? signed(g.attributed_pp, locale, t) : DASH,
                residual: fin(g.residual_pp) ? signed(g.residual_pp, locale, t) : DASH,
                ils: fin(g.residual_ils) ? formatMoney(g.residual_ils, "ILS", locale, { signed: true }) : DASH,
                gap: fin(g.gap_pp) ? signed(g.gap_pp, locale, t) : DASH,
              })}
              <span className="mt-1 block text-muted" data-testid="reconcile-flag">{g.reconciles === false ? t("notReconcile") : g.reconciles ? t("reconciles") : ""}</span>
              {g.note && <span className="mt-1 block text-caption text-muted"><Txt>{g.note}</Txt></span>}
            </p>
          </>
        )}
    </section>
  );
}

function signed(v: number, locale: string, t: (k: "ppUnit", v: { value: string }) => string): string {
  return t("ppUnit", { value: new Intl.NumberFormat(intlLocale(locale), { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "exceptZero" }).format(v) });
}

function Headline({ pm }: { pm: Postmortem }) {
  const t = useTranslations("postmortem");
  const locale = useLocale();
  const ex = pm.expectation;
  const bench = pm.gap_vs_benchmark;
  const gapTile = (label: string, g: number | null | undefined) => (
    <p className="text-sm"><span className="text-muted">{label}: </span><span className="font-semibold"><PpText value={g} /></span></p>
  );
  return (
    <section className="card space-y-3" aria-label={t("headlineTitle")}>
      <h2 className="text-heading">{t("headlineTitle")}</h2>
      <div className="grid gap-3 sm:grid-cols-3">
        <div data-testid="tile-return">
          <p className="text-caption text-muted">{t("yourReturn")}</p>
          <p className="text-2xl font-bold"><PnlText pct={pm.twr_pct} locale={locale} /></p>
          {fin(pm.pnl_ils) && <p className="text-sm"><PnlText value={pm.pnl_ils} currency="ILS" locale={locale} /></p>}
        </div>
        <div data-testid="tile-expectation">
          <p className="text-caption text-muted">{t("yourExpectation")}</p>
          {ex.status === "ok" && fin(ex.expected_for_period_pct) ? (
            <>
              <p className="text-2xl font-bold tabular-nums" dir="ltr">{formatPct(ex.expected_for_period_pct, locale)}</p>
              <p className="text-caption text-muted">{t("expectedPeriod")} · {t("expectedFor", { pct: formatPct(ex.expected_return_pct ?? 0, locale), months: ex.horizon_months ?? 0 })}</p>
            </>
          ) : <p className="text-lg text-muted">{t("notSet")}</p>}
        </div>
        <div data-testid="tile-benchmark">
          <p className="text-caption text-muted">{t("benchmark")}</p>
          <p className="text-2xl font-bold tabular-nums" dir="ltr">{fin(bench?.reference_pct) ? formatPct(bench.reference_pct, locale) : DASH}</p>
          <p className="text-caption text-muted">{t("benchmarkNote")}</p>
        </div>
      </div>
      <div className="space-y-1 border-t border-line pt-2">
        {gapTile(t("gapVsExpectation"), pm.gap_vs_expectation?.status === "ok" ? pm.gap_vs_expectation.gap_pp : null)}
        {gapTile(t("gapVsBenchmark"), bench?.status === "ok" ? bench.gap_pp : null)}
      </div>
      {pm.summary && <p className="text-sm text-muted" dir="auto"><Txt>{pm.summary}</Txt></p>}
    </section>
  );
}

function HoldingRow({ h }: { h: HoldingResult }) {
  const t = useTranslations("postmortem");
  const locale = useLocale();
  const cell = (label: string, v: number) => (
    <div><dt className="text-caption text-muted">{label}</dt><dd><PnlText value={v} currency="ILS" locale={locale} /></dd></div>
  );
  return (
    <li className="card space-y-2" data-testid="pm-holding">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0"><p className="break-words font-semibold"><Txt>{h.name}</Txt></p><p className="text-caption text-muted" dir="ltr">{h.symbol}</p></div>
        <div className="text-end"><p className="font-bold"><PnlText value={h.total_ils} currency="ILS" locale={locale} /></p><p className="text-caption text-muted" dir="ltr">{t("ofCapital", { pct: formatPct(h.pct_of_capital, locale, { signed: true }) })}</p></div>
      </div>
      <dl className="grid grid-cols-2 gap-2 border-t border-line pt-2 text-sm sm:grid-cols-4">
        {cell(t("realized"), h.realized_ils)}
        {cell(t("unrealized"), h.unrealized_ils)}
        {cell(t("localEffect"), h.local_ils)}
        {cell(t("fxEffect"), h.fx_ils)}
      </dl>
      <span className={h.still_held ? "chip-neutral" : "chip-warn"}>{h.still_held ? t("stillHeld") : t("sold")}</span>
    </li>
  );
}

function Holdings({ pm }: { pm: Postmortem }) {
  const t = useTranslations("postmortem");
  const list = (title: string, s: string[]) => (
    <div>
      <h3 className="font-semibold">{title}</h3>
      {s.length === 0 ? <p className="text-sm text-muted">{t("none")}</p> : <ul className="flex flex-wrap gap-1.5" aria-label={title}>{s.map((x) => <li key={x} className="chip-neutral" dir="ltr">{x}</li>)}</ul>}
    </div>
  );
  if ((pm.holdings ?? []).length === 0) return null;
  return (
    <section className="space-y-3" aria-label={t("holdingsTitle")}>
      <h2 className="text-heading">{t("holdingsTitle")}</h2>
      <div className="grid gap-3 sm:grid-cols-2">{list(t("contributors"), pm.top_contributors ?? [])}{list(t("detractors"), pm.top_detractors ?? [])}</div>
      <ul className="grid gap-3 md:grid-cols-2">{[...(pm.holdings ?? [])].sort((a, b) => b.total_ils - a.total_ils).map((h) => <HoldingRow key={h.symbol} h={h} />)}</ul>
    </section>
  );
}

function Period({ applied, onApply }: { applied: { start: string | null; end: string | null }; onApply: (v: { start: string | null; end: string | null }) => void }) {
  const t = useTranslations("postmortem");
  const [custom, setCustom] = useState(applied.start !== null || applied.end !== null);
  const [start, setStart] = useState(applied.start ?? "");
  const [end, setEnd] = useState(applied.end ?? "");
  const bad = custom && ((start !== "" && !ISO.test(start)) || (end !== "" && !ISO.test(end)) || (start !== "" && end !== "" && start >= end) || (start === "" && end === ""));
  return (
    <fieldset className="card space-y-3">
      <legend className="px-1 text-sm font-semibold">{t("periodTitle")}</legend>
      <div className="flex flex-wrap gap-4 text-sm">
        <label className="flex items-center gap-2"><input type="radio" name="period" checked={!custom} onChange={() => { setCustom(false); onApply({ start: null, end: null }); }} />{t("periodSince")}</label>
        <label className="flex items-center gap-2"><input type="radio" name="period" checked={custom} onChange={() => setCustom(true)} />{t("periodCustom")}</label>
      </div>
      {custom && (
        <div className="grid grid-cols-2 gap-3">
          <div><label htmlFor="pm-start" className="label">{t("from")}</label><input id="pm-start" type="date" className="input" dir="ltr" value={start} onChange={(e) => setStart(e.target.value)} /></div>
          <div><label htmlFor="pm-end" className="label">{t("to")}</label><input id="pm-end" type="date" className="input" dir="ltr" value={end} onChange={(e) => setEnd(e.target.value)} /></div>
          {bad && start !== "" && end !== "" && <p className="col-span-2 text-sm text-warn-fg" role="status">{t("badRange")}</p>}
          <div className="col-span-2"><button type="button" className="btn-secondary" disabled={bad} onClick={() => onApply({ start: start || null, end: end || null })}>{t("apply")}</button></div>
        </div>
      )}
    </fieldset>
  );
}

export function PostmortemBody({ initialId = null, initialStart = null, initialEnd = null }: { initialId?: number | null; initialStart?: string | null; initialEnd?: string | null }) {
  const t = useTranslations("postmortem");
  const c = useTranslations("common");
  const { data: portfolios, error: pErr, mutate: mutatePortfolios } = usePortfolios();
  const [pid, setPid] = useState<number | null>(initialId);
  const [period, setPeriod] = useState({ start: initialStart && ISO.test(initialStart) ? initialStart : null, end: initialEnd && ISO.test(initialEnd) ? initialEnd : null });
  const saved = loadPortfolioChoice();
  const fallback = typeof saved === "number" && portfolios?.some((p) => p.id === saved) ? saved : portfolios?.[0]?.id ?? null;
  const id = pid ?? fallback;
  const pm = usePostmortem(id, period.start, period.end);
  const portfolio = portfolios?.find((p) => p.id === id);

  if (pErr) return <p role="alert">{c("errorLoad")}</p>;
  if (!portfolios) return <p role="status" className="text-muted">{c("loading")}</p>;
  const data = pm.data;
  const needsExp = data?.expectation.status === "needs_expectation";
  const refresh = () => { void mutatePortfolios(); void pm.mutate(); };
  return (
    <>
      <div>
        <Link href="/" className="text-sm text-brand-text hover:underline">← {c("back")}</Link>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-sm text-muted">{t("intro")}</p>
      </div>
      {id === null || !portfolio ? <p className="card">{t("noPortfolio")}</p> : (
        <>
          {portfolios.length > 1 && <PortfolioSwitcher portfolios={portfolios} value={id} onChange={(v) => setPid(v as number)} allowCombined={false} />}
          <Period key={`${period.start}-${period.end}`} applied={period} onApply={setPeriod} />
          {pm.error ? <p role="alert">{t("error")}</p> : !data ? <p role="status" className="text-muted">{t("loading")}</p> : (
            <>
              <p className="text-caption text-muted" data-testid="period-shown">{t("periodShown", { start: formatDate(data.start), end: formatDate(data.end), days: data.days })}</p>
              {data.status === "not_enough_history" && (
                <div className="card space-y-1" role="status" data-testid="not-enough-history">
                  <h2 className="text-heading">{t("notEnoughHistoryTitle")}</h2>
                  <p className="text-sm">{t("notEnoughHistory", { remaining: data.history?.days_remaining ?? 0, have: data.history?.days_available ?? 0, need: data.history?.days_required ?? 0 })}</p>
                </div>
              )}
              {data.status === "not_enough_data" && <p className="card" role="status">{t("notEnoughData")}</p>}
              {data.status === "ok" && (
                <>
                  <Headline pm={data} />
                  <Explain pm={data} />
                  <Holdings pm={data} />
                  <section className="space-y-3" aria-label={t("findingsTitle")}>
                    <h2 className="text-heading">{t("findingsTitle")}</h2>
                    <ul className="grid gap-3 md:grid-cols-2">{(data.findings ?? []).map((f) => <FindingCard key={f.kind} f={f} />)}</ul>
                  </section>
                  {(data.excluded ?? []).length > 0 && (
                    <section className="card space-y-1" aria-label={t("excludedTitle")}>
                      <h3 className="font-semibold">{t("excludedTitle")}</h3>
                      <ul className="text-sm">{(data.excluded ?? []).map((x) => <li key={x.symbol}><span dir="ltr" className="font-medium">{x.symbol}</span> · <Txt>{x.reason}</Txt></li>)}</ul>
                    </section>
                  )}
                </>
              )}
              {needsExp ? (
                <section className="card space-y-2" aria-label={t("expTitle")} data-testid="exp-needed">
                  <h2 className="text-heading">{t("expTitle")}</h2>
                  <ExpectationForm key="unset" portfolio={portfolio} onSaved={refresh} />
                </section>
              ) : (
                <details className="card">
                  <summary className="cursor-pointer font-semibold">{t("expEdit")}</summary>
                  <div className="mt-3"><ExpectationForm key={`${portfolio.expected_return_pct}-${portfolio.expected_return_horizon_months}`} portfolio={portfolio} onSaved={refresh} /></div>
                </details>
              )}
              <p className="text-xs text-muted">{data.disclaimer}</p>
            </>
          )}
        </>
      )}
    </>
  );
}

export function PostmortemPage(props: { initialId?: number | null; initialStart?: string | null; initialEnd?: string | null }) {
  return <AppShell><PostmortemBody {...props} /></AppShell>;
}
