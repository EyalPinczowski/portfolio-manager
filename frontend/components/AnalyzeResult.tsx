"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { api, ApiError, type AnalyzeOut, type AnalyzeQuery, type AskOut, type ExposureCheck, type Horizon, type Portfolio, type PortfolioFit, type RiskPresetName } from "@/lib/api";
import { DASH, formatMoney, formatNumber, formatPct, formatTime, formatWeight } from "@/lib/format";
import { useAnalyze, usePortfolios, useWatchlist } from "@/lib/hooks";
import { Link } from "@/i18n/navigation";
import { isFitIncompleteSummary } from "@/lib/server-text";
import { ServerText } from "./ServerText";
import { ExplanationView } from "./ExplanationView";
import { HorizonPicker, Levels, NoLevels, WhyToggle } from "./ExitLevelsPanel";
import { PnlText } from "./Pnl";

const PRESETS: RiskPresetName[] = ["very_conservative", "conservative", "balanced", "balanced_aggressive", "aggressive", "very_aggressive"];
const finite = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);
const Txt = ({ children }: { children: string }) => <bdi dir="auto">{children}</bdi>;
const money = (n: number, cur: string, locale: string) => <span dir="ltr" className="tabular-nums">{formatMoney(n, cur, locale)}</span>;

function Star({ symbol }: { symbol: string }) {
  const t = useTranslations("analyze");
  const { data, mutate } = useWatchlist();
  const [err, setErr] = useState(false);
  const on = !!data?.some((w) => w.symbol === symbol);
  const toggle = async () => {
    setErr(false);
    try { if (on) await api.removeFromWatchlist(symbol); else await api.addToWatchlist(symbol); await mutate(); } catch { setErr(true); }
  };
  return (
    <div className="text-end">
      <button type="button" aria-pressed={on} aria-label={on ? t("inWatch", { symbol }) : t("addWatch", { symbol })} onClick={toggle} disabled={!data}
        className="inline-flex h-11 w-11 items-center justify-center rounded-full border border-line text-xl hover:bg-surface-2">
        <span aria-hidden="true" className={on ? "text-warn-fg" : "text-muted"}>{on ? "★" : "☆"}</span>
      </button>
      {err && <p role="alert" className="text-caption text-loss">{t("watchError")}</p>}
    </div>
  );
}

/** Inputs the user must give: no defaults, nothing preselected. Apply only sends what was chosen. */
function InputsForm({ portfolios, needs, heldHorizon, onApply, initial }: {
  portfolios: Portfolio[]; needs: string[]; heldHorizon: boolean; onApply: (q: AnalyzeQuery) => void; initial: AnalyzeQuery;
}) {
  const t = useTranslations("analyze");
  const s = useTranslations("settings");
  const [pid, setPid] = useState<number | null>(initial.portfolioId ?? null);
  const [amount, setAmount] = useState(initial.amount != null ? String(initial.amount) : "");
  const [currency, setCurrency] = useState<"ILS" | "USD" | null>(initial.currency ?? null);
  const [horizon, setHorizon] = useState<Horizon | null>(initial.horizon ?? null);
  const [risk, setRisk] = useState<RiskPresetName | "">(initial.risk ?? "");
  const [bad, setBad] = useState<string | null>(null);
  const flag = (k: string) => needs.includes(k);
  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const n = Number(amount.replace(",", "."));
    if (pid === null) { setBad("portfolio_id"); return; }
    if (!(n > 0) || !Number.isFinite(n)) { setBad("amount"); return; }
    if (!currency) { setBad("currency"); return; }
    setBad(null);
    onApply({ portfolioId: pid, amount: n, currency, horizon, risk: risk || null });
  };
  const miss = (k: string) => flag(k) ? "rounded-xl ring-2 ring-warn-fg p-2" : "p-2";
  return (
    <form onSubmit={submit} className="space-y-3" aria-label={t("fitTitle")}>
      <div className={miss("portfolio_id")}>
        <div id="f-pf" className="label">{t("formPortfolio")}</div>
        <div role="radiogroup" aria-labelledby="f-pf" className="flex flex-wrap gap-2">
          {portfolios.map((p) => (
            <button key={p.id} type="button" role="radio" aria-checked={pid === p.id} className={pid === p.id ? "btn-primary" : "btn-secondary"} onClick={() => setPid(p.id)}>{p.name}</button>
          ))}
        </div>
      </div>
      <div className={miss("amount")}>
        <label htmlFor="f-amount" className="label">{t("formAmount")}</label>
        <input id="f-amount" className="input" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} dir="ltr" />
      </div>
      <div className={miss("currency")}>
        <div id="f-cur" className="label">{t("formCurrency")}</div>
        <div role="radiogroup" aria-labelledby="f-cur" className="flex gap-2">
          {(["ILS", "USD"] as const).map((c) => (
            <button key={c} type="button" role="radio" aria-checked={currency === c} className={currency === c ? "btn-primary" : "btn-secondary"} onClick={() => setCurrency(c)}>{c === "ILS" ? "₪ ILS" : "$ USD"}</button>
          ))}
        </div>
      </div>
      <div className={miss("horizon")}>
        <div className="label">{heldHorizon ? t("formHorizonHeld") : t("formHorizon")}</div>
        <HorizonPicker value={horizon} onPick={setHorizon} label={t("formHorizon")} />
      </div>
      <div className="p-2">
        <label htmlFor="f-risk" className="label">{t("formRisk")}</label>
        <select id="f-risk" className="input" value={risk} onChange={(e) => setRisk(e.target.value as RiskPresetName | "")}>
          <option value="">{t("riskPortfolio")}</option>
          {PRESETS.map((p) => <option key={p} value={p}>{s(`presets.${p}`)}</option>)}
        </select>
      </div>
      {bad && <p role="alert" className="text-sm text-loss">{bad === "amount" ? t("invalidAmount") : t("needsBody")} ({t(`needs.${bad as "amount"}`)})</p>}
      <button type="submit" className="btn-primary">{t("formApply")}</button>
    </form>
  );
}

