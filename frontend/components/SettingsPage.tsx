"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import useSWR from "swr";
import { api, ApiError, toPresets, type RiskFilter } from "@/lib/api";
import { ageOf, formatDate, formatTime, QUOTES_STALE_MIN } from "@/lib/format";
import { usePortfolios, useSummary } from "@/lib/hooks";
import { usePathname, useRouter } from "@/i18n/navigation";
import { guideActions } from "@/lib/guide";
import { AppShell } from "./AppShell";
import { Modal } from "./Modal";
import { PortfolioSwitcher } from "./PortfolioSwitcher";

type PasswordAction = "export" | "delete";

/** Asks for the account password (export/delete need it; a wrong one gives 403). */
function PasswordPrompt({ action, onDone, onCancel }: { action: PasswordAction; onDone: () => void; onCancel: () => void }) {
  const t = useTranslations("settings");
  const c = useTranslations("common");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    try {
      if (action === "export") {
        const data = await api.exportData(password);
        const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
        try {
          const a = document.createElement("a");
          a.href = url; a.download = "portfolio-manager-export.json"; a.click();
        } finally { URL.revokeObjectURL(url); }
      } else {
        await api.deleteAccount(password);
      }
      onDone();
    } catch (error) {
      const s = error instanceof ApiError ? error.status : 0;
      setErr(s === 403 ? t("wrongPassword") : s === 429 ? t("tooMany") : c("errorLoad"));
    } finally { setBusy(false); setPassword(""); }
  };
  const isDelete = action === "delete";
  return (
    <Modal title={isDelete ? t("deleteTitle") : t("exportTitle")} onClose={onCancel}>
      <form onSubmit={submit} className="space-y-3">
        <p className="text-sm">{isDelete ? t("deleteBody") : t("exportBody")}</p>
        <div>
          <label htmlFor="confirm-password" className="label">{t("confirmPassword")}</label>
          <input id="confirm-password" className="input" type="password" required autoComplete="current-password" dir="ltr" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {err && <p role="alert" className="text-sm text-loss">{err}</p>}
        <div className="flex gap-2">
          <button type="submit" className={isDelete ? "btn-danger" : "btn-primary"} disabled={busy || password === ""}>{isDelete ? t("deleteYes") : t("exportYes")}</button>
          <button type="button" className="btn-secondary" onClick={onCancel}>{c("cancel")}</button>
        </div>
      </form>
    </Modal>
  );
}

function Sessions() {
  const t = useTranslations("settings");
  const locale = useLocale();
  const { data, error, mutate: refresh } = useSWR("sessions", () => api.sessions());
  const [fail, setFail] = useState(false);
  const run = async (fn: () => Promise<unknown>) => {
    setFail(false);
    try { await fn(); await refresh(); } catch { setFail(true); }
  };
  const when = (iso: string) => `${formatDate(iso)} ${formatTime(iso, locale)}`;
  const others = (data ?? []).filter((x) => !x.current);
  return (
    <section className="card space-y-3" aria-label={t("sessions")}>
      <div>
        <h2 className="text-lg font-bold">{t("sessions")}</h2>
        <p className="text-sm text-muted">{t("sessionsDesc")}</p>
      </div>
      {error && <p role="alert" className="text-sm">{t("sessionsError")}</p>}
      {data && (
        <ul className="space-y-2">
          {data.map((x) => (
            <li key={x.id} className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-line p-3 text-sm ">
              <div>
                <p className="font-semibold">{x.current ? t("sessionCurrent") : t("sessionOther")}</p>
                <p className="text-xs text-muted">{t("sessionSignedIn", { time: when(x.created_at) })} · {t("sessionLastSeen", { time: when(x.last_seen_at) })}</p>
              </div>
              {!x.current && <button type="button" className="btn-secondary" onClick={() => run(() => api.revokeSession(x.id))}>{t("sessionRevoke")}</button>}
            </li>
          ))}
        </ul>
      )}
      {others.length > 0 && <button type="button" className="btn-secondary" onClick={() => run(() => api.revokeOtherSessions())}>{t("sessionRevokeAll")}</button>}
      {fail && <p role="alert" className="text-sm text-loss">{t("sessionsError")}</p>}
    </section>
  );
}

