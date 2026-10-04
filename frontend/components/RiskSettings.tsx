"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";
import useSWR from "swr";
import { api, toPresets, type RiskFilter } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { usePortfolios } from "@/lib/hooks";
import { PortfolioSwitcher } from "./PortfolioSwitcher";
import { FIELD, FieldRow, GroupItem, SettingsGroup } from "./SettingsUI";

const NUM_FIELDS = [
  "max_position_pct", "max_sector_pct", "max_country_pct", "max_loss_per_position_pct",
  "max_portfolio_risk_per_trade_pct", "max_total_portfolio_risk_pct", "min_rr", "drawdown_defensive_pct",
] as const;

const FALLBACK: RiskFilter = {
  preset: "balanced_aggressive", max_position_pct: 12, max_sector_pct: 30, max_country_pct: 60, max_loss_per_position_pct: 12,
  max_portfolio_risk_per_trade_pct: 1.5, max_total_portfolio_risk_pct: 12, min_rr: 2, stop_type: "both", drawdown_defensive_pct: 15,
};

/** The risk limits of one portfolio: a preset, then every number, then the stop type. Saved with the button (a stop is never tightened silently). */
export function RiskScreen() {
  const t = useTranslations("settings");
  const c = useTranslations("common");
  const { data: portfolios, mutate: mutatePortfolios } = usePortfolios();
  const { data: presetsRaw } = useSWR("presets", () => api.riskPresets());
  const presets = presetsRaw ? toPresets(presetsRaw) : [];
  const [pid, setPid] = useState<number | null>(null);
  const id = pid ?? portfolios?.[0]?.id ?? null;
  const current = portfolios?.find((p) => p.id === id);
  const [edits, setEdits] = useState<RiskFilter | null>(null);
  const form = edits ?? current?.risk_filter ?? FALLBACK;
  const setForm = (f: RiskFilter | ((p: RiskFilter) => RiskFilter)) => setEdits(typeof f === "function" ? f(form) : f);
  const [saved, setSaved] = useState(false);

  const pickPreset = (name: string) => {
    const p = presets.find((x) => x.name === name);
    if (!p) return;
    const { name: _n, ...rest } = p;
    void _n;
    setForm({ ...rest, preset: name }); setSaved(false);
  };
  const setField = (k: (typeof NUM_FIELDS)[number], v: string) => {
    const n = Number(v);
    setForm((f) => ({ ...f, [k]: Number.isFinite(n) ? n : 0, preset: undefined })); setSaved(false);
  };
  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    if (id === null) return;
    await api.patchPortfolio(id, { risk_filter: form });
    await mutatePortfolios();
    setEdits(null);
    setSaved(true);
  };
  return (
    <form onSubmit={save} className="space-y-5" aria-label={t("risk")}>
      {portfolios && id !== null && (
        <SettingsGroup label={t("portfolio")} footer={t("riskIntro")}>
          <GroupItem><PortfolioSwitcher portfolios={portfolios} value={id} onChange={(v) => { setPid(v as number); setEdits(null); setSaved(false); }} allowCombined={false} /></GroupItem>
        </SettingsGroup>
      )}
      <SettingsGroup>
        <FieldRow label={t("preset")} htmlFor="preset">
          <select id="preset" className={FIELD} value={form.preset ?? ""} onChange={(e) => pickPreset(e.target.value)}>
            {!form.preset && <option value="">{t("custom")}</option>}
            {presets.map((p) => <option key={p.name} value={p.name}>{t.has(`presets.${p.name}`) ? t(`presets.${p.name}`) : p.name}</option>)}
          </select>
        </FieldRow>
      </SettingsGroup>
      <SettingsGroup label={t("risk")} footer={t("trackingSince", { date: formatDate(current?.tracking_started_at) })}>
        {NUM_FIELDS.map((k) => (
          <FieldRow key={k} label={t(`fields.${k}`)} htmlFor={`f-${k}`}>
            <input id={`f-${k}`} className={`${FIELD} w-24`} type="number" step="any" min="0" dir="ltr" value={form[k]} onChange={(e) => setField(k, e.target.value)} />
          </FieldRow>
        ))}
        <FieldRow label={t("fields.stop_type")} htmlFor="f-stop">
          <select id="f-stop" className={FIELD} value={form.stop_type} onChange={(e) => { setForm((f) => ({ ...f, stop_type: e.target.value as RiskFilter["stop_type"], preset: undefined })); setSaved(false); }}>
            {(["fixed", "trailing", "both"] as const).map((s) => <option key={s} value={s}>{t(`stopTypes.${s}`)}</option>)}
          </select>
        </FieldRow>
      </SettingsGroup>
      <div className="flex items-center gap-3 px-1">
        <button type="submit" className="btn-primary">{c("save")}</button>
        {saved && <span role="status" className="text-sm text-gain">✓ {c("saved")}</span>}
      </div>
    </form>
  );
}