function ExposureTable({ rows }: { rows: ExposureCheck[] }) {
  const t = useTranslations("analyze");
  const locale = useLocale();
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-start text-sm">
        <thead className="text-xs text-muted">
          <tr>
            <th scope="col" className="py-1 pe-2 text-start font-medium">{t("colName")}</th>
            <th scope="col" className="px-2 text-start font-medium">{t("colBefore")}</th>
            <th scope="col" className="px-2 text-start font-medium">{t("colAfter")}</th>
            <th scope="col" className="px-2 text-start font-medium">{t("colCap")}</th>
            <th scope="col" className="ps-2 text-start font-medium"><span className="sr-only">{t("fitTitle")}</span></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((e) => (
            <tr key={e.rule} className="border-t border-line" data-testid={`exposure-${e.dimension}`}>
              <th scope="row" className="py-1 pe-2 text-start font-normal">{t(`dim.${e.dimension}`)} <bdi dir="auto" className="text-muted">{e.name}</bdi></th>
              <td className="px-2 tabular-nums" dir="ltr">{formatWeight(e.before_pct, locale)}</td>
              <td className="px-2 tabular-nums" dir="ltr">{finite(e.after_pct) ? formatWeight(e.after_pct, locale) : DASH}</td>
              <td className="px-2 tabular-nums" dir="ltr">{formatWeight(e.limit_pct, locale, 0)}</td>
              <td className="ps-2">{e.breaks ? <span className="chip-warn">{t("breaks")}</span> : <span className="chip-neutral">{t("withinCap")}</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FitBody({ f, cur }: { f: PortfolioFit; cur: string }) {
  const t = useTranslations("analyze");
  const locale = useLocale();
  const ms = f.max_position_size;
  const ss = f.suggested_size;
  return (
    <div className="space-y-4">
      <div className="space-y-1 rounded-xl border border-line p-3">
        <h3 className="text-heading">{t("maxSize")}</h3>
        {ms && finite(ms.max_additional_ils) && ms.max_additional_ils > 0 ? (
          <p className="text-lg font-bold" data-testid="max-size">
            <span dir="ltr" className="tabular-nums">{formatMoney(ms.max_additional_ils, "ILS", locale)}{finite(ms.max_additional_usd) && <span className="text-caption font-normal text-muted"> ({formatMoney(ms.max_additional_usd, "USD", locale)})</span>}</span>
          </p>
        ) : <p className="font-medium text-warn-fg" data-testid="max-size">{t("maxSizeNone")}</p>}
        {finite(f.amount) && f.currency && <p className="text-sm text-muted">{t("requested", { amount: formatMoney(f.amount, f.currency, locale) })}</p>}
        {ms && <p className="text-sm text-muted" dir="auto"><ServerText code={ms.reason_text} text={ms.reason} />{ms.binding_rule && <> {t("maxSizeNote", { rule: t.has(`rule.${ms.binding_rule}`) ? t(`rule.${ms.binding_rule as "max_sector_pct"}`) : ms.binding_rule })}</>}</p>}
      </div>

      <section aria-label={t("exposureTitle")} className="space-y-2">
        <h3 className="text-heading">{t("exposureTitle")}</h3>
        <ExposureTable rows={f.exposures ?? []} />
        {(f.caps_broken_at_requested_amount ?? []).length > 0 && (
          <p className="chip-warn" role="status">{t("capsBroken", { rules: f.caps_broken_at_requested_amount!.map((r) => (t.has(`rule.${r}`) ? t(`rule.${r as "max_sector_pct"}`) : r)).join(", ") })}</p>
        )}
      </section>

      {ss && (
        <section aria-label={t("suggested")} className="space-y-1 rounded-xl border border-line p-3">
          <h3 className="text-heading">{t("suggested")}</h3>
          <p>{t("suggestedQty", { qty: formatNumber(ss.quantity, locale, 4), cost: formatMoney(ss.cost_native, ss.currency, locale) })}</p>
          <p className="text-sm text-muted">{t("suggestedRisk", { risk: formatMoney(-Math.abs(ss.risk_ils), "ILS", locale, { signed: true }), pct: formatPct(ss.risk_pct_of_portfolio, locale) })}</p>
        </section>
      )}
      {finite(f.entry) && <p className="text-sm">{t("entry", { price: formatMoney(f.entry, cur, locale) })}</p>}

      <section aria-label={t("levelsTitle")} className="space-y-2">
        <h3 className="text-heading">{t("levelsTitle")}</h3>
        {f.levels && f.levels.status === "levels" ? <Levels r={f.levels} uid="fit" /> : f.levels ? <NoLevels r={f.levels} />
          : <p className="rounded-xl bg-warn-bg p-3 text-warn-fg" role="status">{t("levelsNoneBody", { reason: f.levels_unavailable_reason ?? DASH })}</p>}
      </section>

      {((f.rules ?? []).length > 0 || (f.rules_not_checked ?? []).length > 0) && (
        <section aria-label={t("rulesTitle")} className="space-y-1">
          <h3 className="text-heading">{t("rulesTitle")}</h3>
          <ul className="list-disc space-y-1 ps-5 text-sm">
            {(f.rules ?? []).map((r) => <li key={r.rule}><span className="chip-neutral">{t(`ruleStatus.${r.status}`)}</span> <bdi dir="auto">{r.reason}</bdi></li>)}
          </ul>
          {(f.rules_not_checked ?? []).length > 0 && <p className="text-sm text-muted">{t("notChecked", { rules: f.rules_not_checked!.join(", ") })}</p>}
        </section>
      )}
    </div>
  );
}

function FitSection({ d, portfolios, query, setQuery }: { d: AnalyzeOut; portfolios: Portfolio[] | undefined; query: AnalyzeQuery; setQuery: (q: AnalyzeQuery) => void }) {
  const t = useTranslations("analyze");
  const locale = useLocale();
  const f = d.portfolio_fit;
  const needs = (f.needs_input ?? d.needs_input ?? []) as string[];
  const [editing, setEditing] = useState(false);
  const showForm = f.status === "incomplete" || editing;
  const tone = f.status === "fits" ? "chip-brand" : f.status === "incomplete" ? "chip-neutral" : "chip-warn";
  const cur = d.scout.currency;
  return (
    <section className="card space-y-4" aria-label={t("fitTitle")} data-testid="fit-section" data-status={f.status}>
      <h2 className="text-lg font-bold">{t("fitTitle")}</h2>
      <p className={`${tone} text-sm`} role="status" data-testid="fit-status">{t(`fit.${f.status}`)}</p>
      {f.status === "incomplete" && isFitIncompleteSummary(f.summary) ? <p className="text-sm">{t("fitIncompleteSummary")}</p> : <p className="text-sm" dir="auto"><Txt>{f.summary}</Txt></p>}
      {f.held ? (
        <p className="text-sm" data-testid="held">
          <span className="chip-brand">{t("held")}</span>{" "}
          {t("heldDetail", { qty: formatNumber(f.held.quantity, locale, 4), weight: formatWeight(f.held.weight_pct, locale), value: formatMoney(f.held.value_ils, "ILS", locale) })}
          {f.held.via_dual_listing && ` (${t("dual")})`}
        </p>
      ) : f.portfolio_id ? <p className="text-sm text-muted" data-testid="held"><span className="chip-neutral">{t("heldNew")}</span></p> : null}

      {f.status === "incomplete" && (
        <div className="space-y-1 rounded-xl bg-warn-bg p-3 text-warn-fg" role="status">
          <p className="font-semibold">{t("needsTitle")}</p>
          <p className="text-sm">{t("needsBody")}</p>
          <ul className="list-disc ps-5 text-sm">{needs.map((k) => <li key={k}>{t.has(`needs.${k}`) ? t(`needs.${k as "amount"}`) : k}</li>)}</ul>
        </div>
      )}
      {portfolios && portfolios.length === 0 && <p className="card">{t("noPortfolios")}</p>}
      {showForm && portfolios && portfolios.length > 0 && (
        <InputsForm portfolios={portfolios} needs={needs} heldHorizon={!!f.held?.horizon} initial={query} onApply={(q) => { setQuery(q); setEditing(false); }} />
      )}
      {!showForm && <button type="button" className="btn-secondary" onClick={() => setEditing(true)}>{t("formEdit")}</button>}

      {f.status !== "incomplete" && <FitBody f={f} cur={cur} />}
      <WhyToggle id="why-fit"><ExplanationView e={f.explanation} currency={cur} /></WhyToggle>
    </section>
  );
}

function Gate({ d }: { d: AnalyzeOut }) {
  const t = useTranslations("analyze");
  const c = d.candidate_info;
  return (
    <section className="card space-y-2 border-warn-fg" aria-label={t("gateTitle")} data-testid="gate-notice">
      <h2 className="text-heading">{t("gateTitle")}</h2>
      <p className="text-sm">{t("gateBody")}</p>
      {!c.launch_gate_open && c.launch_gate_reasons.length > 0 && (
        <div className="text-sm text-muted">
          <p>{t("gateReasons")}</p>
          <ul className="list-disc ps-5">{c.launch_gate_reasons.map((r) => <li key={r} dir="auto"><Txt>{r}</Txt></li>)}</ul>
        </div>
      )}
    </section>
  );
}

function Signals({ d }: { d: AnalyzeOut }) {
  const t = useTranslations("analyze");
  const h = useTranslations("holding");
  const locale = useLocale();
  const ch = d.chart;
  const c = d.candidate_info;
  return (
    <section className="card space-y-3" aria-label={t("signalsTitle")}>
      <h2 className="text-heading">{t("signalsTitle")}</h2>
      <p className="text-sm text-muted">{t("signalsNote")}</p>
      <p className="text-sm" data-testid="score-line">
        {c.score_available ? t("scoreLine", { score: `${c.score > 0 ? "+" : ""}${formatNumber(c.score, locale, 0)}`, conf: formatWeight(c.confidence * 100, locale, 0) }) : t("scoreNone")}
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-start text-sm">
          <thead className="text-xs text-muted">
            <tr>
              <th scope="col" className="py-1 pe-2 text-start font-medium">{t("colSignal")}</th>
              <th scope="col" className="px-2 text-start font-medium">{t("colScore")}</th>
              <th scope="col" className="px-2 text-start font-medium">{t("colWeight")}</th>
              <th scope="col" className="ps-2 text-start font-medium">{t("colConf")}</th>
            </tr>
          </thead>
          <tbody>
            {ch.breakdown.map((s) => (
              <tr key={s.name} className="border-t border-line align-top" data-testid={`signal-${s.name}`}>
                <th scope="row" className="py-1 pe-2 text-start font-normal">
                  {h.has(`signal.${s.name}`) ? h(`signal.${s.name}`) : s.name}
                  {s.available ? <ul className="list-disc ps-4 text-caption text-muted">{s.reasons.map((r) => <li key={r} dir="auto"><Txt>{r}</Txt></li>)}</ul> : <span className="block text-caption text-muted">{t("noData")}</span>}
                </th>
                <td className="px-2 tabular-nums" dir="ltr">{s.available ? `${s.score > 0 ? "+" : ""}${formatNumber(s.score, locale, 0)}` : DASH}</td>
                <td className="px-2 tabular-nums" dir="ltr">{s.available ? formatWeight(s.weight, locale, 0) : DASH}</td>
                <td className="ps-2 tabular-nums" dir="ltr">{s.available ? formatWeight(s.confidence * 100, locale, 0) : DASH}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function ChartSection({ d }: { d: AnalyzeOut }) {
  const t = useTranslations("analyze");
  const locale = useLocale();
  const ch = d.chart;
  const cur = d.scout.currency;
  const inds = Object.entries(ch.indicators ?? {});
  const levels = ch.levels ?? [];
  return (
    <section className="card space-y-3" aria-label={t("chartTitle")}>
      <h2 className="text-heading">{t("chartTitle")}</h2>
      {!ch.available && levels.length === 0 && (ch.annotations ?? []).length === 0 ? <p className="text-sm text-muted">{t("chartNone")}</p> : (
        <>
          {levels.length > 0 && (
            <ul className="space-y-1 text-sm" aria-label={t("chartTitle")}>
              {levels.map((l, i) => (
                <li key={i} className="flex flex-wrap items-center gap-2" data-testid={`chart-level-${l.kind}`}>
                  <span className="chip-neutral">{t(l.kind)}</span>
                  {money(l.price, cur, locale)}
                  <PnlText pct={l.distance_pct} locale={locale} />
                  <span className="text-caption text-muted">{t("touches", { n: l.touches })}</span>
                </li>
              ))}
            </ul>
          )}
          {(ch.annotations ?? []).length > 0 && (
            <ul className="list-disc ps-5 text-sm">
              {ch.annotations!.map((a, i) => <li key={i}>{t(`annotation.${a.kind}`)}: <bdi dir="auto">{a.label}</bdi>{finite(a.price) && <> · {money(a.price, cur, locale)}</>}</li>)}
            </ul>
          )}
          {inds.length > 0 && (
            <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4" aria-label={t("indicators")}>
              {inds.map(([k, v]) => <div key={k}><dt className="text-caption text-muted" dir="ltr">{k}</dt><dd className="tabular-nums" dir="ltr">{formatNumber(v, locale, 2)}</dd></div>)}
            </dl>
          )}
        </>
      )}
      <WhyToggle id="why-chart"><ExplanationView e={ch.explanation} currency={cur} /></WhyToggle>
    </section>
  );
}

function Missing({ d }: { d: AnalyzeOut }) {
  const t = useTranslations("analyze");
  const h = useTranslations("holding");
  const rows = d.scout.not_available ?? [];
  if (rows.length === 0) return null;
  return (
    <section className="card space-y-2" aria-label={t("missingTitle")} data-testid="not-available">
      <h2 className="text-heading">{t("missingTitle")}</h2>
      <p className="text-sm text-muted">{t("missingNote")}</p>
      <ul className="space-y-1 text-sm">
        {rows.map((m) => <li key={m.name}><span className="font-medium">{h.has(`signal.${m.name}`) ? h(`signal.${m.name}`) : m.name}</span>: <bdi dir="auto" className="text-muted">{m.reason}</bdi></li>)}
      </ul>
    </section>
  );
}

function AskBox({ symbol }: { symbol: string }) {
  const t = useTranslations("analyze");
  const [question, setQuestion] = useState("");
  const [notes, setNotes] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(false);
  const [empty, setEmpty] = useState(false);
  const [ans, setAns] = useState<AskOut | null>(null);
  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!question.trim()) { setEmpty(true); return; }
    setEmpty(false); setErr(false); setBusy(true);
    try { setAns(await api.askAboutStock(symbol, { question: question.trim(), notes: notes.trim() || null })); }
    catch { setErr(true); setAns(null); }
    finally { setBusy(false); }
  };
  return (
    <section className="card space-y-3" aria-label={t("askTitle")}>
      <h2 className="text-heading">{t("askTitle")}</h2>
      <p className="text-sm text-muted">{t("askNote")}</p>
      <form onSubmit={send} className="space-y-2">
        <label htmlFor="ask-q" className="label">{t("askLabel")}</label>
        <textarea id="ask-q" className="input" rows={2} maxLength={1000} value={question} onChange={(e) => setQuestion(e.target.value)} dir="auto" />
        <label htmlFor="ask-n" className="label">{t("askNotes")}</label>
        <textarea id="ask-n" className="input" rows={2} maxLength={2000} value={notes} onChange={(e) => setNotes(e.target.value)} dir="auto" />
        {empty && <p role="alert" className="text-sm text-loss">{t("askEmpty")}</p>}
        <button type="submit" className="btn-primary" disabled={busy}>{busy ? t("askSending") : t("askSend")}</button>
      </form>
      {err && <p role="alert" className="text-loss">{t("askError")}</p>}
      {ans && (
        <div className="space-y-2 rounded-xl bg-surface-2 p-3" data-testid="ask-answer" aria-live="polite">
          {ans.question_declined && <p className="chip-warn" role="status">{t("askDeclined")}</p>}
          <p dir="auto"><Txt>{ans.answer}</Txt></p>
          <p className="text-caption text-muted">{ans.llm_used ? t("askLlm") : t("askTemplate")}</p>
          {ans.grounded_in.length > 0 && <p className="text-caption text-muted" dir="auto">{t("askGrounded", { items: ans.grounded_in.join(", ") })}</p>}
        </div>
      )}
    </section>
  );
}

function Header({ d }: { d: AnalyzeOut }) {
  const t = useTranslations("analyze");
  const locale = useLocale();
  const s = d.scout;
  const p = s.price;
  return (
    <div className="space-y-2">
      <Link href="/analyze" className="text-sm text-brand-text hover:underline">← {t("back")}</Link>
      <div className="flex items-start justify-between gap-2">
        <div>
          <h1 className="text-2xl font-bold"><bdi dir="auto">{locale === "he" ? s.name_he : s.name_en}</bdi></h1>
          <p className="text-sm text-muted"><span dir="ltr">{s.symbol}</span> · <bdi dir="auto">{s.sector}</bdi> · <bdi dir="auto">{s.country}</bdi></p>
        </div>
        <Star symbol={s.symbol} />
      </div>
      {p ? (
        <div data-testid="price">
          <p className="text-xl font-bold">{money(p.price, p.currency, locale)} {finite(p.change_pct) && <PnlText pct={p.change_pct} locale={locale} className="text-base" />}</p>
          {p.is_fresh ? <p className="text-caption text-muted">{t("priceLive")} · {formatTime(p.as_of, locale)}</p>
            : <p className="chip-warn" role="status">{t("priceStale", { time: formatTime(p.as_of, locale) })}</p>}
        </div>
      ) : <p className="chip-warn" role="status">{s.price_reason ?? t("noPrice")}</p>}
      <WhyToggle id="why-scout"><ExplanationView e={s.explanation} currency={s.currency} /></WhyToggle>
    </div>
  );
}

/** One analysis. The result and the typed inputs live only in memory: nothing is saved to storage. */
export function AnalyzeResult({ symbol }: { symbol: string }) {
  const t = useTranslations("analyze");
  const c = useTranslations("common");
  const [query, setQuery] = useState<AnalyzeQuery>({});
  const { data, error, mutate, isLoading } = useAnalyze(symbol, query);
  const { data: portfolios } = usePortfolios();
  const [last, setLast] = useState<AnalyzeOut | null>(null);
  if (data && data !== last) setLast(data); // keep the previous result visible while new inputs load
  const d = data ?? last;

  if (error && !d) {
    if (error instanceof ApiError && error.status === 404) {
      return (
        <div className="space-y-3">
          <Link href="/analyze" className="text-sm text-brand-text hover:underline">← {t("back")}</Link>
          <div className="card space-y-1" role="alert" data-testid="not-found">
            <h1 className="text-heading">{t("notFoundTitle")}</h1>
            <p>{t("notFoundBody", { symbol })}</p>
          </div>
        </div>
      );
    }
    return (
      <div className="space-y-2">
        <Link href="/analyze" className="text-sm text-brand-text hover:underline">← {t("back")}</Link>
        <p role="alert" className="text-loss">{t("error")}</p>
        <button type="button" className="btn-secondary" onClick={() => mutate()}>{t("retry")}</button>
      </div>
    );
  }
  if (!d) return <p role="status" className="text-muted">{isLoading ? t("loading") : c("loading")}</p>;
  return (
    <div className="space-y-4" aria-busy={isLoading}>
      <Header d={d} />
      <FitSection d={d} portfolios={portfolios} query={query} setQuery={setQuery} />
      <Gate d={d} />
      <Signals d={d} />
      <ChartSection d={d} />
      <Missing d={d} />
      <AskBox symbol={symbol} />
      <p className="text-xs text-muted">{d.disclaimer ?? t("disclaimer")}</p>
    </div>
  );
}
