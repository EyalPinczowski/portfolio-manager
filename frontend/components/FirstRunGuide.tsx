"use client";
import { useEffect, useState, type ReactNode } from "react";
import { useLocale, useTranslations } from "next-intl";
import useSWR, { mutate } from "swr";
import { api, toPresets, type Holding, type Horizon, type LinkCode, type Portfolio } from "@/lib/api";
import { guideActions, useGuideState } from "@/lib/guide";
import { suggestHorizon } from "@/lib/horizonSuggest";
import { useSetupFacts } from "@/lib/setup";
import { useTelegramStatus } from "@/lib/hooks";
import { Link } from "@/i18n/navigation";
import { CreatePortfolio } from "./CreatePortfolio";
import { CodeBox } from "./NotificationSettings";
import { CheckIcon } from "./icons";

export type StepId = "disclaimer" | "portfolio" | "import" | "risk" | "horizon" | "telegram";
export type StepState = "done" | "current" | "todo" | "skipped";
export interface GuideStep { id: StepId; state: StepState; body: ReactNode }

const ORDER: StepId[] = ["disclaimer", "portfolio", "import", "risk", "horizon"];
/** Only these can be skipped. Everything else must be done. */
const OPTIONAL: StepId[] = ["telegram"];
const ORDER_WITH_TELEGRAM: StepId[] = [...ORDER, "telegram"];

