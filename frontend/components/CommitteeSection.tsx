"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { api, ApiError, type CommitteeOut, type CommitteeStance } from "@/lib/api";
import { COMMITTEE_MAX_ADJUSTMENT, COMMITTEE_RUNS_PER_DAY } from "@/lib/config";
import { formatNumber } from "@/lib/format";

type Problem = "rate" | "notFound" | "generic" | null;
const Txt = ({ children }: { children: string }) => <bdi dir="auto">{children}</bdi>;
const STANCE_CHIP: Record<CommitteeStance, string> = { rebutted: "chip-brand", accepted: "chip-warn", unresolved: "chip-neutral" };

function Source({ r }: { r: { source: "llm" | "cache" | "template"; status: "ok" | "no_coverage" } }) {
  const t = useTranslations("committee");
  return <p className="text-caption text-muted">{t(`source.${r.source}`)}{r.status === "no_coverage" ? ` · ${t("noCoverage")}` : ""}</p>;
}

function Report({ out }: { out: CommitteeOut }) {
  const t = useTranslations("committee");
  const locale = useLocale();
  const r = out.report;
  const claims = r.profile.value.claims ?? [];
  const news = r.news.value.items ?? [];
  const risks = r.bear.value.risks ?? [];
  const answers = new Map((r.cio.value.responses ?? []).map((x) => [x.risk_index, x]));
  const s = r.cio_score;
  const signed = (n: number) => `${n > 0 ? "+" : ""}${formatNumber(n, locale, 1)}`;
  return (
    <div className="space-y-4" data-testid="committee-report">
      {!out.launch_gate_open && (
        <div className="rounded-xl border border-warn-fg p-3" data-testid="committee-gate">
          <p className="font-semibold">{t("gateTitle")}</p>
          <p className="text-sm">{t("gateBody")}</p>
          {(out.launch_gate_reasons ?? []).length > 0 && <ul className="list-disc ps-5 text-sm text-muted">{out.launch_gate_reasons!.map((x) => <li key={x} dir="auto"><Txt>{x}</Txt></li>)}</ul>}
        </div>
      )}
      <p className="text-caption text-muted">{out.llm_used ? t("llmUsed") : t("templateOnly")}</p>

      <section className="space-y-2" aria-label={t("profileTitle")} data-testid="committee-profile">
        <h3 className="font-semibold">{t("profileTitle")}</h3>
        {claims.length === 0 ? <p className="text-sm text-muted">{t("noProfile")}</p> : (
          <ul className="space-y-2">
            {claims.map((c, i) => (
              <li key={i} className="text-sm"><span className="chip-neutral me-2">{t(`topic.${c.topic}`)}</span><Txt>{c.text}</Txt></li>
            ))}
          </ul>
        )}
        <Source r={r.profile} />
      </section>

      <section className="space-y-2" aria-label={t("newsTitle")} data-testid="committee-news">
        <h3 className="font-semibold">{t("newsTitle")}</h3>
        {news.length === 0 ? <p className="text-sm text-muted">{t("noNews")}</p> : (
          <ul className="space-y-2">
            {news.map((n, i) => (
              <li key={i} className="text-sm"><span className="chip-neutral me-2">{t(`tone.${n.tone}`)}</span><Txt>{n.text}</Txt></li>
            ))}
          </ul>
        )}
        <Source r={r.news} />
      </section>

      <section className="space-y-2" aria-label={t("risksTitle")} data-testid="committee-risks">
        <h3 className="font-semibold">{t("risksTitle")}</h3>
        <p className="text-sm text-muted">{t("risksNote")}</p>
        {risks.length === 0 ? <p className="text-sm text-muted">{t("noRisks")}</p> : (
          <ol className="space-y-3">
            {risks.map((k, i) => {
              const a = answers.get(i);
              return (
                <li key={i} className="space-y-2 rounded-xl border border-line p-3" data-testid="committee-risk">
                  <p className="text-sm"><span className="chip-warn me-2">{t("severity", { n: k.severity })}</span><Txt>{k.text}</Txt></p>
                  {k.what_would_invalidate && <p className="text-caption text-muted">{t("invalidate")}: <Txt>{k.what_would_invalidate}</Txt></p>}
                  <div className="rounded-lg bg-surface-2 p-2 text-sm" data-testid="committee-answer">
                    <p className="text-caption font-semibold text-muted">{t("cioAnswer")}</p>
                    {a ? <p><span className={`${STANCE_CHIP[a.stance]} me-2`} data-testid="stance">{t(`stance.${a.stance}`)}</span><Txt>{a.reason}</Txt></p>
                      : <p><span className="chip-neutral me-2" data-testid="stance">{t("stance.unanswered")}</span></p>}
                  </div>
                </li>
              );
            })}
          </ol>
        )}
        <Source r={r.bear} />
      </section>

      <section className="space-y-2" aria-label={t("nudgeTitle")} data-testid="committee-nudge">
        <h3 className="font-semibold">{t("nudgeTitle")}</h3>
        <p className="text-sm" dir="auto">
          {t("nudgeBody", { adjustment: signed(s.adjustment), cap: formatNumber(COMMITTEE_MAX_ADJUSTMENT, locale, 0) })}
        </p>
        <dl className="grid grid-cols-2 gap-2 text-sm">
          <div><dt className="text-caption text-muted">{t("baseScore")}</dt><dd className="tabular-nums" dir="ltr">{s.base_score === null ? "—" : formatNumber(s.base_score, locale, 1)}</dd></div>
          <div><dt className="text-caption text-muted">{t("adjustedScore")}</dt><dd className="tabular-nums" dir="ltr">{s.adjusted_score === null ? "—" : formatNumber(s.adjusted_score, locale, 1)}</dd></div>
        </dl>
        {s.assessment.adjustment_reason && <p className="text-sm text-muted"><Txt>{s.assessment.adjustment_reason}</Txt></p>}
        <Source r={r.cio} />
      </section>
      <p className="text-xs text-muted">{out.disclaimer}</p>
    </div>
  );
}

