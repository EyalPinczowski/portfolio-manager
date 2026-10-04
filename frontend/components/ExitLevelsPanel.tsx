"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { api, type ExitLevel, type ScaleOutPlan, type ExitLevelsResult, type Horizon } from "@/lib/api";
import { DASH, formatMoney, formatNumber, formatPct, formatTime, formatWeight } from "@/lib/format";
import { useExitLevels } from "@/lib/hooks";
import { horizonFromLabel, isPlanNote, matchStopReason, sourceKey } from "@/lib/server-text";
import { ExplanationView } from "./ExplanationView";
import { PnlText } from "./Pnl";

export const HORIZONS: Horizon[] = ["1w", "1m", "3m", "6m", "1y"];

const finite = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);

/** Radio group with NO preselection unless `value` is given. */
export function HorizonPicker({ value, onPick, label, disabled }: {
  value: Horizon | null; onPick: (h: Horizon) => void; label: string; disabled?: boolean;
}) {
  const t = useTranslations("holding");
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-wrap gap-2">
      {HORIZONS.map((h) => (
        <button
          key={h} type="button" role="radio" aria-checked={value === h} disabled={disabled}
          onClick={() => onPick(h)} className={value === h ? "btn-primary" : "btn-secondary"}
        >
          {t(`horizons.${h}`)}
        </button>
      ))}
    </div>
  );
}

/** ILS large, USD small beside it. */
function TwoMoney({ ils, usd, signed }: { ils: number; usd: number; signed?: boolean }) {
  const locale = useLocale();
  return (
    <span dir="ltr" className="tabular-nums">
      {formatMoney(ils, "ILS", locale, { signed })} <span className="text-caption text-muted">({formatMoney(usd, "USD", locale, { signed })})</span>
    </span>
  );
}

/** Expander that renders a typed Explanation. Facts and reasons only, never a verdict. */
export function WhyToggle({ id, children }: { id: string; children: React.ReactNode }) {
  const t = useTranslations("exit");
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" className="btn-secondary" aria-expanded={open} aria-controls={id} onClick={() => setOpen((v) => !v)}>
        {open ? t("whyHide") : t("why")}
      </button>
      {open && <div id={id} className="mt-2 rounded-xl bg-surface-2 p-3">{children}</div>}
    </div>
  );
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-caption text-muted">{label}</dt>
      <dd className="font-medium">{children}</dd>
    </div>
  );
}

/** Reason in the user's language when the sentence is a known shape (else server text in a <bdi>), then the source on its own line. */
function LevelReason({ lv }: { lv: ExitLevel }) {
  const t = useTranslations("exit");
  const m = matchStopReason(lv.reason);
  const sk = sourceKey(lv.source);
  const src = sk ? t(`sources.${sk.key}` as "sources.atr", { n: sk.n ?? "" }) : lv.source;
  return (
    <div className="text-sm text-muted">
      <p dir="auto">{m ? t(`reasonText.${m.key}` as "reasonText.atr", m.values) : <bdi dir="auto">{lv.reason}</bdi>}</p>
      <p className="text-caption">{t("source", { source: src })}</p>
    </div>
  );
}