/** System status from /api/health, plus the FX staleness note from the summary. Never shows a verdict. */
export function SystemStatus() {
  const t = useTranslations("settings");
  const { data: health, error } = useSWR("health", () => api.health(), { refreshInterval: 60_000, shouldRetryOnError: false });
  const { data: portfolios } = usePortfolios();
  const { data: summary } = useSummary(portfolios?.[0]?.id ?? null);
  const rel = (iso: string | null | undefined) => {
    const a = ageOf(iso);
    if (!a) return t("never");
    return a.unit === "now" ? t("relNow") : t(a.unit === "minutes" ? "relMinutes" : a.unit === "hours" ? "relHours" : "relDays", { n: a.n });
  };
  if (error) return <section className="card" aria-label={t("status")}><h2 className="text-lg font-bold">{t("status")}</h2><p role="alert" className="text-sm">{t("statusLoadError")}</p></section>;
  if (!health) return null;
  const stockOpen = !!summary && (summary.markets.US.open || summary.markets.TASE.open);
  const quotesAge = ageOf(health.last_quotes_at);
  const quotesOld = quotesAge === null || quotesAge.unit === "hours" || quotesAge.unit === "days" || (quotesAge.unit === "minutes" && quotesAge.n > QUOTES_STALE_MIN);
  const down = health.scheduler === "unavailable";
  const stale = stockOpen && quotesOld;
  return (
    <section className="card space-y-2" aria-label={t("status")}>
      <h2 className="text-lg font-bold">{t("status")}</h2>
      {down && <p role="alert" className="rounded-lg bg-warn-bg px-3 py-2 text-sm font-medium text-warn-fg">{t("schedulerDown")}</p>}
      {stale && <p role="alert" className="rounded-lg bg-warn-bg px-3 py-2 text-sm font-medium text-warn-fg">{t("staleWarning")}</p>}
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-muted">{t("scheduler")}</dt>
        <dd>{t(`schedulerStates.${health.scheduler}`)}</dd>
        <dt className="text-muted">{t("lastQuotes")}</dt>
        <dd data-testid="last-quotes">{rel(health.last_quotes_at)}</dd>
        <dt className="text-muted">{t("lastSnapshot")}</dt>
        <dd data-testid="last-snapshot" title={health.last_snapshot_at ? formatDate(health.last_snapshot_at) : undefined}>{rel(health.last_snapshot_at)}</dd>
      </dl>
      <p className="text-sm text-muted">{t("fxNote")}</p>
      {summary && <p className="text-sm" role="status">{summary.fx_stale ? t("fxNow") : t("fxOk")}</p>}
    </section>
  );
}

function LaunchGateNote() {
  const t = useTranslations("settings");
  const { data } = useSWR("launch-gate", () => api.launchGate(), { shouldRetryOnError: false });
  if (!data) return null;
  return (
    <section className="card space-y-2" aria-label={t("gate")}>
      <h2 className="text-lg font-bold">{t("gate")}</h2>
      <p className="text-sm">{data.open ? t("gateOpen") : t("gateClosed")}</p>
      {!data.open && data.reasons.length > 0 && <ul className="list-disc ps-5 text-sm text-muted">{data.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
    </section>
  );
}

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
  const [prompt, setPrompt] = useState<PasswordAction | null>(null);

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
    <>
      <h1 className="text-2xl font-bold">{t("title")}</h1>

      <form onSubmit={save} className="card space-y-4" aria-label={t("risk")}>
        <div>
          <h2 className="text-lg font-bold">{t("risk")}</h2>
          <p className="text-sm text-muted">{t("riskIntro")}</p>
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
        <p className="text-sm text-muted">{t("trackingSince", { date: formatDate(current?.tracking_started_at) })}</p>
        <div className="flex items-center gap-3">
          <button type="submit" className="btn-primary">{c("save")}</button>
          {saved && <span role="status" className="text-sm text-gain">✓ {c("saved")}</span>}
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

      <section className="card space-y-2" aria-label={t("guideTitle")}>
        <h2 className="text-lg font-bold">{t("guideTitle")}</h2>
        <p className="text-sm text-muted">{t("guideDesc")}</p>
        <button type="button" className="btn-secondary" onClick={() => { guideActions.reopen(); router.push("/"); }}>{t("guideOpen")}</button>
      </section>

      <Sessions />
      <SystemStatus />
      <LaunchGateNote />

      <section className="card space-y-2" aria-label={t("data")}>
        <h2 className="text-lg font-bold">{t("data")}</h2>
        <p className="text-sm text-muted">{t("exportDesc")}</p>
        <button type="button" className="btn-secondary" onClick={() => setPrompt("export")}>{t("export")}</button>
      </section>

      <section className="card space-y-2 border-loss" aria-label={t("danger")}>
        <h2 className="text-lg font-bold text-loss">{t("danger")}</h2>
        <p className="text-sm">{t("deleteWarn")}</p>
        <button type="button" className="btn-danger" onClick={() => setPrompt("delete")}>{t("deleteAccount")}</button>
      </section>

      {prompt && (
        <PasswordPrompt
          action={prompt}
          onCancel={() => setPrompt(null)}
          onDone={() => { const was = prompt; setPrompt(null); if (was === "delete") router.replace("/login"); }}
        />
      )}
    </>
  );
}

export function SettingsPage() {
  return <AppShell><Body /></AppShell>;
}
