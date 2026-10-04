"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import useSWR from "swr";
import {
  api, ApiError, toPresets, type AssetType, type BuyCandidate, type BuyIdeasIn, type BuyIdeasOut, type Horizon, type MarketKey, type SkippedItem,
} from "@/lib/api";
import { formatDate, formatMoney, formatNumber, formatPct, formatTime } from "@/lib/format";
import { loadPortfolioChoice, usePortfolios } from "@/lib/hooks";
import { parseLocaleNumber } from "@/lib/number";
import { analyzeHref } from "@/lib/routes";
import { Link } from "@/i18n/navigation";
import { AppShell } from "./AppShell";
import { ExplanationView } from "./ExplanationView";
import { WhyToggle } from "./ExitLevelsPanel";
import { PnlText } from "./Pnl";
import { PortfolioSwitcher } from "./PortfolioSwitcher";

const HORIZONS: Horizon[] = ["1w", "1m", "3m", "6m", "1y"];
const MARKETS: MarketKey[] = ["US", "TASE", "CRYPTO"];
const TYPES: Extract<AssetType, "stock" | "etf" | "crypto">[] = ["stock", "etf", "crypto"];
const toggle = <T,>(list: T[], v: T): T[] => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

/** Every input starts empty: no default amount, currency, holding period, risk level, market or asset type. */
function SuggestForm({ busy, onSubmit, initial }: { busy: boolean; onSubmit: (b: BuyIdeasIn) => void; initial: BuyIdeasIn | null }) {
  const t = useTranslations("suggest");
  const st = useTranslations("settings");
  const { data: presetsRaw } = useSWR("presets", () => api.riskPresets());
  const presets = presetsRaw ? toPresets(presetsRaw) : [];
  const [amount, setAmount] = useState(initial ? String(initial.amount) : "");
  const [currency, setCurrency] = useState<"ILS" | "USD" | null>(initial?.currency ?? null);
  const [horizon, setHorizon] = useState<Horizon | null>(initial?.horizon ?? null);
  const [risk, setRisk] = useState<string>(initial?.risk ?? "");
  const [markets, setMarkets] = useState<MarketKey[]>(initial?.markets ?? []);
  const [types, setTypes] = useState<string[]>(initial?.asset_types ?? []);
  const n = parseLocaleNumber(amount);
  const amountBad = amount.trim() !== "" && !(n !== null && n > 0);
  const valid = n !== null && n > 0 && currency !== null && horizon !== null && risk !== "" && markets.length > 0 && types.length > 0;
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid || n === null || currency === null || horizon === null) return;
    onSubmit({ amount: n, currency, horizon, risk: risk as BuyIdeasIn["risk"], markets, asset_types: types as BuyIdeasIn["asset_types"] });
  };
  return (
    <form onSubmit={submit} className="card space-y-4" aria-label={t("formTitle")}>
      <h2 className="text-heading">{t("formTitle")}</h2>
      <div>
        <label htmlFor="sg-amount" className="label">{t("amount")}</label>
        <input id="sg-amount" className="input" inputMode="decimal" dir="ltr" value={amount} onChange={(e) => setAmount(e.target.value)} aria-invalid={amountBad} />
        {amountBad && <p role="alert" className="text-sm text-loss">{t("invalidAmount")}</p>}
      </div>
      <div>
        <p className="label" id="sg-cur">{t("currency")}</p>
        <div role="radiogroup" aria-labelledby="sg-cur" className="flex gap-2">
          {(["ILS", "USD"] as const).map((c) => <button key={c} type="button" role="radio" aria-checked={currency === c} className={currency === c ? "btn-primary" : "btn-secondary"} onClick={() => setCurrency(c)}>{c === "ILS" ? "₪ ILS" : "$ USD"}</button>)}
        </div>
      </div>
      <div>
        <p className="label" id="sg-h">{t("horizon")}</p>
        <div role="radiogroup" aria-labelledby="sg-h" className="flex flex-wrap gap-2">
          {HORIZONS.map((h) => <button key={h} type="button" role="radio" aria-checked={horizon === h} className={horizon === h ? "btn-primary" : "btn-secondary"} onClick={() => setHorizon(h)}>{t(`horizons.${h}`)}</button>)}
        </div>
      </div>
      <div>
        <label htmlFor="sg-risk" className="label">{t("risk")}</label>
        <select id="sg-risk" className="input" value={risk} onChange={(e) => setRisk(e.target.value)}>
          <option value="">{t("riskPlaceholder")}</option>
          {presets.map((p) => <option key={p.name} value={p.name}>{st.has(`presets.${p.name}`) ? st(`presets.${p.name}`) : p.name}</option>)}
        </select>
      </div>
      <fieldset>
        <legend className="label">{t("markets")}</legend>
        <div className="flex flex-wrap gap-4">
          {MARKETS.map((m) => <label key={m} className="flex min-h-11 items-center gap-2"><input type="checkbox" className="h-5 w-5" checked={markets.includes(m)} onChange={() => setMarkets(toggle(markets, m))} />{t(`marketNames.${m}`)}</label>)}
        </div>
      </fieldset>
      <fieldset>
        <legend className="label">{t("assetTypes")}</legend>
        <div className="flex flex-wrap gap-4">
          {TYPES.map((m) => <label key={m} className="flex min-h-11 items-center gap-2"><input type="checkbox" className="h-5 w-5" checked={types.includes(m)} onChange={() => setTypes(toggle(types, m))} />{t(`typeNames.${m}`)}</label>)}
        </div>
      </fieldset>
      {!valid && <p className="text-sm text-muted" role="status">{t("fillAll")}</p>}
      <button type="submit" className="btn-primary" disabled={!valid || busy}>{t("submit")}</button>
    </form>
  );
}