/** Pure: derive each step's state from real facts plus the steps the user skipped. The first open step is "current". */
export function deriveStates(done: Record<StepId, boolean>, skipped: StepId[], order: StepId[] = ORDER): Record<StepId, StepState> {
  const out = {} as Record<StepId, StepState>;
  let currentTaken = false;
  for (const id of order) {
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
  const canClose = steps.every((s) => OPTIONAL.includes(s.id) || s.state === "done"); // required steps cannot be skipped
  return (
    <section className="card space-y-3 border-brand" aria-label={t("title")}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-heading">{t("title")}</h2>
          <p className="text-caption text-muted" role="status">{t("progress", { done: doneCount, total: steps.length })}</p>
        </div>
        {canClose && <button type="button" className="btn-secondary shrink-0" onClick={onDismiss}>{t("finish")}</button>}
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
                  {OPTIONAL.includes(s.id) && s.state !== "done" && s.state !== "skipped" && (
                    <button type="button" className="btn-secondary" onClick={() => onSkip(s.id)}>{t("skipTelegram")}</button>
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

function HorizonStep({ rows, presetOf }: { rows: { pid: number; h: Holding }[]; presetOf: (pid: number) => string | null }) {
  const t = useTranslations("guide");
  const hz = useTranslations("holding");
  const locale = useLocale();
  const [err, setErr] = useState(false);
  const [busy, setBusy] = useState(false);
  if (rows.length === 0) return <p className="text-sm">{t("noHoldingsYet")}</p>;
  const set = async (pid: number, hid: number, v: string) => {
    if (!v) return;
    setErr(false);
    try { await api.patchHolding(pid, hid, { horizon: v as Horizon }); await mutate(() => true); } catch { setErr(true); }
  };
  const suggestionOf = (r: { pid: number; h: Holding }) => (r.h.horizon ? null : suggestHorizon(r.h.asset_type, presetOf(r.pid)));
  const pending = rows.flatMap((r) => { const sg = suggestionOf(r); return sg ? [{ r, sg }] : []; });
  const applyAll = async () => {
    setBusy(true); setErr(false);
    try {
      for (const { r, sg } of pending) await api.patchHolding(r.pid, r.h.id, { horizon: sg.horizon });
      await mutate(() => true);
    } catch { setErr(true); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-2">
      {pending.length > 1 && <button type="button" className="btn-secondary" disabled={busy} onClick={() => void applyAll()}>{t("suggest.useAll")}</button>}
      {rows.map((r) => {
        const { pid, h } = r;
        const id = `guide-h-${h.id}`;
        const sg = suggestionOf(r);
        return (
          <div key={h.id} className="space-y-1">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <label htmlFor={id} className="min-w-0 flex-1 break-words text-sm font-medium">{locale === "he" ? h.name_he : h.name_en}</label>
              <select id={id} className="input w-auto min-w-40" value={h.horizon ?? ""} onChange={(e) => set(pid, h.id, e.target.value)}>
                {!h.horizon && <option value="">{t("choose")}</option>}
                {HORIZONS.map((x) => <option key={x} value={x}>{hz(`horizons.${x}`)}</option>)}
              </select>
            </div>
            {sg && (
              <div className="flex flex-wrap items-center gap-2 text-caption text-muted" data-testid="horizon-suggestion">
                <span className="min-w-0 flex-1">{t("suggest.line", { period: hz(`horizons.${sg.horizon}`), reason: t(`suggest.reason.${sg.reason}`) })}</span>
                <button type="button" className="btn-secondary" onClick={() => void set(pid, h.id, sg.horizon)}>{t("suggest.use")}</button>
              </div>
            )}
          </div>
        );
      })}
      {err && <p role="alert" className="text-sm font-medium text-loss">{t("error")}</p>}
    </div>
  );
}

/** Telegram alerts via the same link-code flow as Settings. Skipping is handled by the stepper's skip button. */
function TelegramStep() {
  const t = useTranslations("guide");
  const { data: st, mutate: refresh } = useTelegramStatus();
  const [code, setCode] = useState<LinkCode | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(false);
  const shown = st?.linked ? null : code;
  useEffect(() => {
    if (!shown) return;
    const id = setInterval(() => void refresh(), 3000);
    return () => clearInterval(id);
  }, [shown, refresh]);
  const getCode = async () => {
    setBusy(true); setErr(false);
    try { setCode(await api.telegramLinkCode()); } catch { setErr(true); } finally { setBusy(false); }
  };
  if (st?.linked) return <p className="text-sm font-medium" role="status">{t("tgLinked")}</p>;
  return (
    <div className="space-y-2">
      {shown ? <CodeBox code={shown} onNew={() => void getCode()} /> : <button type="button" className="btn-primary" disabled={busy} onClick={() => void getCode()}>{t("tgConnect")}</button>}
      {err && <p role="alert" className="text-sm font-medium text-loss">{t("tgError")}</p>}
    </div>
  );
}

/** Container: wires the steps to real state (account, portfolios, holdings, risk choice, holding periods). */
export function FirstRunGuide({ portfolios }: { portfolios: Portfolio[] }) {
  const t = useTranslations("guide");
  const d = useTranslations("disclaimer");
  const guide = useGuideState();
  const { data: tg } = useTelegramStatus();
  const { list, rows, first, done: core } = useSetupFacts(portfolios);
  const showTelegram = tg?.configured === true || tg?.linked === true; // hidden when the server has no bot
  const skipped: StepId[] = guide.telegramSkipped ? ["telegram"] : [];
  useEffect(() => { guideActions.markStarted(); }, []);

  const done: Record<StepId, boolean> = { ...core, telegram: tg?.linked === true };
  const order = showTelegram ? ORDER_WITH_TELEGRAM : ORDER;
  const states = deriveStates(done, skipped, order);
  const presetOf = (pid: number) => list.find((p) => p.id === pid)?.risk_filter?.preset ?? null;
  const bodies: Record<StepId, ReactNode> = {
    disclaimer: <p className="rounded-xl bg-surface-2 p-3 text-sm">{d("footer")}</p>,
    portfolio: <CreatePortfolio />,
    import: <Link href="/import" className="btn-primary">{t("importCta")}</Link>,
    risk: first ? <RiskStep portfolio={first} /> : <p className="text-sm">{t("needPortfolio")}</p>,
    horizon: <HorizonStep rows={rows} presetOf={presetOf} />,
    telegram: <TelegramStep />,
  };
  const steps: GuideStep[] = order.map((id) => ({ id, state: states[id], body: bodies[id] }));
  return <GuideSteps steps={steps} onSkip={() => guideActions.skipTelegram()} onDismiss={() => guideActions.dismiss()} />;
}
