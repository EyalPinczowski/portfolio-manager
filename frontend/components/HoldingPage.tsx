"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { useSWRConfig } from "swr";
import { api, type Horizon } from "@/lib/api";
import { DASH, formatMoney, formatWeight } from "@/lib/format";
import { useAlerts, useScorecard } from "@/lib/hooks";
import { Link } from "@/i18n/navigation";
import { AppShell } from "./AppShell";
import { ExitLevelsPanel } from "./ExitLevelsPanel";
import { ExplanationView } from "./ExplanationView";

const HORIZONS: Horizon[] = ["1w", "1m", "3m", "6m", "1y"];

function Body({ hid }: { hid: number }) {
  const t = useTranslations("holding");
  const c = useTranslations("common");
  const locale = useLocale();
  const sc = useScorecard(hid);
  const { mutate: globalMutate } = useSWRConfig();
  const alerts = useAlerts();
  const [open, setOpen] = useState(false);
  const [op, setOp] = useState<"above" | "below">("above");
  const [price, setPrice] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(false);

  if (sc.error) return <p role="alert">{t("notFound")}</p>;
  const d = sc.data;
  if (!d) return <p role="status">{c("loading")}</p>;
  const name = locale === "he" ? d.name_he : d.name_en;
  const cur = d.symbol.endsWith(".TA") || d.symbol.startsWith("GEMEL-") ? "ILS" : "USD";
  const mine = (alerts.data ?? []).filter((a) => a.symbol === d.symbol);

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

  return (
    <>
      <div>
        <Link href="/" className="text-sm text-brand-text hover:underline">← {c("back")}</Link>
        <h1 className="text-2xl font-bold">{name}</h1>
        <p className="text-sm text-muted" dir="ltr">{d.symbol}</p>
      </div>

      <section className="card space-y-3" aria-label={t("scoreCard")}>
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-lg font-bold">{t("scoreCard")}</h2>
          <span className="chip bg-warn-bg text-warn-fg">{t("notValidatedLabel")}</span>
        </div>
        <p className="text-sm text-muted">{t("notValidated")}</p>
        {d.disclaimer && <p className="text-xs text-muted">{d.disclaimer}</p>}
        {d.available ? (
          <div className="flex items-baseline gap-3">
            <span className="text-4xl font-bold tabular-nums" dir="ltr">{d.total > 0 ? "+" : ""}{d.total}</span>
            <span className="text-sm text-muted">{t("total")} · {t("confidence", { pct: formatWeight(d.confidence * 100, locale, 0) })}</span>
          </div>
        ) : (
          <p role="status" className="rounded-xl bg-surface-2 p-3 font-medium ">
            <span className="text-2xl font-bold" aria-hidden="true">{DASH} </span>{t("noData")}
          </p>
        )}
        <ul className="space-y-2">
          {d.signals.map((s) => (
            <li key={s.name} className="rounded-xl border border-line p-3 ">
              <div className="flex items-center justify-between">
                <span className="font-semibold">{t.has(`signal.${s.name}`) ? t(`signal.${s.name}`) : s.name}</span>
                {s.confidence === 0
                  ? <span className="text-sm text-muted">{t("signalNoData")}</span>
                  : <span className="tabular-nums" dir="ltr">{s.score > 0 ? "+" : ""}{s.score}</span>}
              </div>
              <p className="text-xs text-muted">
                {t("confidence", { pct: formatWeight(s.confidence * 100, locale, 0) })} · {t("weight", { pct: formatWeight(s.weight, locale, 0) })}
                {s.nominal_weight != null && Math.abs(s.nominal_weight - s.weight) >= 0.05 && ` · ${t("nominalWeight", { pct: formatWeight(s.nominal_weight, locale, 0) })}`}
              </p>
            </li>
          ))}
        </ul>
        <button
          type="button"
          className="btn-secondary"
          aria-expanded={open}
          aria-controls="why-panel"
          onClick={() => setOpen((v) => !v)}
        >
          {open ? t("whyHide") : t("why")}
        </button>
        {open && (
          <div id="why-panel" className="space-y-4 rounded-xl bg-surface-2 p-3 ">
            <ExplanationView e={d.explanation} currency={cur} />
            {d.signals.map((s) => (
              <div key={s.name} className="border-t border-line pt-3 ">
                <h3 className="mb-1 font-semibold">{t.has(`signal.${s.name}`) ? t(`signal.${s.name}`) : s.name}</h3>
                <ExplanationView e={s.explanation} reasons={s.reasons} asOf={s.data_as_of} currency={cur} showContributions={false} />
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="card space-y-3" aria-label={t("horizonTitle")}>
        <h2 className="text-lg font-bold">{t("horizonTitle")}</h2>
        {d.horizon === null ? (
          <p className="rounded-xl bg-warn-bg p-3 font-medium text-warn-fg" role="status">{t("horizonPrompt")}</p>
        ) : (
          <p className="text-sm">{t("horizonCurrent", { value: t(`horizons.${d.horizon}`) })}</p>
        )}
        <div role="radiogroup" aria-label={t("horizonTitle")} className="flex flex-wrap gap-2">
          {HORIZONS.map((h) => (
            <button
              key={h}
              type="button"
              role="radio"
              aria-checked={d.horizon === h}
              onClick={() => setHorizon(h)}
              className={d.horizon === h ? "btn-primary" : "btn-secondary"}
            >
              {t(`horizons.${h}`)}
            </button>
          ))}
        </div>
      </section>

      <section className="card space-y-3" aria-label={t("alertsTitle")}>
        <h2 className="text-lg font-bold">{t("alertsTitle")}</h2>
        {mine.length === 0 ? <p className="text-sm text-muted">{t("alertsNone")}</p> : (
          <ul className="space-y-2">
            {mine.map((a) => (
              <li key={a.id} className="flex items-center justify-between rounded-xl border border-line p-2 ">
                <span dir="auto">{t("alertSummary", { op: t(a.op), price: formatMoney(a.price, d.symbol.endsWith(".TA") ? "ILS" : "USD", locale) })}</span>
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
      </section>

      <ExitLevelsPanel holdingId={hid} portfolioId={d.portfolio_id} onHorizonSaved={() => sc.mutate()} />
    </>
  );
}

export function HoldingPage({ id }: { id: number }) {
  return <AppShell><Body hid={id} /></AppShell>;
}
