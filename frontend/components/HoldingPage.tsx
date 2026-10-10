"use client";
import { useMemo, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { useSWRConfig } from "swr";
import { api, type Horizon } from "@/lib/api";
import { DASH, formatMoney, formatNumber, formatWeight } from "@/lib/format";
import { useAlerts, useCandles, useHoldings, useScorecard } from "@/lib/hooks";
import { Link, useRouter } from "@/i18n/navigation";
import { AppShell } from "./AppShell";
import { AnalystView } from "./AnalystView";
import { ExplanationView } from "./ExplanationView";
import { HoldingActions } from "./HoldingActions";
import { LevelsMain, NoLevels, WhatIf, WhatIfBanner, HorizonPicker, levelsMoreItems, useExitPanel } from "./ExitLevelsPanel";
import { MoreSections, type MoreItem } from "./MoreSections";
import { PnlText } from "./Pnl";
import { ScoreBars } from "./ScoreBars";
import { LevelLegend, PriceChart, type Candle, type PriceLevel } from "./charts";

const finite = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);

function Body({ hid }: { hid: number }) {
  const t = useTranslations("holding");
  const x = useTranslations("exit");
  const hs = useTranslations("holdings");
  const c = useTranslations("common");
  const locale = useLocale();
  const sc = useScorecard(hid);
  const { mutate: globalMutate } = useSWRConfig();
  const alerts = useAlerts();
  const router = useRouter();
  const own = useHoldings(sc.data?.portfolio_id ?? null, undefined);
  const ex = useExitPanel(hid, sc.data?.portfolio_id ?? 0, () => sc.mutate());
  const isFund = !!sc.data && sc.data.symbol.startsWith("GEMEL-");
  const candles = useCandles(sc.data && !isFund ? sc.data.symbol : null, 180);
  const [why, setWhy] = useState(false);
  const [op, setOp] = useState<"above" | "below">("above");
  const [price, setPrice] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(false);

  const d = sc.data;
  const held = d ? (own.data ?? []).find((h) => h.id === d.holding_id) : undefined;
  const exit = ex.data;
  const cur = d ? (d.symbol.endsWith(".TA") || d.symbol.startsWith("GEMEL-") ? "ILS" : "USD") : "USD";
  const bars = (candles.data?.candles ?? []) as Candle[];

  const levels = useMemo<PriceLevel[]>(() => {
    if (!exit || exit.status !== "levels") return [];
    const out: PriceLevel[] = [];
    if (exit.stop) out.push({ price: exit.stop.price, kind: "stop" });
    if (exit.trailing_stop) out.push({ price: exit.trailing_stop.price, kind: "stop", title: x("kind.trailing_stop") });
    (exit.take_profits ?? []).forEach((tp, i) => out.push({ price: tp.price, kind: "target", title: `TP${i + 1}` }));
    if (finite(exit.price)) out.push({ price: exit.price, kind: "price" });
    if (held && finite(held.avg_cost) && (held.cost_currency ?? held.currency) === held.currency) out.push({ price: held.avg_cost, kind: "entry" });
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exit, held?.avg_cost, held?.cost_currency, held?.currency, locale]);
  const chartLabels = useMemo(
    () => ({ support: "", resistance: "", aria: t("chart.aria"), entry: t("chart.entry"), stop: t("chart.stop"), target: t("chart.target"), price: t("chart.price") }),
    [t],
  );

  if (sc.error) return <p role="alert">{t("notFound")}</p>;
  if (!d) return <p role="status">{c("loading")}</p>;
  const name = locale === "he" ? d.name_he : d.name_en;
  const mine = (alerts.data ?? []).filter((a) => a.symbol === d.symbol);
  const kinds = [...new Set(levels.map((l) => l.kind))];
  const legendLabels = { stop: t("chart.stop"), target: t("chart.target"), entry: t("chart.entry"), price: t("chart.price") };

  const setHorizon = async (h: Horizon) => {
    await api.patchHolding(d.portfolio_id, d.holding_id, { horizon: h });
    await Promise.all([sc.mutate(), globalMutate((k) => Array.isArray(k) && k[0] === "exit-levels")]);
  };
  const addAlert = async (e: React.FormEvent) => {
    e.preventDefault();
    const n = Number(price);
    if (!Number.isFinite(n) || n <= 0) return;
    setBusy(true); setErr(false);
    try { await api.createAlert({ symbol: d.symbol, op, price: n }); setPrice(""); await alerts.mutate(); }
    catch { setErr(true); }
    finally { setBusy(false); }
  };

  const hasPrice = !!held && finite(held.price) && held.price > 0;
  const items: MoreItem[] = [
    {
      id: "alerts", title: t("alertsTitle"),
      body: (
        <div className="space-y-3">
          {mine.length === 0 ? <p className="text-sm text-muted">{t("alertsNone")}</p> : (
            <ul className="space-y-2">
              {mine.map((a) => (
                <li key={a.id} className="flex items-center justify-between rounded-xl border border-line p-2">
                  <span dir="auto">{t("alertSummary", { op: t(a.op), price: formatMoney(a.price, cur, locale) })}</span>
                  <button type="button" className="btn-secondary" onClick={async () => { await api.deleteAlert(a.id); await alerts.mutate(); }}>{t("alertDelete")}</button>
                </li>
              ))}
            </ul>
          )}
          <form onSubmit={addAlert} className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
            <div>
              <label htmlFor="alert-op" className="label">{t("alertOp")}</label>
              <select id="alert-op" className="input" value={op} onChange={(e) => setOp(e.target.value as "above" | "below")}>
                <option value="above">{t("above")}</option>
                <option value="below">{t("below")}</option>
              </select>
            </div>
            <div>
              <label htmlFor="alert-price" className="label">{t("alertPrice")}</label>
              <input id="alert-price" className="input" type="number" inputMode="decimal" step="any" min="0" required value={price} onChange={(e) => setPrice(e.target.value)} dir="ltr" />
            </div>
            <button type="submit" className="btn-primary" disabled={busy}>{t("alertAdd")}</button>
          </form>
          {err && <p role="alert" className="text-sm text-loss">{c("errorLoad")}</p>}
        </div>
      ),
    },
  ];
  if (exit && exit.status !== "needs_horizon" && exit.reason_code !== "fund_no_levels") {
    items.push({ id: "preview", title: t("more.preview"), body: <WhatIf whatIf={ex.whatIf} setWhatIf={ex.setWhatIf} banner={false} /> });
  }
  if (exit && exit.status === "levels") {
    items.push(...levelsMoreItems(exit, `h${hid}`, { explain: t("more.explain"), reasons: x("levelReasons"), why: t("more.whyNumbers") }));
  }

  return (
    <>
      <section className="card space-y-3" aria-label={name}>
        <Link href="/" className="text-sm text-brand-text hover:underline">← {c("back")}</Link>
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h1 className="text-2xl font-bold"><bdi dir="auto">{name}</bdi></h1>
            <p className="text-sm text-muted" dir="ltr">{d.symbol}</p>
          </div>
          {held && <HoldingActions h={held} portfolioId={d.portfolio_id} name={name} menu onRemoved={() => router.push("/")} />}
        </div>
        {held && !held.fund && (
          <div className="flex flex-wrap items-start justify-between gap-2" data-testid="holding-position">
            <div className="text-start">
              <p className="text-xl font-semibold tabular-nums" dir="ltr" data-testid="position-value">{hasPrice && finite(held.value_native) ? formatMoney(held.value_native, held.currency, locale) : DASH}</p>
              <p className="text-caption text-muted tabular-nums" dir="ltr" data-testid="qty-price">
                {hs("qtyTimesPrice", { qty: formatNumber(held.quantity, locale, 4), price: hasPrice ? formatMoney(held.price, held.currency, locale) : DASH })}
              </p>
            </div>
            <div className="text-end">
              {held.pnl && <p className="text-sm"><PnlText value={held.pnl_native ?? (held.currency === "ILS" ? held.pnl.ils : undefined)} pct={held.pnl.pct} currency={held.currency} locale={locale} className="font-semibold" /></p>}
              {held.check_numbers && <span className="chip-warn" title={hs("checkNumbersHint")} data-testid="check-numbers">{hs("checkNumbers")}</span>}
            </div>
          </div>
        )}
        {bars.length > 0 && (
          <div className="space-y-1">
            <PriceChart bars={bars} levels={levels} marks={[]} labels={chartLabels} height={220} />
            {kinds.length > 0 && <LevelLegend kinds={kinds} labels={legendLabels} />}
            {kinds.length > 0 && <p className="text-caption text-muted">{t("chart.note")}</p>}
          </div>
        )}
      </section>

      <section className="card space-y-3" aria-label={t("scoreCard")}>
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-lg font-bold">{t("scoreCard")}</h2>
          <span className="chip bg-warn-bg text-warn-fg">{t("notValidatedLabel")}</span>
        </div>
        <p className="text-caption text-muted">{t("notValidated")}</p>
        {!d.available && (
          <p role="status" className="rounded-xl bg-surface-2 p-3 font-medium">
            <span className="text-2xl font-bold" aria-hidden="true">{DASH} </span>{t("noData")}
          </p>
        )}
        <ScoreBars
          total={d.available ? d.total : null}
          confidenceText={d.available ? `${t("total")} · ${t("confidence", { pct: formatWeight(d.confidence * 100, locale, 0) })}` : undefined}
          rows={d.signals.map((s) => ({ name: s.name, label: t.has(`signal.${s.name}`) ? t(`signal.${s.name}`) : s.name, score: s.score, noData: s.confidence === 0 }))}
        />
        <AnalystView symbol={d.symbol} price={exit?.price ?? held?.price ?? null} currency={cur} />
        <button type="button" className="btn-secondary" aria-expanded={why} aria-controls="why-panel" onClick={() => setWhy((v) => !v)}>
          {why ? t("whyHide") : t("why")}
        </button>
        {why && (
          <div id="why-panel" className="space-y-4 rounded-xl bg-surface-2 p-3">
            <ExplanationView e={d.explanation} currency={cur} />
            {d.signals.map((s) => (
              <div key={s.name} className="border-t border-line pt-3">
                <h3 className="mb-1 font-semibold">{t.has(`signal.${s.name}`) ? t(`signal.${s.name}`) : s.name}</h3>
                <p className="text-caption text-muted">
                  {t("confidence", { pct: formatWeight(s.confidence * 100, locale, 0) })} · {t("weight", { pct: formatWeight(s.weight, locale, 0) })}
                  {s.nominal_weight != null && Math.abs(s.nominal_weight - s.weight) >= 0.05 && ` · ${t("nominalWeight", { pct: formatWeight(s.nominal_weight, locale, 0) })}`}
                </p>
                <ExplanationView e={s.explanation} reasons={s.reasons} asOf={s.data_as_of} currency={cur} showContributions={false} />
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="card space-y-3" aria-label={t("exitTitle")}>
        <h2 className="text-lg font-bold">{t("exitTitle")}</h2>
        <WhatIfBanner whatIf={ex.whatIf} setWhatIf={ex.setWhatIf} />
        {ex.ui ?? (exit && (exit.status === "no_levels" ? <NoLevels r={exit} /> : (
          <LevelsMain r={exit} entry={held && finite(held.avg_cost) && (held.cost_currency ?? held.currency) === held.currency ? held.avg_cost : null} />
        )))}
      </section>

      <section className="card space-y-3" aria-label={t("horizonTitle")}>
        <h2 className="text-lg font-bold">{t("horizonTitle")}</h2>
        {d.horizon === null ? (
          <p className="rounded-xl bg-warn-bg p-3 font-medium text-warn-fg" role="status">{t("horizonPrompt")}</p>
        ) : (
          <p className="text-sm">{t("horizonCurrent", { value: t(`horizons.${d.horizon}`) })}</p>
        )}
        <HorizonPicker value={d.horizon ?? null} onPick={setHorizon} label={t("horizonTitle")} />
      </section>

      <MoreSections items={items} label={t("more.explain")} />
      {(exit?.disclaimer || d.disclaimer) && <p className="text-xs text-muted" data-testid="holding-disclaimer">{d.disclaimer ?? exit?.disclaimer}</p>}
    </>
  );
}

export function HoldingPage({ id }: { id: number }) {
  return <AppShell><Body hid={id} /></AppShell>;
}
