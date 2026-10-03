"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import useSWR from "swr";
import { api, toPresets, type RiskFilter } from "@/lib/api";
import { usePortfolios } from "@/lib/hooks";
import { usePathname, useRouter } from "@/i18n/navigation";
import { AppShell } from "./AppShell";
import { Modal } from "./Modal";
import { PortfolioSwitcher } from "./PortfolioSwitcher";

const NUM_FIELDS = [
  "max_position_pct", "max_sector_pct", "max_country_pct", "max_loss_per_position_pct",
  "max_portfolio_risk_per_trade_pct", "max_total_portfolio_risk_pct", "min_rr", "drawdown_defensive_pct",
] as const;

const FALLBACK: RiskFilter = {
  preset: "balanced_aggressive", max_position_pct: 12, max_sector_pct: 30, max_country_pct: 60, max_loss_per_position_pct: 12,
  max_portfolio_risk_per_trade_pct: 1.5, max_total_portfolio_risk_pct: 12, min_rr: 2, stop_type: "both", drawdown_defensive_pct: 15,
};

function Body() {
  const t = useTranslations("settings");
  const c = useTranslations("common");
  const locale = useLocale();
  const router = useRouter();
  const pathname = usePathname();
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
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

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
  const exportData = async () => {
    try {
      const data = await api.exportData();
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url; a.download = "portfolio-manager-export.json"; a.click();
      URL.revokeObjectURL(url);
    } catch { setMsg(c("errorLoad")); }
  };
  const del = async () => {
    try { await api.deleteAccount(); router.replace("/login"); }
    catch { setMsg(c("errorLoad")); setConfirmDelete(false); }
  };

  return (
    <>
      <h1 className="text-2xl font-bold">{t("title")}</h1>

      <form onSubmit={save} className="card space-y-4" aria-label={t("risk")}>
        <div>
          <h2 className="text-lg font-bold">{t("risk")}</h2>
          <p className="text-sm text-slate-600 dark:text-slate-400">{t("riskIntro")}</p>
        </div>
        {portfolios && id !== null && <PortfolioSwitcher portfolios={portfolios} value={id} onChange={(v) => { setPid(v as number); setEdits(null); setSaved(false); }} allowCombined={false} />}
        <div>
          <label htmlFor="preset" className="label">{t("preset")}</label>
          <select id="preset" className="input" value={form.preset ?? ""} onChange={(e) => pickPreset(e.target.value)}>
            {!form.preset && <option value="">{t("custom")}</option>}
            {presets.map((p) => <option key={p.name} value={p.name}>{t.has(`presets.${p.name}`) ? t(`presets.${p.name}`) : p.name}</option>)}
          </select>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          {NUM_FIELDS.map((k) => (
            <div key={k}>
              <label htmlFor={`f-${k}`} className="label">{t(`fields.${k}`)}</label>
              <input id={`f-${k}`} className="input" type="number" step="any" min="0" dir="ltr" value={form[k]} onChange={(e) => setField(k, e.target.value)} />
            </div>
          ))}
          <div>
            <label htmlFor="f-stop" className="label">{t("fields.stop_type")}</label>
            <select id="f-stop" className="input" value={form.stop_type} onChange={(e) => { setForm((f) => ({ ...f, stop_type: e.target.value as RiskFilter["stop_type"], preset: undefined })); setSaved(false); }}>
              {(["fixed", "trailing", "both"] as const).map((s) => <option key={s} value={s}>{t(`stopTypes.${s}`)}</option>)}
            </select>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <button type="submit" className="btn-primary">{c("save")}</button>
          {saved && <span role="status" className="text-sm text-emerald-700 dark:text-emerald-400">✓ {c("saved")}</span>}
        </div>
      </form>

      <section className="card space-y-2" aria-label={t("language")}>
        <h2 className="text-lg font-bold">{t("language")}</h2>
        <div className="flex gap-2">
          {(["he", "en"] as const).map((l) => (
            <button key={l} type="button" lang={l} aria-pressed={locale === l} className={locale === l ? "btn-primary" : "btn-secondary"} onClick={() => router.replace(pathname, { locale: l })}>
              {t(`languages.${l}`)}
            </button>
          ))}
        </div>
      </section>

      <section className="card space-y-2" aria-label={t("data")}>
        <h2 className="text-lg font-bold">{t("data")}</h2>
        <p className="text-sm text-slate-600 dark:text-slate-400">{t("exportDesc")}</p>
        <button type="button" className="btn-secondary" onClick={exportData}>{t("export")}</button>
      </section>

      <section className="card space-y-2 border-red-300 dark:border-red-900" aria-label={t("danger")}>
        <h2 className="text-lg font-bold text-red-800 dark:text-red-400">{t("danger")}</h2>
        <p className="text-sm">{t("deleteWarn")}</p>
        <button type="button" className="btn-danger" onClick={() => setConfirmDelete(true)}>{t("deleteAccount")}</button>
        {msg && <p role="alert" className="text-sm text-red-700 dark:text-red-400">{msg}</p>}
      </section>

      {confirmDelete && (
        <Modal title={t("deleteTitle")} onClose={() => setConfirmDelete(false)}>
          <p className="text-sm">{t("deleteBody")}</p>
          <div className="flex gap-2">
            <button type="button" className="btn-danger" onClick={del}>{t("deleteYes")}</button>
            <button type="button" className="btn-secondary" onClick={() => setConfirmDelete(false)}>{c("cancel")}</button>
          </div>
        </Modal>
      )}
    </>
  );
}

export function SettingsPage() {
  return <AppShell><Body /></AppShell>;
}
