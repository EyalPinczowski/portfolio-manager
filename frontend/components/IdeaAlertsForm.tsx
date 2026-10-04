"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";
import useSWR from "swr";
import { api, toPresets, type IdeaAlertsFilter, type Horizon, type MarketKey, type Settings } from "@/lib/api";
import { HHMM, SaveStatus, useSettingsSave } from "./SettingsControls";
import { CheckList, FIELD, FieldRow, GroupItem, MultiCheckList, SettingsGroup } from "./SettingsUI";

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
      <SettingsGroup label={t("title")} footer={t("intro")}>
        <GroupItem>
          <p className={saved ? "chip-brand w-fit" : "chip-warn w-fit"} role="status" data-testid="idea-state">{saved ? t("stateSet") : t("stateNotSet")}</p>
          {saved && <p className="mt-2 text-sm text-muted">{t("summary", { conf: `${Math.round(saved.min_confidence * 100)}%`, horizon: h(`horizons.${saved.horizon}`), risk: presetLabel(saved.risk_preset), max: saved.max_per_day })}</p>}
          {gate && !gate.open && <p className="mt-2 rounded-xl bg-warn-bg p-3 text-sm text-warn-fg" role="note">{t("gateClosed")}</p>}
        </GroupItem>
      </SettingsGroup>
      <form onSubmit={submit} className="space-y-5" aria-label={t("title")}>
        <SettingsGroup footer={t("minConfidenceHelp")}>
          <FieldRow label={t("minConfidence")} htmlFor="ia-conf">
            <input id="ia-conf" className={`${FIELD} w-24`} inputMode="numeric" dir="ltr" placeholder="%" value={conf} onChange={(e) => setConf(e.target.value)} />
          </FieldRow>
        </SettingsGroup>
        <CheckList
          title={t("horizon")} label={t("horizon")} value={horizon} onPick={setHorizon}
          options={HORIZONS.map((x) => ({ value: x, label: h(`horizons.${x}`) }))}
        />
        <SettingsGroup>
          <FieldRow label={t("risk")} htmlFor="ia-risk">
            <select id="ia-risk" className={FIELD} value={risk} onChange={(e) => setRisk(e.target.value)}>
              <option value="">{t("riskPlaceholder")}</option>
              {presets.map((p) => <option key={p.name} value={p.name}>{presetLabel(p.name)}</option>)}
            </select>
          </FieldRow>
        </SettingsGroup>
        <MultiCheckList title={t("markets")} label={t("markets")} values={markets} onToggle={(m) => setMarkets(toggle(markets, m))} options={MARKETS.map((m) => ({ value: m, label: t(`marketNames.${m}`) }))} />
        <MultiCheckList title={t("assetTypes")} label={t("assetTypes")} values={types} onToggle={(m) => setTypes(toggle(types, m))} options={TYPES.map((m) => ({ value: m, label: t(`types.${m}`) }))} />
        <SettingsGroup footer={t("maxPerDayHelp")}>
          <FieldRow label={t("maxPerDay")} htmlFor="ia-max">
            <input id="ia-max" className={`${FIELD} w-24`} inputMode="numeric" dir="ltr" value={perDay} onChange={(e) => setPerDay(e.target.value)} />
          </FieldRow>
        </SettingsGroup>
        <CheckList
          title={t("quiet")} label={t("quiet")} value={quiet} onPick={setQuiet}
          options={[{ value: "none", label: t("quietNone") }, { value: "use", label: t("quietUse") }]}
        />
        {quiet === "use" && (
          <SettingsGroup label={t("quiet")}>
            <FieldRow label={tn("quietFrom")} htmlFor="ia-qs"><input id="ia-qs" type="time" dir="ltr" className={FIELD} value={qs} onChange={(e) => setQs(e.target.value)} /></FieldRow>
            <FieldRow label={tn("quietTo")} htmlFor="ia-qe"><input id="ia-qe" type="time" dir="ltr" className={FIELD} value={qe} onChange={(e) => setQe(e.target.value)} /></FieldRow>
          </SettingsGroup>
        )}
        {!valid && <p className="px-4 text-sm text-muted" role="status">{t("pickAll")}</p>}
        <div className="flex flex-wrap items-center gap-2 px-1">
          <button type="submit" className="btn-primary" disabled={!valid || state === "saving"}>{t("save")}</button>
          <button type="button" className="btn-secondary" disabled={!saved || state === "saving"} onClick={() => void clear()}>{t("clear")}</button>
          <SaveStatus state={state} status={status} />
        </div>
        {cleared && state === "saved" && <p className="px-4 text-sm text-muted" role="status">{t("cleared")}</p>}
      </form>
    </>
  );
}
