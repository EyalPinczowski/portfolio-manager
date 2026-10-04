"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { api, ApiError, type XrayRuleOut, type XrayRuleResult } from "@/lib/api";
import { formatWeight } from "@/lib/format";
import { useXrayRules } from "@/lib/hooks";
import { ExplanationView } from "./ExplanationView";

const STATE_CHIP = { breach: "chip-warn", ok: "chip-neutral", off: "chip-neutral" } as const;
const STATE_MARK = { breach: "⚠", ok: "✓", off: "–" } as const;

/** Parses a typed percentage (dot or comma); null when it is not a number. */
const parsePct = (s: string): number | null => {
  const n = Number(s.trim().replace(",", "."));
  return s.trim() !== "" && Number.isFinite(n) ? n : null;
};

function Controls({ pid, cfg, onChanged }: { pid: number; cfg: XrayRuleOut; onChanged: () => void }) {
  const t = useTranslations("xray");
  const [text, setText] = useState(cfg.override_pct !== null ? String(cfg.override_pct) : "");
  const [bounds, setBounds] = useState({ min: cfg.min_pct, max: cfg.max_pct });
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const { mutate } = useXrayRules(pid);
  const id = `xr-${cfg.rule}`;
  const n = parsePct(text);
  const outOfRange = n !== null && (n < cfg.min_pct || n > cfg.max_pct);

  const send = async (change: { enabled?: boolean; threshold_pct?: number | null }, done?: () => void) => {
    setBusy(true); setMsg(null);
    try {
      await api.patchXrayRules(pid, { rules: [{ rule: cfg.rule, ...change }] });
      done?.();
      await Promise.all([mutate(), Promise.resolve(onChanged())]);
      if ("threshold_pct" in change) setMsg({ kind: "ok", text: t("saved") });
    } catch (e) {
      if (e instanceof ApiError && e.status === 422 && e.code === "threshold_out_of_bounds") {
        const b = e.body as { min_pct?: number; max_pct?: number };
        const next = { min: b.min_pct ?? cfg.min_pct, max: b.max_pct ?? cfg.max_pct };
        setBounds(next);
        setMsg({ kind: "err", text: t("outOfBounds", next) });
      } else setMsg({ kind: "err", text: t("saveError") });
    } finally { setBusy(false); }
  };

  return (
    <div className="space-y-2 border-t border-line pt-2 text-sm">
      <label className="flex items-center gap-2">
        <input type="checkbox" role="switch" checked={cfg.enabled} disabled={busy} onChange={(e) => void send({ enabled: e.target.checked })} />
        {t("enable")}
      </label>
      <div>
        <label htmlFor={id} className="label">{cfg.default_threshold_pct === null ? t("setLimitLabel") : t("overrideLabel")}</label>
        <div className="flex flex-wrap items-center gap-2">
          <input id={id} className="input w-28" inputMode="decimal" dir="ltr" value={text} aria-describedby={`${id}-hint`}
            aria-invalid={outOfRange} onChange={(e) => { setText(e.target.value); setMsg(null); }} />
          <button type="button" className="btn-secondary" disabled={busy || n === null || outOfRange}
            onClick={() => void send({ threshold_pct: n })}>{busy ? t("saving") : t("save")}</button>
          {cfg.override_pct !== null && (
            <button type="button" className="btn-secondary" disabled={busy} onClick={() => void send({ threshold_pct: null }, () => setText(""))}>{t("clear")}</button>
          )}
        </div>
        <p id={`${id}-hint`} className="mt-1 text-caption text-muted">{cfg.default_threshold_pct === null ? t("setLimitHint", { min: bounds.min, max: bounds.max }) : t("overrideHint", { min: bounds.min, max: bounds.max, def: cfg.default_threshold_pct })}</p>
        {outOfRange && <p className="text-caption text-warn-fg" role="status">{t("outOfBounds", bounds)}</p>}
        {msg && <p className={`text-caption ${msg.kind === "err" ? "text-warn-fg" : "text-muted"}`} role={msg.kind === "err" ? "alert" : "status"}>{msg.text}</p>}
      </div>
    </div>
  );
}

/** The four toggleable exposure rules. Informational only: nothing here blocks or changes anything. */
export function XrayRules({ pid, rules, onChanged }: { pid: number; rules: XrayRuleResult[]; onChanged: () => void }) {
  const t = useTranslations("xray");
  const locale = useLocale();
  const { data, error } = useXrayRules(pid);
  return (
    <section className="space-y-3" aria-label={t("rulesTitle")} data-testid="xray-rules">
      <div>
        <h2 className="text-lg font-bold">{t("rulesTitle")}</h2>
        <p className="text-sm text-muted">{t("rulesIntro")}</p>
      </div>
      {error && <p role="alert" className="text-sm">{t("rulesError")}</p>}
      <ul className="grid gap-3 md:grid-cols-2">
        {rules.map((r) => {
          const cfg = data?.rules.find((x) => x.rule === r.rule);
          return (
            <li key={r.rule} className="card space-y-2" data-testid={`rule-${r.rule}`} data-state={r.state}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="font-semibold">{t(`ruleName.${r.rule}`)}</h3>
                <span className={STATE_CHIP[r.state]}><span aria-hidden="true">{STATE_MARK[r.state]} </span>{t(`state.${r.state}`)}</span>
              </div>
              <p className="text-sm text-muted">
                {r.threshold_pct === null ? t("noLimit") : <>{t("thresholdNow", { value: formatWeight(r.threshold_pct, locale) })} · {t(`source.${r.threshold_source}`)}</>}
                {r.state !== "off" && typeof r.value_pct === "number" && <> · {t("largest", { value: formatWeight(r.value_pct, locale) })}</>}
              </p>
              {r.state === "breach" && r.items.length > 0 && (
                <ul className="flex flex-wrap gap-1.5" aria-label={t("above")}>
                  {r.items.map((i) => <li key={i.name} className="chip-warn"><bdi dir="auto">{i.name}</bdi> <span dir="ltr" className="tabular-nums">{formatWeight(i.value_pct, locale)}</span></li>)}
                </ul>
              )}
              {r.threshold_pct === null && r.state !== "off" && r.items.length > 0 && (
                <div>
                  <p className="text-caption text-muted">{t("currencySplit")}</p>
                  <ul className="flex flex-wrap gap-1.5" aria-label={t("currencySplit")}>
                    {r.items.map((i) => <li key={i.name} className="chip-neutral"><bdi dir="auto">{i.name}</bdi> <span dir="ltr" className="tabular-nums">{formatWeight(i.value_pct, locale)}</span></li>)}
                  </ul>
                </div>
              )}
              {r.state === "ok" && r.threshold_pct !== null && <p className="text-sm text-muted">{t("none")}</p>}
              <details className="border-t border-line pt-2">
                <summary className="cursor-pointer text-sm font-medium text-brand-text">{t("why")}</summary>
                <div className="mt-2"><ExplanationView e={r.explanation} /></div>
              </details>
              {cfg && <Controls key={`${cfg.enabled}-${cfg.override_pct}-${cfg.min_pct}`} pid={pid} cfg={cfg} onChanged={onChanged} />}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