function GateNotice({ r }: { r: BuyIdeasOut }) {
  const t = useTranslations("suggest");
  if (r.launch_gate_open) return null;
  return (
    <section className="card space-y-2 border-warn-fg" aria-label={t("gateTitle")} data-testid="gate-notice">
      <h2 className="text-heading">{t("gateTitle")}</h2>
      <p className="text-sm">{t("gateBody")}</p>
      {r.launch_gate_reasons.length > 0 && (
        <div className="text-sm text-muted">
          <p>{t("gateReasons")}</p>
          <ul className="list-disc ps-5">{r.launch_gate_reasons.map((x) => <li key={x}>{x}</li>)}</ul>
        </div>
      )}
    </section>
  );
}

function Candidate({ c }: { c: BuyCandidate }) {
  const t = useTranslations("suggest");
  const st = useTranslations("settings");
  const locale = useLocale();
  const name = locale === "he" ? c.name_he : c.name_en;
  const money = (v: number) => formatMoney(v, c.currency, locale);
  const rules = (c.size.limited_by ?? []).map((r) => (st.has(`fields.${r}`) ? st(`fields.${r}`) : r)).join(", ");
  return (
    <li className="card space-y-3" data-testid="candidate">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="break-words font-semibold"><span className="text-muted">{t("rank", { n: c.rank })}</span> <bdi dir="auto">{name}</bdi></p>
          <p className="text-caption text-muted"><span dir="ltr">{c.symbol}</span> · {c.sector} · {c.country}</p>
        </div>
        <Link href={analyzeHref(c.symbol)} className="btn-secondary shrink-0" aria-label={t("analyzeLabel", { symbol: c.symbol })}>{t("analyze")}</Link>
      </div>
      {/* Neutral scale: while the launch gate is closed the score is never green or red. */}
      <dl className="grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt className="text-caption text-muted">{t("score")}</dt>
          <dd className="font-medium tabular-nums" dir="ltr">{formatNumber(c.score, locale, 0)}</dd>
          <div className="mt-1 h-1.5 rounded-full bg-surface-2" role="meter" aria-label={t("score")} aria-valuemin={-100} aria-valuemax={100} aria-valuenow={c.score}>
            <div className="h-full rounded-full bg-muted" style={{ width: `${Math.min(100, Math.max(0, (c.score + 100) / 2))}%` }} />
          </div>
        </div>
        <div>
          <dt className="text-caption text-muted">{t("confidence")}</dt>
          <dd className="font-medium tabular-nums" dir="ltr">{formatNumber(c.confidence * 100, locale, 0)}%</dd>
          <dd className="text-caption text-muted">{t("staleScore", { time: `${formatDate(c.score_as_of)} ${formatTime(c.score_as_of, locale)}` })}</dd>
        </div>
      </dl>
      <dl className="grid grid-cols-2 gap-3 border-t border-line pt-3 text-sm sm:grid-cols-4">
        <div><dt className="text-caption text-muted">{t("entry")}</dt><dd className="tabular-nums" dir="ltr">{money(c.entry)}</dd></div>
        <div><dt className="text-caption text-muted">{t("stop")}</dt><dd className="tabular-nums" dir="ltr">{money(c.stop.price)}</dd><dd><PnlText pct={c.stop.distance_pct} locale={locale} /></dd></div>
        <div className="col-span-2">
          <dt className="text-caption text-muted">{t("takeProfits")}</dt>
          <dd className="flex flex-wrap gap-x-4">{c.take_profits.map((tp) => <span key={tp.label} className="tabular-nums" dir="ltr">{money(tp.price)} <PnlText pct={tp.distance_pct} locale={locale} /></span>)}</dd>
        </div>
      </dl>
      <div className="space-y-1 border-t border-line pt-3 text-sm">
        <p className="text-caption text-muted">{t("size")}</p>
        <p>{t("sizeLine", { qty: formatNumber(c.size.quantity, locale, 4), cost: money(c.size.cost_native), pct: formatPct(c.size.pct_of_amount, locale) })}</p>
        <p className="text-muted">{t("riskLine", { risk: formatMoney(-Math.abs(c.size.risk_ils), "ILS", locale, { signed: true }), pct: formatPct(c.size.risk_pct_of_portfolio, locale) })} · {t("rr")}: <span dir="ltr">{formatNumber(c.best_rr, locale, 1)}</span> · {t("volatility", { pct: formatPct(c.annualised_volatility_pct, locale) })}</p>
        {rules && <p className="chip-warn w-fit">{t("limitedBy", { rules })}</p>}
      </div>
      <WhyToggle id={`why-${c.symbol}`}><ExplanationView e={c.explanation} reasons={c.reasons} currency={c.currency} /></WhyToggle>
    </li>
  );
}

