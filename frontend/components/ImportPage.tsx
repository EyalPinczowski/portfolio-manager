"use client";
import { useRef, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { mutate } from "swr";
import { api, ApiError, type ChangeType, type ImportDraft, type ImportRow, type ProposedChange } from "@/lib/api";
import { MAX_UPLOAD_BYTES } from "@/lib/config";
import { formatDate, formatTime, formatWeight } from "@/lib/format";
import { useMe, usePortfolios } from "@/lib/hooks";
import { prepareForServer, readScreenshotOnDevice, type OcrProgress } from "@/lib/ocr/engine";
import { Link } from "@/i18n/navigation";
import { AppShell } from "./AppShell";
import { Modal } from "./Modal";
import { NumberCell } from "./NumberCell";

const TYPES: ChangeType[] = ["buy", "sell", "deposit", "withdrawal"];
type ErrorKind = "generic" | "noRows" | "ocr" | "tooLarge" | "type" | "rate" | "confirm";

function Body() {
  const t = useTranslations("import");
  const c = useTranslations("common");
  const locale = useLocale();
  const { data: me, mutate: mutateMe } = useMe();
  const { data: portfolios } = usePortfolios();
  const [pid, setPid] = useState<number | null>(null);
  const [hasFile, setHasFile] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState<ImportDraft | null>(null);
  const [rows, setRows] = useState<ImportRow[]>([]);
  const [changes, setChanges] = useState<ProposedChange[]>([]);
  const [bad, setBad] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<"device" | "server" | "confirm" | null>(null);
  const [progress, setProgress] = useState<OcrProgress | null>(null);
  const [error, setError] = useState<{ kind: ErrorKind; wait?: number } | null>(null);
  const [done, setDone] = useState(false);
  const [consentOpen, setConsentOpen] = useState(false);

  const portfolioId = pid ?? portfolios?.[0]?.id ?? null;

  /** Take the chosen file out of the input (the browser keeps no other reference to it). */
  const takeFile = (): File | null => {
    const f = fileRef.current?.files?.[0] ?? null;
    if (fileRef.current) fileRef.current.value = "";
    setHasFile(false);
    return f;
  };
  const showDraft = (d: ImportDraft) => { setDraft(d); setRows(d.rows); setChanges(d.proposed_changes); setBad(new Set()); };
  const fail = (e: unknown, fallback: ErrorKind = "generic") => {
    if (e instanceof ApiError) {
      if (e.status === 413) return setError({ kind: "tooLarge" });
      if (e.status === 415) return setError({ kind: "type" });
      if (e.status === 429) return setError({ kind: "rate", wait: e.retryAfter });
    }
    setError({ kind: fallback });
  };

  const readOnDevice = async (e: React.FormEvent) => {
    e.preventDefault();
    const file = takeFile();
    if (!file || portfolioId === null) return;
    if (file.size > MAX_UPLOAD_BYTES) return setError({ kind: "tooLarge" });
    setBusy("device"); setError(null); setProgress(null);
    let parsed: ImportRow[];
    try {
      parsed = await readScreenshotOnDevice(file, setProgress);
    } catch { setBusy(null); setProgress(null); return setError({ kind: "ocr" }); }
    try {
      if (parsed.length === 0) return setError({ kind: "noRows" });
      showDraft(await api.importRows(portfolioId, parsed));
    } catch (err) { fail(err); } finally { setBusy(null); setProgress(null); }
  };

  const uploadToServer = async () => {
    setConsentOpen(false);
    const file = takeFile();
    if (!file || portfolioId === null) return;
    if (file.size > MAX_UPLOAD_BYTES) return setError({ kind: "tooLarge" });
    setBusy("server"); setError(null);
    try {
      if (!me?.ocr_consent) { await api.consentOcr(); await mutateMe(); }
      showDraft(await api.createImport(portfolioId, await prepareForServer(file)));
    } catch (err) { fail(err); } finally { setBusy(null); }
  };

  const confirm = async () => {
    if (!draft) return;
    setBusy("confirm"); setError(null);
    try {
      await api.patchImport(draft.id, { rows, proposed_changes: changes });
      await api.confirmImport(draft.id);
      await mutate(() => true);
      setDone(true);
    } catch (err) { fail(err, "confirm"); } finally { setBusy(null); }
  };

  const editRow = (i: number, patch: Partial<ImportRow>) =>
    setRows((rs) => rs.map((r) => (r.index === i ? { ...r, ...patch } : r)));
  const editChange = (i: number, patch: Partial<ProposedChange>) =>
    setChanges((cs) => cs.map((x) => (x.row_index === i ? { ...x, ...patch } : x)));
  const cell = (key: string) => (valid: boolean) =>
    setBad((s) => { const n = new Set(s); if (valid) n.delete(key); else n.add(key); return n; });

  if (done) {
    return (
      <div className="card space-y-3 text-center">
        <p role="status" className="text-lg font-semibold">{t("done")}</p>
        <Link href="/" className="btn-primary">{t("backHome")}</Link>
      </div>
    );
  }

  const errorText = error && (error.kind === "rate"
    ? (error.wait ? t("error.rateWait", { seconds: error.wait }) : t("error.rate"))
    : t(`error.${error.kind}`));

  return (
    <>
      <h1 className="text-2xl font-bold">{t("title")}</h1>
      {!draft && (
        <form onSubmit={readOnDevice} className="card space-y-4">
          <p className="text-sm text-slate-700 dark:text-slate-300">{t("intro")}</p>
          <p className="rounded-xl bg-emerald-50 p-3 text-sm font-medium text-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200">{t("keepOnly")}</p>
          <div>
            <label htmlFor="imp-portfolio" className="label">{t("portfolio")}</label>
            <select id="imp-portfolio" className="input" value={portfolioId ?? ""} onChange={(e) => setPid(Number(e.target.value))}>
              {(portfolios ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="imp-file" className="label">{t("chooseFile")}</label>
            <input
              id="imp-file" ref={fileRef} className="input" type="file" accept="image/png,image/jpeg,image/webp" required
              onChange={(e) => setHasFile((e.target.files?.length ?? 0) > 0)}
            />
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <button type="submit" className="btn-primary" disabled={!hasFile || busy !== null}>
              {busy === "device" ? t("reading") : t("readOnDevice")}
            </button>
            <button type="button" className="btn-secondary" disabled={!hasFile || busy !== null} onClick={() => (me?.ocr_consent ? uploadToServer() : setConsentOpen(true))}>
              {busy === "server" ? t("uploading") : t("useServer")}
            </button>
          </div>
          <p className="text-xs text-slate-600 dark:text-slate-400">{t("onDeviceNote")}</p>
          {busy === "device" && (
            <div role="status" aria-live="polite" className="space-y-1 text-sm">
              <p>{progress && progress.status.includes("loading") ? t("progress.loading") : t("progress.reading")}</p>
              <progress className="w-full" max={1} value={progress?.progress ?? 0} aria-label={t("reading")} />
            </div>
          )}
        </form>
      )}
      {consentOpen && (
        <Modal title={t("consentTitle")} onClose={() => setConsentOpen(false)}>
          <p className="text-sm">{t("consentBody")}</p>
          <div className="flex gap-2">
            <button type="button" className="btn-primary" onClick={uploadToServer}>{t("consentAccept")}</button>
            <button type="button" className="btn-secondary" onClick={() => setConsentOpen(false)}>{t("consentDecline")}</button>
          </div>
        </Modal>
      )}
      {draft && (
        <section className="card space-y-3" aria-label={t("reviewTitle")}>
          <h2 className="text-lg font-bold">{t("reviewTitle")}</h2>
          <p className="text-sm text-slate-700 dark:text-slate-300">{t("reviewHint")}</p>
          {draft.expires_at && (
            <p className="text-xs text-slate-600 dark:text-slate-400">{t("expires", { time: `${formatDate(draft.expires_at)} ${formatTime(draft.expires_at, locale)}` })}</p>
          )}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[56rem] text-sm">
              <thead className="bg-slate-100 dark:bg-slate-800">
                <tr>
                  {(["name", "symbol", "quantity", "price", "value", "currency", "change", "changeAmount"] as const).map((k) => (
                    <th key={k} scope="col" className="px-2 py-2 text-start font-semibold">{t(`col.${k}`)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const ch = changes.find((x) => x.row_index === r.index);
                  const flagged = r.flags.length > 0;
                  const k = (f: string) => `${draft.id}-${r.index}-${f}`;
                  const isCash = ch?.type === "deposit" || ch?.type === "withdrawal";
                  return (
                    <tr key={r.index} data-flagged={flagged} className={`border-t align-top ${flagged ? "bg-amber-50 dark:bg-amber-950/40" : ""} border-slate-200 dark:border-slate-800`}>
                      <td className="px-2 py-2">
                        <input aria-label={`${t("col.name")} ${r.index + 1}`} className="input min-w-32" value={r.name} onChange={(e) => editRow(r.index, { name: e.target.value })} />
                        {flagged && (
                          <ul className="mt-1 text-xs font-medium text-amber-900 dark:text-amber-200">
                            {r.flags.map((f) => <li key={f}>⚠ {t.has(`flags.${f}`) ? t(`flags.${f}`) : f}</li>)}
                          </ul>
                        )}
                      </td>
                      <td className="px-2 py-2">
                        <input aria-label={`${t("col.symbol")} ${r.index + 1}`} className="input w-28" dir="ltr" value={r.symbol ?? ""} onChange={(e) => editRow(r.index, { symbol: e.target.value || null })} />
                        {r.candidates && r.candidates.length > 0 && (
                          <div className="mt-1 text-xs">
                            <p className="font-medium">{t("candidates")}</p>
                            <ul className="space-y-1">
                              {r.candidates.map((cand) => (
                                <li key={cand.symbol}>
                                  <button type="button" className="text-start font-semibold text-blue-800 underline dark:text-blue-300" onClick={() => editRow(r.index, { symbol: cand.symbol })}>
                                    {t("useCandidate", { symbol: cand.symbol, name: cand.name, pct: formatWeight(cand.score * 100, locale, 0) })}
                                  </button>
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {r.tase_number && <p className="mt-1 text-xs text-slate-600 dark:text-slate-400" dir="ltr">{t("taseNumber", { n: r.tase_number })}</p>}
                      </td>
                      <td className="px-2 py-2"><NumberCell key={k("q")} label={`${t("col.quantity")} ${r.index + 1}`} className="w-24" value={r.quantity} onValue={(n, ok) => { editRow(r.index, { quantity: n }); cell(k("q"))(ok); }} /></td>
                      <td className="px-2 py-2"><NumberCell key={k("p")} label={`${t("col.price")} ${r.index + 1}`} className="w-24" value={r.price} onValue={(n, ok) => { editRow(r.index, { price: n }); cell(k("p"))(ok); }} /></td>
                      <td className="px-2 py-2"><NumberCell key={k("v")} label={`${t("col.value")} ${r.index + 1}`} className="w-28" value={r.value} onValue={(n, ok) => { editRow(r.index, { value: n }); cell(k("v"))(ok); }} /></td>
                      <td className="px-2 py-2 tabular-nums" dir="ltr">{r.currency}{r.unit === "agorot" ? " (ag.)" : ""}</td>
                      <td className="px-2 py-2">
                        {ch && (
                          <select aria-label={`${t("col.change")} ${r.index + 1}`} className="input w-32" value={ch.type} onChange={(e) => editChange(r.index, { type: e.target.value as ChangeType })}>
                            {TYPES.map((x) => <option key={x} value={x}>{t(`changeType.${x}`)}</option>)}
                          </select>
                        )}
                      </td>
                      <td className="px-2 py-2">
                        {ch && (
                          <NumberCell
                            key={`${k("c")}-${ch.type}`}
                            label={`${t("col.changeAmount")} ${r.index + 1}`} className="w-28"
                            value={isCash ? ch.amount : ch.quantity}
                            onValue={(n, ok) => { editChange(r.index, isCash ? { amount: n } : { quantity: n }); cell(k("c"))(ok); }}
                          />
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {bad.size > 0 && <p role="alert" className="text-sm text-red-700 dark:text-red-400">{t("error.badNumber")}</p>}
          {error && <p role="alert" className="text-sm text-red-700 dark:text-red-400">{errorText}</p>}
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-primary" onClick={confirm} disabled={busy === "confirm" || bad.size > 0}>
              {busy === "confirm" ? t("confirming") : t("confirm")}
            </button>
            <button type="button" className="btn-secondary" onClick={() => { setDraft(null); setRows([]); setChanges([]); setError(null); }}>{c("cancel")}</button>
          </div>
        </section>
      )}
      {!draft && error && <p role="alert" className="text-sm text-red-700 dark:text-red-400">{errorText}</p>}
    </>
  );
}

export function ImportPage() {
  return <AppShell><Body /></AppShell>;
}