/** Investment Committee on demand: public data only, no verdict. Result kept in memory, never stored. */
export function CommitteeSection({ symbol }: { symbol: string }) {
  const t = useTranslations("committee");
  const locale = useLocale();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem>(null);
  const [out, setOut] = useState<CommitteeOut | null>(null);
  const [left, setLeft] = useState<number | null>(null);
  const run = async () => {
    setBusy(true); setProblem(null);
    try { const o = await api.committee(symbol); setOut(o); setLeft(o.runs_left_today); }
    catch (err) { setOut(null); if (err instanceof ApiError && err.status === 429) setLeft(0); setProblem(err instanceof ApiError ? (err.status === 429 ? "rate" : err.status === 404 ? "notFound" : "generic") : "generic"); }
    finally { setBusy(false); }
  };
  return (
    <section className="card space-y-3" aria-label={t("title")} aria-busy={busy} data-testid="committee">
      <h2 className="text-heading">{t("title")}</h2>
      <p className="text-sm text-muted">{t("intro")}</p>
      <p className="text-caption text-muted" data-testid="committee-runs-left">{left === null ? t("runsPerDay", { n: formatNumber(COMMITTEE_RUNS_PER_DAY, locale, 0) }) : t("runsLeft", { left: formatNumber(left, locale, 0), n: formatNumber(out?.runs_per_day ?? COMMITTEE_RUNS_PER_DAY, locale, 0) })}</p>
      <button type="button" className="btn-primary" disabled={busy} onClick={() => void run()}>{busy ? t("running") : out ? t("rerun") : t("run")}</button>
      {busy && <p role="status" className="text-sm text-muted">{t("runningNote")}</p>}
      {problem && <p role="alert" className="text-loss" data-testid={`committee-problem-${problem}`}>{t(`problem.${problem}`)}</p>}
      {out && <Report out={out} />}
    </section>
  );
}
