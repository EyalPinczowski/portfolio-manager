"use client";
import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import useSWR from "swr";
import { api, ApiError, type AskConversation, type AskMessage, type NeedsHorizon } from "@/lib/api";
import { ASK_MAX_QUESTION_CHARS } from "@/lib/config";
import { holdingHref } from "@/lib/routes";
import { Link } from "@/i18n/navigation";
import { usePortfolios, type PortfolioRef } from "@/lib/hooks";
import { formatDate } from "@/lib/format";
import { AppShell } from "./AppShell";
import { PortfolioSwitcher } from "./PortfolioSwitcher";
import { ConfirmSheet } from "./SettingsUI";

type Problem = "rate" | "empty" | "notFound" | "generic" | null;

function HorizonPrompt({ items }: { items: NeedsHorizon[] }) {
  const t = useTranslations("ask");
  return (
    <div className="space-y-2 rounded-xl border border-warn-fg p-3" role="status" data-testid="needs-horizon">
      <p className="font-semibold">{t("needsHorizonTitle")}</p>
      <p className="text-sm">{t("needsHorizonBody")}</p>
      <ul className="flex flex-wrap gap-2">
        {items.map((h) => (
          <li key={`${h.portfolio_id}-${h.holding_id}`}>
            <Link href={holdingHref(h.holding_id)} className="btn-secondary" data-testid="needs-horizon-link"><span dir="ltr">{h.symbol}</span> · {t("needsHorizonLink")}</Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Answers cite their tools inline as "[tool:name]"; the tools are already listed as chips below. */
export function stripCites(text: string): string {
  return text.replace(/\s*\[tool:[a-z_]+\]/g, "").trim();
}

function Bubble({ m }: { m: AskMessage }) {
  const t = useTranslations("ask");
  const tools = m.tools_called ?? [];
  if (m.role === "user") {
    return <li className="ms-auto max-w-[90%] rounded-2xl bg-brand-soft p-3 text-brand-text" data-testid="msg-user"><p dir="auto" className="whitespace-pre-wrap break-words">{m.content}</p></li>;
  }
  return (
    <li className="max-w-full space-y-2 rounded-2xl bg-surface-2 p-3" data-testid="msg-answer">
      {m.declined && <p className="chip-warn" role="status">{t("declined")}</p>}
      <p dir="auto" className="whitespace-pre-wrap break-words">{stripCites(m.content)}</p>
      <div className="flex flex-wrap items-center gap-2 text-caption text-muted">
        {tools.length > 0 ? (
          <>
            <span>{t("toolsUsed")}:</span>
            <ul className="flex flex-wrap gap-1" aria-label={t("toolsUsed")} data-testid="tools-used">
              {tools.map((x) => <li key={x} className="chip-neutral">{t.has(`tool.${x}`) ? t(`tool.${x}`) : x}</li>)}
            </ul>
          </>
        ) : <span data-testid="no-tools">{t("noTools")}</span>}
        {m.source && <span>· {t(`source.${m.source}`)}</span>}
      </div>
    </li>
  );
}

function Body() {
  const t = useTranslations("ask");
  const c = useTranslations("common");
  const { data: portfolios } = usePortfolios();
  const [pid, setPid] = useState<PortfolioRef>("combined");
  const [question, setQuestion] = useState("");
  const [convId, setConvId] = useState<number | null>(null);
  const [messages, setMessages] = useState<AskMessage[]>([]);
  const [needs, setNeeds] = useState<NeedsHorizon[]>([]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem>(null);
  const [toDelete, setToDelete] = useState<AskConversation | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [delErr, setDelErr] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const list = useSWR<AskConversation[]>("ask-conversations", () => api.askConversations(), { revalidateOnFocus: false });
  const valid = pid === "combined" || portfolios?.some((p) => p.id === pid);
  const ref: PortfolioRef = valid ? pid : "combined";
  useEffect(() => { if (messages.length > 0) endRef.current?.scrollIntoView?.({ block: "nearest" }); }, [messages.length]);

  if (!portfolios) return <p role="status" className="text-muted">{c("loading")}</p>;

  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    const q = question.trim();
    if (!q) { setProblem("empty"); return; }
    setProblem(null); setNeeds([]); setBusy(true);
    try {
      const r = await api.askPortfolio({ question: q, conversation_id: convId, portfolio_id: convId === null && ref !== "combined" ? ref : null });
      setConvId(r.conversation_id);
      setMessages((m) => [...m, r.question, r.answer]);
      setNeeds(r.needs_horizon ?? []);
      setQuestion("");
      void list.mutate();
    } catch (err) {
      setProblem(err instanceof ApiError ? (err.status === 429 ? "rate" : err.status === 404 ? "notFound" : "generic") : "generic");
    } finally { setBusy(false); }
  };

  const open = async (id: number) => {
    setProblem(null);
    try {
      const d = await api.askConversation(id);
      setConvId(d.id); setMessages(d.messages); setNeeds([]);
      if (d.portfolio_id != null) setPid(d.portfolio_id);
    } catch (err) { setProblem(err instanceof ApiError && err.status === 404 ? "notFound" : "generic"); void list.mutate(); }
  };
  const fresh = () => { setNeeds([]); setConvId(null); setMessages([]); setProblem(null); setQuestion(""); };
  const remove = async () => {
    if (!toDelete) return;
    setDeleting(true); setDelErr(false);
    try {
      await api.deleteAskConversation(toDelete.id);
      if (toDelete.id === convId) fresh();
      setToDelete(null);
      void list.mutate();
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) { setToDelete(null); void list.mutate(); } else setDelErr(true);
    } finally { setDeleting(false); }
  };

  const left = ASK_MAX_QUESTION_CHARS - question.length;
  return (
    <>
      <div className="space-y-1">
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-sm text-muted">{t("intro")}</p>
      </div>
      <section className="card space-y-3" aria-label={t("title")}>
        {convId === null && <PortfolioSwitcher portfolios={portfolios} value={ref} onChange={setPid} />}
        {messages.length > 0 && (
          <>
            <ul className="space-y-2" aria-live="polite" aria-label={t("thread")} data-testid="thread">
              {messages.map((m) => <Bubble key={m.id} m={m} />)}
            </ul>
            {needs.length > 0 && <HorizonPrompt items={needs} />}
            <div ref={endRef} />
          </>
        )}
        <form onSubmit={send} className="space-y-2">
          <label htmlFor="ask-portfolio-q" className="label">{t("askLabel")}</label>
          <textarea
            id="ask-portfolio-q" className="input" rows={3} maxLength={ASK_MAX_QUESTION_CHARS} dir="auto" value={question}
            placeholder={t("placeholder")} onChange={(e) => { setQuestion(e.target.value); if (problem === "empty") setProblem(null); }}
          />
          <p className="text-caption text-muted" dir="ltr">{left}</p>
          {problem && (
            <p role="alert" className="text-sm text-loss" data-testid={`ask-problem-${problem}`}>
              {t(`problem.${problem}`)}
            </p>
          )}
          <div className="flex flex-wrap gap-2">
            <button type="submit" className="btn-primary" disabled={busy}>{busy ? t("sending") : t("send")}</button>
            {convId !== null && <button type="button" className="btn-secondary" onClick={fresh}>{t("newConversation")}</button>}
          </div>
        </form>
        <p className="text-caption text-muted">{t("note")}</p>
      </section>

      <section className="card space-y-2" aria-label={t("historyTitle")}>
        <h2 className="text-heading">{t("historyTitle")}</h2>
        {list.error ? <p role="alert" className="text-loss">{c("errorLoad")}</p>
          : !list.data ? <p role="status" className="text-muted">{c("loading")}</p>
          : list.data.length === 0 ? <p className="text-sm text-muted" data-testid="no-conversations">{t("historyNone")}</p> : (
            <ul className="divide-y divide-line" data-testid="conversations">
              {list.data.map((cv) => (
                <li key={cv.id} className="flex items-center justify-between gap-2 py-1" data-testid="conversation">
                  <button type="button" className="flex min-h-11 min-w-0 flex-1 flex-col justify-center text-start hover:underline" aria-label={t("openConversation", { title: cv.title })} onClick={() => void open(cv.id)}>
                    <bdi dir="auto" className="truncate font-medium">{cv.title}</bdi>
                    <span className="text-caption text-muted">{formatDate(cv.updated_at)}</span>
                  </button>
                  <button type="button" className="btn-secondary" aria-label={t("deleteConversation", { title: cv.title })} onClick={() => { setDelErr(false); setToDelete(cv); }}>{c("delete")}</button>
                </li>
              ))}
            </ul>
          )}
        <p className="text-caption text-muted">{t("historyNote")}</p>
      </section>
      {toDelete && (
        <ConfirmSheet title={t("deleteTitle")} body={t("deleteBody")} confirmLabel={t("deleteYes")} cancelLabel={c("cancel")} busy={deleting} onConfirm={() => void remove()} onCancel={() => setToDelete(null)}>
          {delErr && <p role="alert" className="text-sm text-loss">{t("problem.generic")}</p>}
        </ConfirmSheet>
      )}
    </>
  );
}

export function AskPage() {
  return <AppShell><Body /></AppShell>;
}