function Skipped({ items }: { items: SkippedItem[] }) {
  const t = useTranslations("suggest");
  return (
    <details className="card" data-testid="skipped">
      <summary className="min-h-11 cursor-pointer text-heading">{t("skippedTitle", { n: items.length })}</summary>
      <div className="mt-2 space-y-2">
        <p className="text-sm text-muted">{items.length === 0 ? t("skippedNone") : t("skippedNote")}</p>
        <ul className="divide-y divide-line">
          {items.map((s) => (
            <li key={s.symbol} className="space-y-0.5 py-2 text-sm">
              <p className="font-medium"><span dir="ltr">{s.symbol}</span> · <bdi dir="auto" className="text-muted">{s.name_en}</bdi></p>
              <p>{t.has(`skip.${s.code}`) ? t(`skip.${s.code}`) : s.reason}</p>
            </li>
          ))}
        </ul>
      </div>
    </details>
  );
}

function Results({ r, onEdit }: { r: BuyIdeasOut; onEdit: () => void }) {
  const t = useTranslations("suggest");
  const locale = useLocale();
  return (
    <>
      <GateNotice r={r} />
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-heading">{t("resultsTitle", { n: r.candidates.length })}</h2>
          <p className="text-caption text-muted">{t("resultsMeta", { universe: r.universe_size, time: `${formatDate(r.generated_at)} ${formatTime(r.generated_at, locale)}`, amount: formatMoney(r.amount, r.currency, locale) })}</p>
        </div>
        <button type="button" className="btn-secondary" onClick={onEdit}>{t("edit")}</button>
      </div>
      {r.candidates.length === 0 ? <p className="card" role="status">{t("empty")}</p> : <ul className="grid gap-3 md:grid-cols-2">{r.candidates.map((c) => <Candidate key={c.symbol} c={c} />)}</ul>}
      <Skipped items={r.skipped} />
      <p className="text-xs text-muted">{r.notice} {t("disclaimer")}</p>
    </>
  );
}

function Body() {
  const t = useTranslations("suggest");
  const c = useTranslations("common");
  const { data: portfolios, error: pErr } = usePortfolios();
  const [pid, setPid] = useState<number | null>(null);
  const saved = loadPortfolioChoice();
  const fallback = typeof saved === "number" && portfolios?.some((p) => p.id === saved) ? saved : portfolios?.[0]?.id ?? null;
  const id = pid ?? fallback;
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [result, setResult] = useState<BuyIdeasOut | null>(null);
  const [last, setLast] = useState<BuyIdeasIn | null>(null);

  const run = async (b: BuyIdeasIn) => {
    if (id === null) return;
    setBusy(true); setFailed(false); setLast(b);
    try { setResult(await api.buyIdeas(id, b)); } catch (e) { if (!(e instanceof ApiError)) throw e; setFailed(true); } finally { setBusy(false); }
  };

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
          {portfolios.length > 1 && <PortfolioSwitcher portfolios={portfolios} value={id} onChange={(v) => { setPid(v as number); setResult(null); }} allowCombined={false} />}
          {busy ? <p role="status" className="text-muted">{t("loading")}</p> : result ? <Results r={result} onEdit={() => setResult(null)} /> : <SuggestForm busy={busy} onSubmit={(b) => void run(b)} initial={last} />}
          {failed && !busy && <p role="alert" className="text-loss">{t("error")}</p>}
        </>
      )}
    </>
  );
}

export function SuggestPage() {
  return <AppShell><Body /></AppShell>;
}
