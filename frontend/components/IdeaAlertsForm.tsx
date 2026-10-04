"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";
import useSWR from "swr";
import { api, toPresets, type IdeaAlertsFilter, type Horizon, type MarketKey, type Settings } from "@/lib/api";
import { HHMM, SaveStatus, SettingsCard, useSettingsSave } from "./SettingsControls";

const HORIZONS: Horizon[] = ["1w", "1m", "3m", "6m", "1y"];
const MARKETS: MarketKey[] = ["US", "TASE", "CRYPTO"];
const TYPES = ["stock", "etf", "crypto"] as const;
type QuietChoice = "none" | "use" | null;

const toggle = <T,>(list: T[], v: T): T[] => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

/**
 * The idea-alerts filter. NOTHING is prefilled: a saved filter fills the form, otherwise every field starts empty.
 * Save stays disabled until every required field is valid. Clear sends an explicit null.
 */
export function IdeaAlertsForm({ s }: { s: Settings }) {
  const t = useTranslations("prefs.ideas");
  const h = useTranslations("suggest");
  const tn = useTranslations("prefs.notifications");
  const st = useTranslations("settings");
  const presetLabel = (n: string) => (st.has(`presets.${n}`) ? st(`presets.${n}`) : n);
  const saved = s.idea_alerts.state === "set" ? s.idea_alerts.filter ?? null : null;
  const { save, state, status } = useSettingsSave();
  const { data: gate } = useSWR("launch-gate", () => api.launchGate(), { shouldRetryOnError: false });
  const { data: presetsRaw } = useSWR("presets", () => api.riskPresets());
  const presets = presetsRaw ? toPresets(presetsRaw) : [];

  const [conf, setConf] = useState(saved ? String(Math.round(saved.min_confidence * 100)) : "");
  const [horizon, setHorizon] = useState<Horizon | null>(saved?.horizon ?? null);
  const [risk, setRisk] = useState<string>(saved?.risk_preset ?? "");
  const [markets, setMarkets] = useState<MarketKey[]>(saved?.markets ?? []);
  const [types, setTypes] = useState<string[]>(saved?.asset_types ?? []);
  const [perDay, setPerDay] = useState(saved ? String(saved.max_per_day) : "");
  const [quiet, setQuiet] = useState<QuietChoice>(saved ? (saved.quiet_hours ? "use" : "none") : null);
  const [qs, setQs] = useState(saved?.quiet_hours?.start ?? "");
  const [qe, setQe] = useState(saved?.quiet_hours?.end ?? "");
  const [cleared, setCleared] = useState(false);

  const confN = conf.trim() === "" ? NaN : Number(conf);
  const perN = perDay.trim() === "" ? NaN : Number(perDay);
  const quietOk = quiet === "none" || (quiet === "use" && HHMM.test(qs) && HHMM.test(qe) && qs !== qe);
  const valid = Number.isFinite(confN) && confN >= 0 && confN <= 100 && horizon !== null && risk !== "" && markets.length > 0 && types.length > 0
    && Number.isInteger(perN) && perN >= 1 && perN <= 20 && quietOk;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!valid || horizon === null) return;
    setCleared(false);
    const filter: IdeaAlertsFilter = {
      min_confidence: confN / 100, horizon, risk_preset: risk as IdeaAlertsFilter["risk_preset"], markets,
      asset_types: types as IdeaAlertsFilter["asset_types"], max_per_day: perN, quiet_hours: quiet === "use" ? { start: qs, end: qe } : null,
    };
    await save({ idea_alerts: filter });
  };
  const clear = async () => {
    if (await save({ idea_alerts: null })) {
      setConf(""); setHorizon(null); setRisk(""); setMarkets([]); setTypes([]); setPerDay(""); setQuiet(null); setQs(""); setQe(""); setCleared(true);
    }
  };

  return (
    <>
      <SettingsCard title={t("title")} intro={t("intro")}>
        <p className={saved ? "chip-brand w-fit" : "chip-warn w-fit"} role="status" data-testid="idea-state">{saved ? t("stateSet") : t("stateNotSet")}</p>
        {saved && <p className="text-sm text-muted">{t("summary", { conf: `${Math.round(saved.min_confidence * 100)}%`, horizon: h(`horizons.${saved.horizon}`), risk: presetLabel(saved.risk_preset), max: saved.max_per_day })}</p>}
        {gate && !gate.open && <p className="rounded-xl bg-warn-bg p-3 text-sm text-warn-fg" role="note">{t("gateClosed")}</p>}
      </SettingsCard>
      <form onSubmit={submit} className="card space-y-4" aria-label={t("title")}>
        <div>
          <label htmlFor="ia-conf" className="label">{t("minConfidence")}</label>
          <input id="ia-conf" className="input" inputMode="numeric" dir="ltr" value={conf} onChange={(e) => setConf(e.target.value)} aria-describedby="ia-conf-h" />
          <p id="ia-conf-h" className="text-caption text-muted">{t("minConfidenceHelp")}</p>
        </div>
        <div>
          <p className="label" id="ia-h">{t("horizon")}</p>
          <div role="radiogroup" aria-labelledby="ia-h" className="flex flex-wrap gap-2">
            {HORIZONS.map((x) => <button key={x} type="button" role="radio" aria-checked={horizon === x} className={horizon === x ? "btn-primary" : "btn-secondary"} onClick={() => setHorizon(x)}>{h(`horizons.${x}`)}</button>)}
          </div>
        </div>
        <div>
          <label htmlFor="ia-risk" className="label">{t("risk")}</label>
          <select id="ia-risk" className="input" value={risk} onChange={(e) => setRisk(e.target.value)}>
            <option value="">{t("riskPlaceholder")}</option>
            {presets.map((p) => <option key={p.name} value={p.name}>{presetLabel(p.name)}</option>)}
          </select>
        </div>
        <fieldset className="space-y-1">
          <legend className="label">{t("markets")}</legend>
          <div className="flex flex-wrap gap-4">
            {MARKETS.map((m) => <label key={m} className="flex min-h-11 items-center gap-2"><input type="checkbox" className="h-5 w-5" checked={markets.includes(m)} onChange={() => setMarkets(toggle(markets, m))} />{t(`marketNames.${m}`)}</label>)}
          </div>
        </fieldset>
        <fieldset className="space-y-1">
          <legend className="label">{t("assetTypes")}</legend>
          <div className="flex flex-wrap gap-4">
            {TYPES.map((m) => <label key={m} className="flex min-h-11 items-center gap-2"><input type="checkbox" className="h-5 w-5" checked={types.includes(m)} onChange={() => setTypes(toggle(types, m))} />{t(`types.${m}`)}</label>)}
          </div>
        </fieldset>
        <div>
          <label htmlFor="ia-max" className="label">{t("maxPerDay")}</label>
          <input id="ia-max" className="input" inputMode="numeric" dir="ltr" value={perDay} onChange={(e) => setPerDay(e.target.value)} aria-describedby="ia-max-h" />
          <p id="ia-max-h" className="text-caption text-muted">{t("maxPerDayHelp")}</p>
        </div>
        <fieldset className="space-y-2">
          <legend className="label">{t("quiet")}</legend>
          <div className="flex flex-wrap gap-2" role="radiogroup" aria-label={t("quiet")}>
            <button type="button" role="radio" aria-checked={quiet === "none"} className={quiet === "none" ? "btn-primary" : "btn-secondary"} onClick={() => setQuiet("none")}>{t("quietNone")}</button>
            <button type="button" role="radio" aria-checked={quiet === "use"} className={quiet === "use" ? "btn-primary" : "btn-secondary"} onClick={() => setQuiet("use")}>{t("quietUse")}</button>
          </div>
          {quiet === "use" && (
            <div className="grid grid-cols-2 gap-3">
              <div><label htmlFor="ia-qs" className="label">{tn("quietFrom")}</label><input id="ia-qs" type="time" dir="ltr" className="input" value={qs} onChange={(e) => setQs(e.target.value)} /></div>
              <div><label htmlFor="ia-qe" className="label">{tn("quietTo")}</label><input id="ia-qe" type="time" dir="ltr" className="input" value={qe} onChange={(e) => setQe(e.target.value)} /></div>
            </div>
          )}
        </fieldset>
        {!valid && <p className="text-sm text-muted" role="status">{t("pickAll")}</p>}
        <div className="flex flex-wrap items-center gap-2">
          <button type="submit" className="btn-primary" disabled={!valid || state === "saving"}>{t("save")}</button>
          <button type="button" className="btn-secondary" disabled={!saved || state === "saving"} onClick={() => void clear()}>{t("clear")}</button>
          <SaveStatus state={state} status={status} />
        </div>
        {cleared && state === "saved" && <p className="text-sm text-muted" role="status">{t("cleared")}</p>}
      </form>
    </>
  );
}
