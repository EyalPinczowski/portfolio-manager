"use client";
import { useEffect, useState, type ReactNode } from "react";
import { useLocale, useTranslations } from "next-intl";
import useSWR, { mutate } from "swr";
import { api, toPresets, type Holding, type Horizon, type Portfolio } from "@/lib/api";
import { guideActions, useGuideState } from "@/lib/guide";
import { useMe, usePortfolios } from "@/lib/hooks";
import { Link } from "@/i18n/navigation";
import { CreatePortfolio } from "./CreatePortfolio";
import { CheckIcon } from "./icons";

export type StepId = "disclaimer" | "portfolio" | "import" | "risk" | "horizon";
export type StepState = "done" | "current" | "todo" | "skipped";
export interface GuideStep { id: StepId; state: StepState; body: ReactNode }

const ORDER: StepId[] = ["disclaimer", "portfolio", "import", "risk", "horizon"];

/** Pure: derive each step's state from real facts plus the steps the user skipped. The first open step is "current". */
export function deriveStates(done: Record<StepId, boolean>, skipped: StepId[]): Record<StepId, StepState> {
  const out = {} as Record<StepId, StepState>;
  let currentTaken = false;
  for (const id of ORDER) {
    if (done[id]) out[id] = "done";
    else if (skipped.includes(id)) out[id] = "skipped";
    else if (!currentTaken) { out[id] = "current"; currentTaken = true; }
    else out[id] = "todo";
  }
  return out;
}

