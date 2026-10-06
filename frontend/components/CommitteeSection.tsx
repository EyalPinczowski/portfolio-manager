"use client";
import { useEffect, useRef, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { api, ApiError, type CommitteeOut, type CommitteeStance } from "@/lib/api";
import { COMMITTEE_MAX_ADJUSTMENT, COMMITTEE_RUNS_PER_DAY } from "@/lib/config";
import { formatNumber } from "@/lib/format";
import { Modal } from "./Modal";

type Problem = "rate" | "notFound" | "generic" | null;
const Txt = ({ children }: { children: string }) => <bdi dir="auto">{children}</bdi>;
const STANCE_CHIP: Record<CommitteeStance, string> = { rebutted: "chip-brand", accepted: "chip-warn", unresolved: "chip-neutral" };

const TEMPLATE_RISK_CODES = new Set(["no_chart_signal", "negative_chart_score"]);

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
  const pct = formatNumber(out.data_completeness_pct, locale, 0);
  const allTemplate = [r.profile, r.news, r.bear, r.cio].every((x) => x.source === "template");
  const nothingToReview = allTemplate && r.profile.status === "no_coverage" && r.news.status === "no_coverage";
  const riskText = (k: (typeof risks)[number]) => (k.code ? (TEMPLATE_RISK_CODES.has(k.code) ? t(`risk.${k.code}`) : "") : k.text);
  const shownRisks = risks.map((k, i) => ({ k, i, text: riskText(k) })).filter((x) => x.text);
  const gateText = (g: { code: string; params?: Record<string, string | number> }) =>
    t.has(`gateReason.${g.code}`) ? t(`gateReason.${g.code}`, g.params ?? {}) : t("gateReason.unknown");
  const gateCodes = out.launch_gate_codes ?? [];
  return (
    <div className="space-y-4" data-testid="committee-report">
      {!out.launch_gate_open && (
        <div className="rounded-xl border border-warn-fg p-3" data-testid="committee-gate">
          <p className="font-semibold">{t("gateTitle")}</p>
          <p className="text-sm">{t("gateBody")}</p>
          {gateCodes.length > 0 && <ul className="list-disc ps-5 text-sm text-muted">{gateCodes.map((g, i) => <li key={`${g.code}-${i}`}>{gateText(g)}</li>)}</ul>}
        </div>
      )}
      <p className="text-caption text-muted" data-testid="committee-banner">{out.llm_used ? t("llmUsed") : t("templateOnly")}</p>

      {nothingToReview ? (
        <section className="space-y-2 rounded-xl border border-line p-3" data-testid="committee-empty">
          <h3 className="font-semibold">{t("emptyTitle")}</h3>
          <p className="text-sm">{t("emptyWhy", { pct })}</p>
          <p className="text-sm text-muted">{t("emptyDo")}</p>
        </section>
      ) : (
        <>
          {out.data_completeness_low && <p className="text-sm text-muted" data-testid="committee-completeness">{t("completeness", { pct })}</p>}

          {claims.length > 0 && (
            <section className="space-y-2" aria-label={t("profileTitle")} data-testid="committee-profile">
              <h3 className="font-semibold">{t("profileTitle")}</h3>
              <ul className="space-y-2">
                {claims.map((c, i) => (
                  <li key={i} className="text-sm"><span className="chip-neutral me-2">{t(`topic.${c.topic}`)}</span><Txt>{c.text}</Txt></li>
                ))}
              </ul>
            </section>
          )}

          {news.length > 0 && (
            <section className="space-y-2" aria-label={t("newsTitle")} data-testid="committee-news">
              <h3 className="font-semibold">{t("newsTitle")}</h3>
              <ul className="space-y-2">
                {news.map((n, i) => (
                  <li key={i} className="text-sm"><span className="chip-neutral me-2">{t(`tone.${n.tone}`)}</span><Txt>{n.text}</Txt></li>
                ))}
              </ul>
            </section>
          )}

          {shownRisks.length > 0 && (
            <section className="space-y-2" aria-label={t("risksTitle")} data-testid="committee-risks">
              <h3 className="font-semibold">{t("risksTitle")}</h3>
              {r.cio.source !== "template" && <p className="text-sm text-muted">{t("risksNote")}</p>}
              <ol className="space-y-3">
                {shownRisks.map(({ k, i, text }) => {
                  const a = answers.get(i);
                  const reply = a?.reason ?? "";
                  const showReply = r.cio.source !== "template" || reply !== "";
                  return (
                    <li key={i} className="space-y-2 rounded-xl border border-line p-3" data-testid="committee-risk">
                      <p className="text-sm"><span className="chip-warn me-2">{t("severity", { n: k.severity })}</span><Txt>{text}</Txt></p>
                      {k.what_would_invalidate && <p className="text-caption text-muted">{t("invalidate")}: <Txt>{k.what_would_invalidate}</Txt></p>}
                      {showReply && (
                        <div className="rounded-lg bg-surface-2 p-2 text-sm" data-testid="committee-answer">
                          <p className="text-caption font-semibold text-muted">{t("cioAnswer")}</p>
                          {a ? <p><span className={`${STANCE_CHIP[a.stance]} me-2`} data-testid="stance">{t(`stance.${a.stance}`)}</span><Txt>{reply}</Txt></p>
                            : <p><span className="chip-neutral me-2" data-testid="stance">{t("stance.unanswered")}</span></p>}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ol>
            </section>
          )}

          <section className="space-y-2" aria-label={t("nudgeTitle")} data-testid="committee-nudge">
            <h3 className="font-semibold">{t("nudgeTitle")}</h3>
            <p className="text-sm" dir="auto">
              {s.adjustment === 0 ? t("nudgeNone") : t("nudgeBody", { adjustment: signed(s.adjustment), cap: formatNumber(COMMITTEE_MAX_ADJUSTMENT, locale, 0) })}
            </p>
            {s.base_score !== null && (
              <dl className="grid grid-cols-2 gap-2 text-sm">
                <div><dt className="text-caption text-muted">{t("baseScore")}</dt><dd className="tabular-nums" dir="ltr">{formatNumber(s.base_score, locale, 1)}</dd></div>
                <div><dt className="text-caption text-muted">{t("adjustedScore")}</dt><dd className="tabular-nums" dir="ltr">{s.adjusted_score === null ? "—" : formatNumber(s.adjusted_score, locale, 1)}</dd></div>
              </dl>
            )}
            {s.assessment.adjustment_reason && !s.assessment.adjustment_code && <p className="text-sm text-muted"><Txt>{s.assessment.adjustment_reason}</Txt></p>}
          </section>
        </>
      )}
      <p className="text-xs text-muted">{out.disclaimer}</p>
    </div>
  );
}

const STAGES = [
  { key: "profile", until: 4 },
  { key: "news", until: 9 },
  { key: "risks", until: 15 },
  { key: "summary", until: Infinity },
] as const;
/** Estimated progress: rises with elapsed seconds, flattens out and never reaches 100 until the reply arrives. */
export function estimateProgress(seconds: number): number { return Math.min(95, 95 * (1 - Math.exp(-seconds / 14))); }
const stageFor = (seconds: number) => STAGES.find((s) => seconds < s.until)!.key;

/** Deep review (the investment committee) on demand: a floating button that opens a side panel. Public data only, no verdict. State lives here, so closing the panel keeps the run going. */
export function CommitteeSection({ symbol }: { symbol: string }) {
  return <DeepReview key={symbol} symbol={symbol} />; // a new ticker starts clean
}

function DeepReview({ symbol }: { symbol: string }) {
  const t = useTranslations("committee");
  const locale = useLocale();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [problem, setProblem] = useState<Problem>(null);
  const [out, setOut] = useState<CommitteeOut | null>(null);
  const [left, setLeft] = useState<number | null>(null);
  const runId = useRef(0);
  useEffect(() => {
    if (!busy) return;
    const started = Date.now();
    const id = setInterval(() => setSeconds((Date.now() - started) / 1000), 250);
    return () => clearInterval(id);
  }, [busy]);
  const run = async () => {
    const mine = ++runId.current;
    setBusy(true); setProblem(null); setSeconds(0);
    try { const o = await api.committee(symbol); if (mine !== runId.current) return; setOut(o); setLeft(o.runs_left_today); }
    catch (err) {
      if (mine !== runId.current) return;
      setOut(null); if (err instanceof ApiError && err.status === 429) setLeft(0);
      setProblem(err instanceof ApiError ? (err.status === 429 ? "rate" : err.status === 404 ? "notFound" : "generic") : "generic");
    } finally { if (mine === runId.current) setBusy(false); }
  };
  const pct = Math.round(estimateProgress(seconds));
  return (
    <>
      <button
        type="button"
        aria-haspopup="dialog"
        aria-label={busy ? `${t("open")} (${t("busyBadge")})` : t("open")}
        data-testid="committee-fab"
        onClick={() => setOpen(true)}
        className="fixed bottom-[calc(var(--tabbar-h)+var(--safe-b)+5.5rem)] end-4 z-30 flex h-12 min-w-12 items-center justify-center gap-2 rounded-full bg-brand px-3 text-brand-on shadow-lg hover:bg-brand-hover md:bottom-6 md:px-4"
      >
        <svg aria-hidden="true" viewBox="0 0 24 24" className="h-6 w-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
        <span className="hidden text-sm font-semibold md:inline">{t("title")}</span>
        {busy && <span aria-hidden="true" data-testid="committee-fab-busy" className="h-3 w-3 animate-pulse rounded-full bg-brand-on" />}
      </button>
      {open && (
        <Modal title={t("title")} onClose={() => setOpen(false)}>
          <div className="space-y-3" aria-busy={busy} data-testid="committee">
            <p className="text-sm text-muted">{t("explainReads")}</p>
            <p className="text-sm text-muted">{t("explainNotAdvice")}</p>
            <p className="text-caption text-muted" data-testid="committee-runs-left">{left === null ? t("runsPerDay", { n: formatNumber(COMMITTEE_RUNS_PER_DAY, locale, 0) }) : t("runsLeft", { left: formatNumber(left, locale, 0), n: formatNumber(out?.runs_per_day ?? COMMITTEE_RUNS_PER_DAY, locale, 0) })}</p>
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn-primary" disabled={busy} onClick={() => void run()}>{busy ? t("running") : out ? t("rerun") : t("run")}</button>
              <button type="button" className="btn-secondary" onClick={() => setOpen(false)}>{t("close")}</button>
            </div>
            {busy && (
              <div className="space-y-1" data-testid="committee-progress">
                <div role="progressbar" aria-label={t("progressLabel")} aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} className="h-2 w-full overflow-hidden rounded-full bg-surface-2">
                  <div className="h-full rounded-full bg-brand transition-[width] duration-300" style={{ width: `${pct}%` }} />
                </div>
                <p role="status" className="text-sm text-muted">{t(`stage.${stageFor(seconds)}`)}</p>
              </div>
            )}
            {problem && <p role="alert" className="text-loss" data-testid={`committee-problem-${problem}`}>{t(`problem.${problem}`)}</p>}
            {out && <Report out={out} />}
          </div>
        </Modal>
      )}
    </>
  );
}
