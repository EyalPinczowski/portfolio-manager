"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { api, type ExitLevel, type ScaleOutPlan, type ExitLevelsResult, type Horizon } from "@/lib/api";
import { DASH, mainFirst, formatMoney, formatNumber, formatPct, formatTime, formatWeight } from "@/lib/format";
import { useExitLevels } from "@/lib/hooks";
import { horizonFromLabel, isPlanNote, matchStopReason, sourceKey } from "@/lib/server-text";
import { ServerText } from "./ServerText";
import { ExplanationView } from "./ExplanationView";
import { LevelLadder } from "./LevelLadder";
import { MoreSections, type MoreItem } from "./MoreSections";

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

/** The main currency large, the other small beside it. */
function TwoMoney({ ils, usd, signed }: { ils: number; usd: number; signed?: boolean }) {
  const locale = useLocale();
  const m = mainFirst(ils, usd);
  return (
    <span dir="ltr" className="tabular-nums">
      {formatMoney(m.main.v, m.main.cur, locale, { signed })} <span className="text-caption text-muted">({formatMoney(m.other.v, m.other.cur, locale, { signed })})</span>
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

/** Reason in the user's language when the sentence is a known shape (else server text in a <bdi>), then the source on its own line. */
function LevelReason({ lv }: { lv: ExitLevel }) {
  const t = useTranslations("exit");
  const m = matchStopReason(lv.reason);
  const sk = sourceKey(lv.source);
  const src = sk ? t(`sources.${sk.key}` as "sources.atr", { n: sk.n ?? "" }) : lv.source;
  return (
    <div className="text-sm text-muted">
      <p dir="auto">{lv.reason_text || !m ? <ServerText code={lv.reason_text} text={lv.reason} /> : t(`reasonText.${m.key}` as "reasonText.atr", m.values)}</p>
      <p className="text-caption">{t("source", { source: src })}</p>
    </div>
  );
}

/** Where one level comes from: the reason in the user's language, the source and its own Why?. Collapsed by the page. */
export function LevelReasons({ r, uid }: { r: ExitLevelsResult; uid: string }) {
  const t = useTranslations("exit");
  const cur = r.currency ?? "ILS";
  const all: { key: string; label: string; lv: ExitLevel }[] = [];
  for (const k of ["stop", "trailing_stop", "breakeven"] as const) { const lv = r[k]; if (lv) all.push({ key: k, label: t(`kind.${k}`), lv }); }
  (r.take_profits ?? []).forEach((lv, i) => all.push({ key: `tp${i}`, label: t("tpN", { n: i + 1 }), lv }));
  if (all.length === 0) return null;
  return (
    <ul className="space-y-3" aria-label={t("levelReasons")}>
      {all.map(({ key, label, lv }) => (
        <li key={key} className="space-y-1" data-testid={`reason-${lv.kind === "take_profit" ? key : lv.kind}`}>
          <h4 className="font-semibold">{label}</h4>
          <LevelReason lv={lv} />
          <WhyToggle id={`why-${uid}-${key}`}><ExplanationView e={lv.explanation} currency={cur} /></WhyToggle>
        </li>
      ))}
    </ul>
  );
}

/** The profile's scale-out plan as one stacked bar with three short labels. The rules live in `PlanRules` (collapsed). */
export function ScalePlan({ plan, r }: { plan: ScaleOutPlan; r: ExitLevelsResult }) {
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
    <section aria-label={t("planTitle", { profile })} className="space-y-2" data-testid="scale-plan">
      <h3 className="text-heading">{t("planTitle", { profile })}</h3>
      {plan.used_fallback && <p className="chip-warn" role="status">{t("planFallback", { profile })}</p>}
      <div className="flex h-2 overflow-hidden rounded-full bg-surface-2" role="img" aria-label={parts.map((p) => `${t(p.key)} ${formatWeight(p.fraction * 100, locale, 0)}`).join(", ")} dir="ltr">
        {parts.map((p) => <span key={p.key} className={p.bar} style={{ width: `${Math.max(0, p.fraction * 100)}%` }} />)}
      </div>
      <ul className="space-y-0.5 text-sm">
        {parts.map((p) => (
          <li key={p.key} data-testid={`plan-${p.key}`}>
            <span aria-hidden="true" className={`me-1.5 inline-block h-2 w-2 rounded-full align-middle ${p.bar}`} />
            <span className="font-medium">{t(p.key)}</span>: {t("planShare", { pct: formatWeight(p.fraction * 100, locale, 0) })}
            {finite(p.price) && finite(qty) && (
              <> {t("planAt", { price: formatMoney(p.price, cur, locale), amount: formatMoney(p.price * qty * p.fraction, cur, locale) })}</>
            )}
          </li>
        ))}
      </ul>
      <p className="text-caption text-muted">{t("planNote")}</p>
    </section>
  );
}

/** Trailing-stop and break-even explanations, the server note and the plan's Why? (collapsed by the page). */
export function PlanRules({ r, uid }: { r: ExitLevelsResult; uid: string }) {
  const t = useTranslations("exit");
  const locale = useLocale();
  const plan = r.scale_out_plan;
  return (
    <div className="space-y-2 text-sm" data-testid="plan-rules">
      {r.trailing_stop && <p>{t("trailNote")}</p>}
      {plan && (
        <>
          <p>{t("planTrailRule", { scale: formatNumber(plan.trail_atr_scale, locale, 2) })}</p>
          <p>{t("planBreakeven", { atr: formatNumber(plan.breakeven_atr_multiple, locale, 2) })}</p>
          {!isPlanNote(plan.note) && <p className="text-muted" dir="auto"><bdi dir="auto">{plan.note}</bdi></p>}
          <WhyToggle id={`why-${uid}-plan`}><ExplanationView e={plan.explanation} currency={r.currency ?? "ILS"} /></WhyToggle>
        </>
      )}
    </div>
  );
}

/** Why these numbers: candidates, skipped sources, trailing memory and the overall explanation. */
export function LevelsWhy({ r }: { r: ExitLevelsResult }) {
  const t = useTranslations("exit");
  const locale = useLocale();
  const cur = r.currency ?? "ILS";
  return (
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
  );
}

/** Position size as one line with a details toggle (reason and rules). */
export function SizeAdvice({ r }: { r: ExitLevelsResult }) {
  const t = useTranslations("exit");
  const locale = useLocale();
  const [open, setOpen] = useState(false);
  const sg = r.size_guidance;
  if (!sg) return null;
  const hasDetails = !!sg.reason || sg.rules.length > 0;
  return (
    <section aria-label={t("sizeTitle")} className="space-y-1" data-testid="size-advice">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <h3 className="text-heading">{t("sizeTitle")}</h3>
        <p className={sg.needed ? "text-sm font-medium text-warn-fg" : "text-sm"} role={sg.needed ? "status" : undefined}>
          {sg.needed
            ? t("sizeSmaller", { suggested: formatNumber(sg.suggested_quantity, locale, 4), current: formatNumber(sg.current_quantity, locale, 4), pct: formatWeight(sg.keep_fraction * 100, locale, 0) })
            : t("sizeOk")}
        </p>
      </div>
      {hasDetails && (
        <>
          <button type="button" className="text-caption text-brand-text underline" aria-expanded={open} aria-controls={`size-details-${r.symbol}`} onClick={() => setOpen((v) => !v)}>
            {open ? t("sizeHide") : t("sizeDetails")}
          </button>
          {open && (
            <div id={`size-details-${r.symbol}`} className="space-y-1">
              {sg.reason && <p className="text-sm text-muted" dir="auto"><ServerText code={sg.reason_text} text={sg.reason} /></p>}
              {sg.rules.length > 0 && <ul className="list-disc ps-5 text-sm text-muted">{sg.rules.map((x, i) => <li key={i} dir="auto"><ServerText code={sg.rules_text?.[i]} text={x} /></li>)}</ul>}
            </div>
          )}
        </>
      )}
    </section>
  );
}

/** The visible part of the plan: price line, ladder, scale-out bar, position size, risk to the stop. */
export function LevelsMain({ r, entry }: { r: ExitLevelsResult; entry?: number | null }) {
  const t = useTranslations("exit");
  const h = useTranslations("holding");
  const locale = useLocale();
  const cur = r.currency ?? "ILS";
  const hz = horizonFromLabel(r.horizon_label) ?? r.horizon ?? null;
  const horizonText = hz ? h(`horizons.${hz}`) : (r.horizon_label ?? "");
  const rs = r.risk_to_stop;
  return (
    <div className="space-y-4">
      <div>
        <p className="font-medium">{t("headline", { horizon: horizonText })}</p>
        {finite(r.price) && (
          <p className="text-caption text-muted">
            {t("priceAsOf", { price: formatMoney(r.price, cur, locale), time: r.price_as_of ? `${formatTime(r.price_as_of, locale)}` : DASH })}
          </p>
        )}
      </div>
      {r.stop_fit && r.stop_fit !== "ok" && <p className="chip-warn" role="status">{t(`fit.${r.stop_fit}`)}</p>}
      <LevelLadder r={r} entry={entry} currency={cur} />
      {r.scale_out_plan && <ScalePlan plan={r.scale_out_plan} r={r} />}
      <SizeAdvice r={r} />
      {rs && (
        <p className="text-sm" data-testid="risk-to-stop">
          <span className="font-semibold">{t("riskTitle")}: </span>
          <TwoMoney ils={-Math.abs(rs.ils)} usd={-Math.abs(rs.usd)} signed />
          <span className="block text-caption text-muted">
            {t("riskOfPosition", { pct: formatPct(rs.pct_of_position, locale) })}
            {finite(rs.pct_of_portfolio) && ` · ${t("riskOfPortfolio", { pct: formatPct(rs.pct_of_portfolio, locale) })}`}
          </span>
        </p>
      )}
    </div>
  );
}

/** Collapsed explanations of the levels, as MoreSections items. */
export function levelsMoreItems(r: ExitLevelsResult, uid: string, titles: { explain: string; reasons: string; why: string }): MoreItem[] {
  return [
    { id: `${uid}-explain`, title: titles.explain, body: <PlanRules r={r} uid={uid} /> },
    { id: `${uid}-reasons`, title: titles.reasons, body: <LevelReasons r={r} uid={uid} /> },
    { id: `${uid}-why`, title: titles.why, body: <LevelsWhy r={r} /> },
  ];
}

export function Levels({ r, uid, entry }: { r: ExitLevelsResult; uid: string; entry?: number | null }) {
  const h = useTranslations("holding");
  const t = useTranslations("exit");
  return (
    <div className="space-y-4">
      <LevelsMain r={r} entry={entry} />
      <MoreSections items={levelsMoreItems(r, uid, { explain: h("more.explain"), reasons: t("levelReasons"), why: h("more.whyNumbers") })} />
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
 * State of the exit-levels flow for one holding: what-if period, "preview only", saving a period.
 * `ui` is the non-levels body (error, loading, needs_horizon); null when levels or no_levels data is ready.
 */
export function useExitPanel(holdingId: number, portfolioId: number, onHorizonSaved?: () => void | Promise<unknown>) {
  const t = useTranslations("exit");
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

  let ui: React.ReactNode = null;
  if (error) {
    ui = (
      <div className="space-y-2">
        <p role="alert" className="text-loss">{t("error")}</p>
        <button type="button" className="btn-secondary" onClick={() => mutate()}>{t("retry")}</button>
      </div>
    );
  } else if (!data) {
    ui = <p role="status" className="text-muted">{t("loading")}</p>;
  } else if (data.status === "needs_horizon") {
    ui = (
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
  }
  return { data: error ? undefined : data, ui, whatIf, setWhatIf };
}

/** Shown while a what-if period is previewed. */
export function WhatIfBanner({ whatIf, setWhatIf }: { whatIf: Horizon | null; setWhatIf: (h: Horizon | null) => void }) {
  const t = useTranslations("exit");
  const h = useTranslations("holding");
  if (!whatIf) return null;
  return (
    <p className="flex flex-wrap items-center gap-2 rounded-xl bg-brand-soft p-3 text-sm text-brand-text" role="status">
      {t("whatIfActive", { value: h(`horizons.${whatIf}`) })}
      <button type="button" className="btn-secondary" onClick={() => setWhatIf(null)}>{t("whatIfClear")}</button>
    </p>
  );
}

/** "Try another period (preview, not saved)": a banner while previewing and the picker. */
export function WhatIf({ whatIf, setWhatIf, banner = true }: { whatIf: Horizon | null; setWhatIf: (h: Horizon | null) => void; banner?: boolean }) {
  const t = useTranslations("exit");
  return (
    <div className="space-y-2">
      {banner && <WhatIfBanner whatIf={whatIf} setWhatIf={setWhatIf} />}
      <p className="text-sm text-muted">{t("whatIf")}</p>
      <HorizonPicker value={whatIf} onPick={setWhatIf} label={t("whatIf")} />
    </div>
  );
}

/**
 * Exit levels of one holding as a standalone card (the holding page composes the same pieces itself).
 * needs_horizon -> ask (no default, saves via the holding PATCH unless "preview only");
 * no_levels -> the reason and no numbers; levels -> ladder, scale-out bar, size advice and collapsed explanations.
 */
export function ExitLevelsPanel({ holdingId, portfolioId, onHorizonSaved }: {
  holdingId: number; portfolioId: number; onHorizonSaved?: () => void | Promise<unknown>;
}) {
  const t = useTranslations("exit");
  const h = useTranslations("holding");
  const { data, ui, whatIf, setWhatIf } = useExitPanel(holdingId, portfolioId, onHorizonSaved);
  const body = ui ?? (data && (
    <div className="space-y-4">
      {data.status === "no_levels" ? <NoLevels r={data} /> : <Levels r={data} uid={`h${holdingId}`} />}
      {data.reason_code !== "fund_no_levels" && <WhatIf whatIf={whatIf} setWhatIf={setWhatIf} />}
      {data.disclaimer && <p className="text-xs text-muted">{data.disclaimer}</p>}
    </div>
  ));
  return (
    <section className="card space-y-3" aria-label={h("exitTitle")}>
      <h2 className="text-lg font-bold">{h("exitTitle")}</h2>
      <p className="text-sm text-muted">{t("intro")}</p>
      {body}
    </section>
  );
}