/** Presentational stepper: marks each step done / current / todo / skipped and shows the body of the open one. */
export function GuideSteps({ steps, onSkip, onDismiss }: { steps: GuideStep[]; onSkip: (id: StepId) => void; onDismiss: () => void }) {
  const t = useTranslations("guide");
  const current = steps.find((s) => s.state === "current")?.id ?? null;
  const [picked, setPicked] = useState<StepId | "none" | null>(null);
  const open = picked === "none" ? null : (picked ?? current);
  const doneCount = steps.filter((s) => s.state === "done").length;
  const finished = current === null;
  return (
    <section className="card space-y-3 border-brand" aria-label={t("title")}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-heading">{t("title")}</h2>
          <p className="text-caption text-muted" role="status">{t("progress", { done: doneCount, total: steps.length })}</p>
        </div>
        <button type="button" className="btn-secondary shrink-0" onClick={onDismiss}>{finished ? t("finish") : t("skipAll")}</button>
      </div>
      <ol className="space-y-2">
        {steps.map((s, i) => {
          const isOpen = open === s.id && s.state !== "done";
          return (
            <li key={s.id} data-step={s.id} data-state={s.state} className="rounded-xl border border-line">
              <button
                type="button"
                aria-expanded={isOpen}
                className="flex min-h-12 w-full items-center gap-3 px-3 py-2 text-start"
                onClick={() => setPicked(isOpen ? "none" : s.id)}
              >
                <span
                  aria-hidden="true"
                  className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${s.state === "done" ? "bg-brand text-brand-on" : s.state === "current" ? "border-2 border-brand text-brand-text" : "border border-line text-muted"}`}
                >
                  {s.state === "done" ? <CheckIcon className="h-4 w-4" /> : i + 1}
                </span>
                <span className="flex-1 font-semibold">{t(`steps.${s.id}.title`)}</span>
                <span className={`text-caption ${s.state === "current" ? "text-brand-text" : "text-muted"}`}>{t(`state.${s.state}`)}</span>
              </button>
              {isOpen && (
                <div className="space-y-3 border-t border-line px-3 py-3">
                  <p className="text-sm text-muted">{t(`steps.${s.id}.hint`)}</p>
                  {s.body}
                  {s.state !== "done" && s.state !== "skipped" && (
                    <button type="button" className="btn-secondary" onClick={() => onSkip(s.id)}>{t("skipStep")}</button>
                  )}
                </div>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function RiskStep({ portfolio }: { portfolio: Portfolio }) {
  const t = useTranslations("guide");
  const s = useTranslations("settings");
  const { data } = useSWR("presets", () => api.riskPresets());
  const presets = data ? toPresets(data) : [];
  const [choice, setChoice] = useState<string | null>(null); // never pre-selected
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(false);
  const save = async () => {
    const p = presets.find((x) => x.name === choice);
    if (!p) return;
    setBusy(true); setErr(false);
    try {
      const { name, ...rest } = p;
      await api.patchPortfolio(portfolio.id, { risk_filter: { ...rest, preset: name } });
      guideActions.markRiskChosen(portfolio.id);
      await mutate(() => true);
    } catch { setErr(true); } finally { setBusy(false); }
  };
  return (
    <fieldset className="space-y-2">
      <legend className="sr-only">{t("steps.risk.title")}</legend>
      {presets.map((p) => (
        <label key={p.name} className="flex min-h-11 cursor-pointer items-center gap-3 rounded-xl border border-line px-3 py-2 has-[:checked]:border-brand has-[:checked]:bg-brand-soft">
          <input type="radio" name="guide-risk" className="h-5 w-5 accent-[var(--brand)]" checked={choice === p.name} onChange={() => setChoice(p.name)} />
          <span className="flex-1">
            <span className="block font-medium">{s.has(`presets.${p.name}`) ? s(`presets.${p.name}`) : p.name}</span>
            <span className="block text-caption text-muted">{t("riskLine", { loss: p.max_loss_per_position_pct })}</span>
          </span>
        </label>
      ))}
      {err && <p role="alert" className="text-sm font-medium text-loss">{t("error")}</p>}
      <button type="button" className="btn-primary" disabled={!choice || busy} onClick={save}>{t("riskSave")}</button>
    </fieldset>
  );
}

const HORIZONS: Horizon[] = ["1w", "1m", "3m", "6m", "1y"];

function HorizonStep({ rows }: { rows: { pid: number; h: Holding }[] }) {
  const t = useTranslations("guide");
  const hz = useTranslations("holding");
  const locale = useLocale();
  const [err, setErr] = useState(false);
  if (rows.length === 0) return <p className="text-sm">{t("noHoldingsYet")}</p>;
  const set = async (pid: number, hid: number, v: string) => {
    if (!v) return;
    setErr(false);
    try { await api.patchHolding(pid, hid, { horizon: v as Horizon }); await mutate(() => true); } catch { setErr(true); }
  };
  return (
    <div className="space-y-2">
      {rows.map(({ pid, h }) => {
        const id = `guide-h-${h.id}`;
        return (
          <div key={h.id} className="flex flex-wrap items-center justify-between gap-2">
            <label htmlFor={id} className="min-w-0 flex-1 break-words text-sm font-medium">{locale === "he" ? h.name_he : h.name_en}</label>
            <select id={id} className="input w-auto min-w-40" value={h.horizon ?? ""} onChange={(e) => set(pid, h.id, e.target.value)}>
              {!h.horizon && <option value="">{t("choose")}</option>}
              {HORIZONS.map((x) => <option key={x} value={x}>{hz(`horizons.${x}`)}</option>)}
            </select>
          </div>
        );
      })}
      {err && <p role="alert" className="text-sm font-medium text-loss">{t("error")}</p>}
    </div>
  );
}

/** Container: wires the steps to real state (account, portfolios, holdings, risk choice, holding periods). */
export function FirstRunGuide({ portfolios }: { portfolios: Portfolio[] }) {
  const t = useTranslations("guide");
  const d = useTranslations("disclaimer");
  const { data: me } = useMe();
  const { data: live } = usePortfolios();
  const list = live ?? portfolios;
  const guide = useGuideState();
  const [skipped, setSkipped] = useState<StepId[]>([]);
  const ids = list.map((p) => p.id);
  const { data: lists } = useSWR(ids.length ? ["guide-holdings", ...ids] : null, async () =>
    Promise.all(ids.map(async (pid) => ({ pid, holdings: await api.holdings(pid) }))));
  useEffect(() => { guideActions.markStarted(); }, []);

  const rows = (lists ?? []).flatMap((l) => l.holdings.map((h) => ({ pid: l.pid, h })));
  const first = list[0];
  const done: Record<StepId, boolean> = {
    disclaimer: me?.disclaimer_accepted === true,
    portfolio: list.length > 0,
    import: rows.length > 0,
    risk: !!first && guide.riskChosen.includes(first.id),
    horizon: rows.length > 0 && rows.every((r) => !!r.h.horizon),
  };
  const states = deriveStates(done, skipped);
  const bodies: Record<StepId, ReactNode> = {
    disclaimer: <p className="rounded-xl bg-surface-2 p-3 text-sm">{d("footer")}</p>,
    portfolio: <CreatePortfolio />,
    import: <Link href="/import" className="btn-primary">{t("importCta")}</Link>,
    risk: first ? <RiskStep portfolio={first} /> : <p className="text-sm">{t("needPortfolio")}</p>,
    horizon: <HorizonStep rows={rows} />,
  };
  const steps: GuideStep[] = ORDER.map((id) => ({ id, state: states[id], body: bodies[id] }));
  return <GuideSteps steps={steps} onSkip={(id) => setSkipped((s) => [...s, id])} onDismiss={() => guideActions.dismiss()} />;
}