export function LevelCard({ lv, currency, uid }: { lv: ExitLevel; currency: string; uid: string }) {
  const t = useTranslations("exit");
  const locale = useLocale();
  const hasPnl = finite(lv.pnl_ils);
  return (
    <li className="rounded-xl border border-line p-3 space-y-2" data-testid={`level-${lv.kind}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h4 className="font-semibold">{t(`kind.${lv.kind}`)}</h4>
        {lv.reached && <span className="chip-warn">{t("reached")}</span>}
      </div>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-4">
        <Stat label={t("price")}><span dir="ltr" className="tabular-nums">{formatMoney(lv.price, currency, locale)}</span></Stat>
        <Stat label={t("distance")}><PnlText pct={lv.distance_pct} locale={locale} /></Stat>
        <Stat label={t("valueChange")}>
          <PnlText value={lv.vs_price_ils} currency="ILS" locale={locale} />
          <span className="block text-caption"><PnlText value={lv.vs_price_usd} currency="USD" locale={locale} /></span>
        </Stat>
        <Stat label={t("pnl")}>
          {hasPnl ? (
            <>
              <PnlText value={lv.pnl_ils} currency="ILS" locale={locale} />
              {finite(lv.pnl_usd) && <span className="block text-caption"><PnlText value={lv.pnl_usd} currency="USD" locale={locale} /></span>}
            </>
          ) : <span className="text-muted" title={t("pnlUnknown")}>{DASH}<span className="sr-only"> {t("pnlUnknown")}</span></span>}
        </Stat>
        {lv.kind === "take_profit" && (
          <Stat label={t("rr")}><span dir="ltr" className="tabular-nums">{finite(lv.rr) ? `1:${formatNumber(lv.rr, locale, 2)}` : DASH}</span></Stat>
        )}
      </dl>
      <LevelReason lv={lv} />
      {lv.kind === "trailing_stop" && <p className="text-caption text-muted">{t("trailNote")}</p>}
      <WhyToggle id={`why-${uid}`}><ExplanationView e={lv.explanation} currency={currency} /></WhyToggle>
    </li>
  );
}

/** The profile's scale-out plan: shares as a bar and list, rules in plain words, the server note and a Why?. */
export function ScalePlan({ plan, r, uid }: { plan: ScaleOutPlan; r: ExitLevelsResult; uid: string }) {
  const t = useTranslations("exit");
  const st = useTranslations("settings");
  const locale = useLocale();
  const cur = r.currency ?? "ILS";
  const profile = st.has(`presets.${plan.profile}`) ? st(`presets.${plan.profile}`) : plan.profile;
  const qty = r.size_guidance?.current_quantity;
  const parts = [
    { key: "planFirst", fraction: plan.first_fraction, price: r.take_profits?.[0]?.price, bar: "bg-brand" },
    { key: "planSecond", fraction: plan.second_fraction, price: r.take_profits?.[1]?.price, bar: "bg-gain" },
    { key: "planTrail", fraction: plan.trail_fraction, price: null, bar: "bg-muted" },
  ];
  return (
    <section aria-label={t("planTitle", { profile })} className="space-y-2 rounded-xl border border-line p-3" data-testid="scale-plan">
      <h3 className="text-heading">{t("planTitle", { profile })}</h3>
      {plan.used_fallback && <p className="chip-warn" role="status">{t("planFallback", { profile })}</p>}
      <div className="flex h-2 overflow-hidden rounded-full bg-surface-2" role="img" aria-label={parts.map((p) => `${t(p.key)} ${formatWeight(p.fraction * 100, locale, 0)}`).join(", ")} dir="ltr">
        {parts.map((p) => <span key={p.key} className={p.bar} style={{ width: `${Math.max(0, p.fraction * 100)}%` }} />)}
      </div>
      <ul className="space-y-1 text-sm">
        {parts.map((p) => (
          <li key={p.key} data-testid={`plan-${p.key}`}>
            <span className="font-medium">{t(p.key)}</span>: {t("planShare", { pct: formatWeight(p.fraction * 100, locale, 0) })}
            {finite(p.price) && finite(qty) && (
              <> {t("planAt", { price: formatMoney(p.price, cur, locale), amount: formatMoney(p.price * qty * p.fraction, cur, locale) })}</>
            )}
          </li>
        ))}
      </ul>
      <p className="text-sm">{t("planTrailRule", { scale: formatNumber(plan.trail_atr_scale, locale, 2) })}</p>
      <p className="text-sm">{t("planBreakeven", { atr: formatNumber(plan.breakeven_atr_multiple, locale, 2) })}</p>
      {!isPlanNote(plan.note) && <p className="text-sm text-muted" dir="auto"><bdi dir="auto">{plan.note}</bdi></p>}
      <p className="text-caption text-muted">{t("planNote")}</p>
      <WhyToggle id={`why-${uid}-plan`}><ExplanationView e={plan.explanation} currency={cur} /></WhyToggle>
    </section>
  );
}

export function Levels({ r, uid }: { r: ExitLevelsResult; uid: string }) {
  const t = useTranslations("exit");
  const h = useTranslations("holding");
  const locale = useLocale();
  const cur = r.currency ?? "ILS";
  const hz = horizonFromLabel(r.horizon_label) ?? r.horizon ?? null;
  const horizonText = hz ? h(`horizons.${hz}`) : (r.horizon_label ?? "");
  const stops = [r.stop, r.trailing_stop, r.breakeven].filter((x): x is ExitLevel => !!x);
  const sg = r.size_guidance;
  const rs = r.risk_to_stop;
  return (
    <div className="space-y-4">
      <p className="font-medium">{t("headline", { horizon: horizonText })}</p>
      {finite(r.price) && (
        <p className="text-sm text-muted">
          {t("priceAsOf", { price: formatMoney(r.price, cur, locale), time: r.price_as_of ? `${formatTime(r.price_as_of, locale)}` : DASH })}
        </p>
      )}
      {r.stop_fit && r.stop_fit !== "ok" && <p className="chip-warn" role="status">{t(`fit.${r.stop_fit}`)}</p>}

      {stops.length > 0 && (
        <section aria-label={t("stopSection")} className="space-y-2">
          <h3 className="text-heading">{t("stopSection")}</h3>
          <ul className="space-y-2">{stops.map((l) => <LevelCard key={l.kind} lv={l} currency={cur} uid={`${uid}-${l.kind}`} />)}</ul>
        </section>
      )}
      {(r.take_profits ?? []).length > 0 && (
        <section aria-label={t("tpSection")} className="space-y-2">
          <h3 className="text-heading">{t("tpSection")}</h3>
          <ul className="space-y-2">{(r.take_profits ?? []).map((l, i) => <LevelCard key={`${l.price}-${i}`} lv={l} currency={cur} uid={`${uid}-tp${i}`} />)}</ul>
        </section>
      )}
      {(r.scale_out ?? []).length > 0 && (
        <section aria-label={t("scaleTitle")} className="space-y-2">
          <h3 className="text-heading">{t("scaleTitle")}</h3>
          <ol className="list-decimal space-y-1 ps-5 text-sm">
            {(r.scale_out ?? []).map((s, i) => (
              <li key={i}>
                {s.step === "take_profit" && finite(s.price)
                  ? t("stepTakeProfit", { pct: formatWeight(s.fraction * 100, locale, 0), price: formatMoney(s.price, cur, locale), qty: formatNumber(s.quantity, locale, 4) })
                  : t("stepTrail", { pct: formatWeight(s.fraction * 100, locale, 0), qty: formatNumber(s.quantity, locale, 4) })}
              </li>
            ))}
          </ol>
        </section>
      )}
      {r.scale_out_plan && <ScalePlan plan={r.scale_out_plan} r={r} uid={uid} />}
      {sg && (
        <section aria-label={t("sizeTitle")} className="space-y-1 rounded-xl border border-line p-3">
          <h3 className="text-heading">{t("sizeTitle")}</h3>
          <p className={sg.needed ? "font-medium text-warn-fg" : "text-sm"} role={sg.needed ? "status" : undefined}>
            {sg.needed
              ? t("sizeSmaller", { suggested: formatNumber(sg.suggested_quantity, locale, 4), current: formatNumber(sg.current_quantity, locale, 4), pct: formatWeight(sg.keep_fraction * 100, locale, 0) })
              : t("sizeOk")}
          </p>
          {sg.reason && <p className="text-sm text-muted" dir="auto"><bdi dir="auto">{sg.reason}</bdi></p>}
          {sg.rules.length > 0 && <ul className="list-disc ps-5 text-sm text-muted">{sg.rules.map((x, i) => <li key={i} dir="auto"><bdi dir="auto">{x}</bdi></li>)}</ul>}
        </section>
      )}
      {rs && (
        <section aria-label={t("riskTitle")} className="space-y-1 rounded-xl border border-line p-3">
          <h3 className="text-heading">{t("riskTitle")}</h3>
          <p><TwoMoney ils={-Math.abs(rs.ils)} usd={-Math.abs(rs.usd)} signed /></p>
          <p className="text-sm text-muted">
            {t("riskOfPosition", { pct: formatPct(rs.pct_of_position, locale) })}
            {finite(rs.pct_of_portfolio) && ` · ${t("riskOfPortfolio", { pct: formatPct(rs.pct_of_portfolio, locale) })}`}
          </p>
        </section>
      )}
      <WhyToggle id={`why-${uid}-all`}>
        <div className="space-y-3">
          <h4 className="font-semibold">{t("whyOverall")}</h4>
          <ExplanationView e={r.explanation} currency={cur} />
          {(r.candidates ?? []).length > 0 && (
            <div className="text-sm">
              <h4 className="font-semibold">{t("candidates")}</h4>
              <ul className="list-disc ps-5">
                {(r.candidates ?? []).map((c, i) => (
                  <li key={i} dir="auto">
                    <span dir="ltr">{c.source} · {formatMoney(c.price, cur, locale)} · {formatPct(c.distance_pct, locale, { signed: true })}</span>
                    {c.chosen && <span className="chip-brand ms-2">{t("chosen")}</span>} <bdi dir="auto" className="text-muted">{c.note}</bdi>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {(r.skipped ?? []).length > 0 && (
            <div className="text-sm">
              <h4 className="font-semibold">{t("skipped")}</h4>
              <ul className="list-disc ps-5">{(r.skipped ?? []).map((x, i) => <li key={i} dir="auto"><span dir="ltr">{x.source}</span>: <bdi dir="auto">{x.reason}</bdi></li>)}</ul>
            </div>
          )}
          {r.state && finite(r.state.highest_high) && finite(r.state.stop) && (
            <p className="text-sm text-muted">{t("state", { high: formatMoney(r.state.highest_high, cur, locale), stop: formatMoney(r.state.stop, cur, locale) })}</p>
          )}
        </div>
      </WhyToggle>
    </div>
  );
}

export function NoLevels({ r }: { r: ExitLevelsResult }) {
  const t = useTranslations("exit");
  const code = r.reason_code ?? "stale_price";
  return (
    <div className="space-y-2 rounded-xl bg-warn-bg p-3 text-warn-fg" role="status">
      <p className="font-semibold">{t("noLevelsTitle")}</p>
      <p>{t.has(`reason.${code}`) ? t(`reason.${code}`) : r.reason}</p>
      {r.reason && code !== "fund_no_levels" && <p className="text-sm" dir="auto"><bdi dir="auto">{r.reason}</bdi></p>}
      <p className="text-sm">{code === "fund_no_levels" ? t("fundNoLevelsNote") : t("noLevelsNumbers")}</p>
    </div>
  );
}

/**
 * Exit levels of one holding. needs_horizon -> ask (no default, saves via the holding PATCH unless "preview only");
 * no_levels -> the reason and no numbers; levels -> stops, take-profits, scale-out, size guidance, each with a "Why?".
 */
export function ExitLevelsPanel({ holdingId, portfolioId, onHorizonSaved }: {
  holdingId: number; portfolioId: number; onHorizonSaved?: () => void | Promise<unknown>;
}) {
  const t = useTranslations("exit");
  const h = useTranslations("holding");
  const [whatIf, setWhatIf] = useState<Horizon | null>(null);
  const [previewOnly, setPreviewOnly] = useState(false);
  const [saveErr, setSaveErr] = useState(false);
  const [busy, setBusy] = useState(false);
  const { data, error, mutate } = useExitLevels(holdingId, whatIf);

  const pick = async (hz: Horizon) => {
    setSaveErr(false);
    if (previewOnly) { setWhatIf(hz); return; }
    setBusy(true);
    try {
      await api.patchHolding(portfolioId, holdingId, { horizon: hz });
      setWhatIf(null);
      await Promise.all([mutate(), onHorizonSaved?.()]);
    } catch { setSaveErr(true); }
    finally { setBusy(false); }
  };

  let body: React.ReactNode;
  if (error) {
    body = (
      <div className="space-y-2">
        <p role="alert" className="text-loss">{t("error")}</p>
        <button type="button" className="btn-secondary" onClick={() => mutate()}>{t("retry")}</button>
      </div>
    );
  } else if (!data) {
    body = <p role="status" className="text-muted">{t("loading")}</p>;
  } else if (data.status === "needs_horizon") {
    body = (
      <div className="space-y-3">
        <p className="rounded-xl bg-warn-bg p-3 font-medium text-warn-fg" role="status">{t("needsHorizonTitle")}</p>
        <p className="text-sm">{t("needsHorizonBody")}</p>
        <HorizonPicker value={null} onPick={pick} label={t("pickHorizon")} disabled={busy} />
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={previewOnly} onChange={(e) => setPreviewOnly(e.target.checked)} />
          {t("previewToggle")}
        </label>
        {!previewOnly && <p className="text-caption text-muted">{t("saveToggle")}</p>}
        {saveErr && <p role="alert" className="text-sm text-loss">{t("saveError")}</p>}
      </div>
    );
  } else {
    body = (
      <div className="space-y-4">
        {whatIf && (
          <p className="flex flex-wrap items-center gap-2 rounded-xl bg-brand-soft p-3 text-sm text-brand-text" role="status">
            {t("whatIfActive", { value: h(`horizons.${whatIf}`) })}
            <button type="button" className="btn-secondary" onClick={() => setWhatIf(null)}>{t("whatIfClear")}</button>
          </p>
        )}
        {data.status === "no_levels" ? <NoLevels r={data} /> : <Levels r={data} uid={`h${holdingId}`} />}
        {data.reason_code !== "fund_no_levels" && (
          <div className="space-y-2">
            <p className="text-sm text-muted">{t("whatIf")}</p>
            <HorizonPicker value={whatIf} onPick={setWhatIf} label={t("whatIf")} />
          </div>
        )}
        {data.disclaimer && <p className="text-xs text-muted">{data.disclaimer}</p>}
      </div>
    );
  }
  return (
    <section className="card space-y-3" aria-label={h("exitTitle")}>
      <h2 className="text-lg font-bold">{h("exitTitle")}</h2>
      <p className="text-sm text-muted">{t("intro")}</p>
      {body}
    </section>
  );
}
