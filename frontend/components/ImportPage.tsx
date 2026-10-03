"use client";
import { useRef, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { mutate } from "swr";
import { api, ApiError, type ChangeType, type ImportDraft, type ImportRow, type ProposedChange } from "@/lib/api";
import { MAX_IMPORT_IMAGES, MAX_UPLOAD_BYTES } from "@/lib/config";
import { rowProblems, type RowProblem } from "@/lib/import-rows";
import { formatDate, formatTime, formatWeight } from "@/lib/format";
import { useMe, usePortfolios } from "@/lib/hooks";
import { prepareForServer, readScreenshotsOnDevice, type OcrProgress } from "@/lib/ocr/engine";
import type { LayoutChoice } from "@/lib/ocr/layouts";
import type { RowMeta } from "@/lib/ocr/types";
import { Link } from "@/i18n/navigation";
import { AppShell } from "./AppShell";
import { CreatePortfolio } from "./CreatePortfolio";
import { Modal } from "./Modal";
import { NumberCell } from "./NumberCell";

const TYPES: ChangeType[] = ["buy", "sell", "deposit", "withdrawal"];
type ErrorKind = "generic" | "noRows" | "ocr" | "tooLarge" | "type" | "rate" | "confirm" | "serverOcr" | "unmatched" | "rows";

function Body() {
  const t = useTranslations("import");
  const c = useTranslations("common");
  const locale = useLocale();
  const { data: me, mutate: mutateMe } = useMe();
  const { data: portfolios } = usePortfolios();
  const [pid, setPid] = useState<number | null>(null);
  const [fileCount, setFileCount] = useState(0);
  const [layout, setLayout] = useState<LayoutChoice>("auto");
  const [metaByIndex, setMetaByIndex] = useState<Record<number, RowMeta>>({});
  const [imageCount, setImageCount] = useState(1);
  const fileRef = useRef<HTMLInputElement>(null);
  const synced = useRef<ImportRow[]>([]);
  const [draft, setDraft] = useState<ImportDraft | null>(null);
  const [rows, setRows] = useState<ImportRow[]>([]);
  const [changes, setChanges] = useState<ProposedChange[]>([]);
  const [bad, setBad] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<"device" | "server" | "confirm" | null>(null);
  const [progress, setProgress] = useState<OcrProgress | null>(null);
  const [error, setError] = useState<{ kind: ErrorKind; wait?: number } | null>(null);
  const [problems, setProblems] = useState<(RowProblem & { name: string })[]>([]);
  const [done, setDone] = useState(false);
  const [consentOpen, setConsentOpen] = useState(false);

  const portfolioId = pid ?? portfolios?.[0]?.id ?? null;

  /** Take the chosen file out of the input (the browser keeps no other reference to it). */
  const takeFiles = (): File[] => {
    const fs = Array.from(fileRef.current?.files ?? []);
    if (fileRef.current) fileRef.current.value = "";
    setFileCount(0);
    return fs;
  };
  const showDraft = (d: ImportDraft) => { synced.current = d.rows; setDraft(d); setRows(d.rows); setChanges(d.proposed_changes); setBad(new Set()); };
  /** `sent` = the rows of the failed request, so a 422 `loc` (position in the array) can be shown against its row. */
  const fail = (e: unknown, fallback: ErrorKind = "generic", sent?: ImportRow[]) => {
    if (e instanceof ApiError) {
      const probs = sent ? rowProblems(e) : [];
      if (e.status === 422 && probs.length > 0) {
        setProblems(probs.map((p) => ({ ...p, name: sent?.[p.position]?.name ?? "" })));
        return setError({ kind: "rows" });
      }
      if (e.status === 413) return setError({ kind: "tooLarge" });
      if (e.status === 415) return setError({ kind: "type" });
      if (e.status === 429) return setError({ kind: "rate", wait: e.retryAfter });
      // 503: the server has no OCR engine (and no cloud vision key). Point back at the on-device reader.
      if (e.status === 503) return setError({ kind: "serverOcr" });
      if (e.status === 422 && fallback === "confirm") return setError({ kind: "unmatched" });
    }
    setError({ kind: fallback });
  };

  const readOnDevice = async (e: React.FormEvent) => {
    e.preventDefault();
    const files = takeFiles();
    if (files.length === 0 || portfolioId === null) return;
    if (files.length > MAX_IMPORT_IMAGES || files.some((f) => f.size > MAX_UPLOAD_BYTES)) return setError({ kind: "tooLarge" });
    setBusy("device"); setError(null); setProblems([]); setProgress(null);
    let parsed: ImportRow[];
    let meta: RowMeta[];
    try {
      const out = await readScreenshotsOnDevice(files, { layout, onProgress: setProgress });
      parsed = out.rows; meta = out.meta;
    } catch { setBusy(null); setProgress(null); return setError({ kind: "ocr" }); }
    try {
      if (parsed.length === 0) return setError({ kind: "noRows" });
      const d = await api.importRows(portfolioId, parsed);
      setImageCount(files.length);
      setMetaByIndex(Object.fromEntries(parsed.map((r, i) => [r.index, meta[i] ?? {}])));
      showDraft(d);
    } catch (err) { fail(err, "generic", parsed); } finally { setBusy(null); setProgress(null); }
  };

  const uploadToServer = async () => {
    setConsentOpen(false);
    const [file] = takeFiles();
    if (!file || portfolioId === null) return;
    if (file.size > MAX_UPLOAD_BYTES) return setError({ kind: "tooLarge" });
    setBusy("server"); setError(null);
    try {
      if (!me?.ocr_consent) { await api.consentOcr(); await mutateMe(); }
      const d = await api.createImport(portfolioId, await prepareForServer(file, layout));
      setMetaByIndex({}); setImageCount(1);
      showDraft(d);
    } catch (err) { fail(err); } finally { setBusy(null); }
  };

  const confirm = async () => {
    if (!draft) return;
    setBusy("confirm"); setError(null); setProblems([]);
    try {
      await api.patchImport(draft.id, { rows, proposed_changes: changes });
      await api.confirmImport(draft.id);
      await mutate(() => true);
      setDone(true);
    } catch (err) { fail(err, "confirm", rows); } finally { setBusy(null); }
  };

  const editRow = (i: number, patch: Partial<ImportRow>) =>
    setRows((rs) => rs.map((r) => (r.index === i ? { ...r, ...patch } : r)));
  /** Symbol fixed or row removed: let the server re-match and recompute the proposed changes, then show them. */
  const resync = async (next: ImportRow[]) => {
    if (!draft) return;
    setRows(next);
    try {
      const d = await api.patchImport(draft.id, { rows: next });
      synced.current = d.rows; setRows(d.rows); setChanges(d.proposed_changes); setBad(new Set());
    } catch (err) { fail(err, "confirm", next); }
  };
  const symbolOf = (list: ImportRow[], i: number) => list.find((r) => r.index === i)?.symbol ?? null;
  const pickSymbol = (i: number, symbol: string) => void resync(rows.map((r) => (r.index === i ? { ...r, symbol } : r)));
  /** On leaving the symbol field: re-match only if it differs from what the server last saw. */
  const commitSymbol = (i: number) => { if (symbolOf(rows, i) !== symbolOf(synced.current, i)) void resync(rows); };
  const removeRow = (i: number) => void resync(rows.filter((r) => r.index !== i));
  const editChange = (match: (c: ProposedChange) => boolean, patch: Partial<ProposedChange>) =>
    setChanges((cs) => cs.map((x) => (match(x) ? { ...x, ...patch } : x)));
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

  /** Held before, absent from this screenshot: the server proposes a sale (row_index -1) and removes the holding on confirm. */
  const vanished = changes.filter((c) => c.row_index < 0);
  const missingSymbol = rows.some((r) => !r.symbol);
  const needsQuantity = (r: ImportRow) => metaByIndex[r.index]?.quantity_uncertain === true && !(typeof r.quantity === "number" && r.quantity > 0);
  const missingQuantity = rows.some(needsQuantity);
  const problemList = error?.kind === "rows" && problems.length > 0 && (
    <ul className="list-disc ps-5 text-sm text-loss">
      {problems.map((p, i) => (
        <li key={i}>{t("rowError", {
          row: p.position + 1,
          name: p.name ? ` (${p.name})` : "",
          field: p.field ? (t.has(`rowFields.${p.field}`) ? t(`rowFields.${p.field}`) : p.field) : "",
          msg: p.msg,
        })}</li>
      ))}
    </ul>
  );
  const errorText = error && (error.kind === "rate"
    ? (error.wait ? t("error.rateWait", { seconds: error.wait }) : t("error.rate"))
    : error.kind === "rows" ? t("rowErrorsTitle") : t(`error.${error.kind}`));

  return (
    <>
      <h1 className="text-2xl font-bold">{t("title")}</h1>
      {!draft && portfolios && portfolios.length === 0 && <CreatePortfolio />}
      {!draft && portfolios && portfolios.length > 0 && (
        <form onSubmit={readOnDevice} className="card space-y-4">
          <p className="text-sm text-muted">{t("intro")}</p>
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
              id="imp-file" ref={fileRef} className="input" type="file" accept="image/png,image/jpeg,image/webp" multiple required
              onChange={(e) => setFileCount(e.target.files?.length ?? 0)}
            />
            <p className="mt-1 text-xs text-muted">{t("manyFilesHint", { max: MAX_IMPORT_IMAGES })}</p>
          </div>
          <div>
            <label htmlFor="imp-layout" className="label">{t("layout")}</label>
            <select id="imp-layout" className="input" value={layout} onChange={(e) => setLayout(e.target.value as LayoutChoice)}>
              <option value="auto">{t("layoutAuto")}</option>
              <option value="meitav_trade">{t("layoutMeitav")}</option>
            </select>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <button type="submit" className="btn-primary" disabled={fileCount === 0 || busy !== null}>
              {busy === "device" ? t("reading") : t("readOnDevice")}
            </button>
            <button type="button" className="btn-secondary" disabled={fileCount !== 1 || busy !== null} onClick={() => (me?.ocr_consent ? uploadToServer() : setConsentOpen(true))}>
              {busy === "server" ? t("uploading") : t("useServer")}
            </button>
          </div>
          <p className="text-xs text-muted">{t("onDeviceNote")}</p>
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
          <p className="text-sm text-muted">{t("reviewHint")}</p>
          {draft.expires_at && (
            <p className="text-xs text-muted">{t("expires", { time: `${formatDate(draft.expires_at)} ${formatTime(draft.expires_at, locale)}` })}</p>
          )}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[56rem] text-sm">
              <thead className="bg-surface-2">
                <tr>
                  {(["name", "symbol", "quantity", "price", "value", "currency", "change", "changeAmount"] as const).map((k) => (
                    <th key={k} scope="col" className="px-2 py-2 text-start font-semibold">{t(`col.${k}`)}</th>
                  ))}
                  <th scope="col" className="px-2 py-2"><span className="sr-only">{t("col.remove")}</span></th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => {
                  const ch = changes.find((x) => x.row_index === r.index);
                  const m = metaByIndex[r.index] ?? {};
                  const mustEnterQty = needsQuantity(r);
                  const flagged = r.flags.length > 0 || mustEnterQty || !!m.conflict || !!m.quantity_fractional;
                  const k = (f: string) => `${draft.id}-${r.index}-${f}`;
                  const isCash = ch?.type === "deposit" || ch?.type === "withdrawal";
                  return (
                    <tr key={r.index} data-flagged={flagged} className={`border-t align-top ${flagged ? "bg-amber-50 dark:bg-amber-950/40" : ""} border-line`}>
                      <td className="px-2 py-2">
                        <input aria-label={`${t("col.name")} ${r.index + 1}`} className="input min-w-32" value={r.name} onChange={(e) => editRow(r.index, { name: e.target.value })} />
                        {flagged && (
                          <ul className="mt-1 text-xs font-medium text-amber-900 dark:text-amber-200">
                            {r.flags.map((f) => <li key={f}>⚠ {t.has(`flags.${f}`) ? t(`flags.${f}`) : f}</li>)}
                            {mustEnterQty && <li>⚠ {t("notes.quantityUncertain")}</li>}
                            {m.quantity_fractional && <li>⚠ {t("notes.quantityFractional")}</li>}
                            {m.conflict && (
                              <li>⚠ {t("notes.conflict", {
                                value: m.conflict.value === null ? "—" : String(m.conflict.value),
                                price: m.conflict.price === null ? "—" : String(m.conflict.price),
                              })}</li>
                            )}
                          </ul>
                        )}
                        {(m.cost_inferred || m.duplicate_removed) && (
                          <ul className="mt-1 text-xs text-muted">
                            {m.cost_inferred && <li data-testid="note-cost-inferred">{t("notes.costInferred", { pnl: `${(m.pnl_pct ?? 0) > 0 ? "+" : ""}${m.pnl_pct ?? 0}%` })}</li>}
                            {m.duplicate_removed && <li data-testid="note-duplicate">{t("notes.duplicateRemoved")}</li>}
                          </ul>
                        )}
                      </td>
                      <td className="px-2 py-2">
                        <input aria-label={`${t("col.symbol")} ${r.index + 1}`} className="input w-28" dir="ltr" value={r.symbol ?? ""} onChange={(e) => editRow(r.index, { symbol: e.target.value.trim() || null })} onBlur={() => commitSymbol(r.index)} />
                        {r.candidates && r.candidates.length > 0 && (
                          <div className="mt-1 text-xs">
                            <p className="font-medium">{t("candidates")}</p>
                            <ul className="space-y-1">
                              {r.candidates.map((cand) => (
                                <li key={cand.symbol}>
                                  <button type="button" className="text-start font-semibold text-brand-text underline" onClick={() => pickSymbol(r.index, cand.symbol)}>
                                    {t("useCandidate", { symbol: cand.symbol, name: cand.name, pct: formatWeight(cand.score, locale, 0) })}
                                  </button>
                                </li>
                              ))}
                            </ul>
                          </div>
                        )}
                        {r.tase_number && <p className="mt-1 text-xs text-muted" dir="ltr">{t("taseNumber", { n: r.tase_number })}</p>}
                      </td>
                      <td className="px-2 py-2"><NumberCell key={k("q")} label={`${t("col.quantity")} ${r.index + 1}`} className="w-24" required={mustEnterQty} value={r.quantity} onValue={(n, ok) => { editRow(r.index, { quantity: n }); cell(k("q"))(ok); }} /></td>
                      <td className="px-2 py-2"><NumberCell key={k("p")} label={`${t("col.price")} ${r.index + 1}`} className="w-24" value={r.price} onValue={(n, ok) => { editRow(r.index, { price: n }); cell(k("p"))(ok); }} /></td>
                      <td className="px-2 py-2"><NumberCell key={k("v")} label={`${t("col.value")} ${r.index + 1}`} className="w-28" value={r.value} onValue={(n, ok) => { editRow(r.index, { value: n }); cell(k("v"))(ok); }} /></td>
                      <td className="px-2 py-2 tabular-nums" dir="ltr">{r.currency}{r.unit === "agorot" ? " (ag.)" : ""}</td>
                      <td className="px-2 py-2">
                        {ch && (
                          <select aria-label={`${t("col.change")} ${r.index + 1}`} className="input w-32" value={ch.type} onChange={(e) => editChange((x) => x.row_index === r.index, { type: e.target.value as ChangeType })}>
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
                            onValue={(n, ok) => { editChange((x) => x.row_index === r.index, isCash ? { amount: n } : { quantity: n }); cell(k("c"))(ok); }}
                          />
                        )}
                      </td>
                      <td className="px-2 py-2">
                        <button type="button" className="btn-secondary" aria-label={t("removeRow", { n: r.index + 1 })} onClick={() => removeRow(r.index)}>✕</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {vanished.length > 0 && (
            <div className="space-y-2 rounded-xl bg-amber-50 p-3 dark:bg-amber-950/40" role="group" aria-label={t("vanished.title")}>
              <h3 className="font-semibold">{imageCount > 1 ? t("vanished.titleMany") : t("vanished.title")}</h3>
              <p className="text-sm text-amber-900 dark:text-amber-200">{t("vanished.hint")}</p>
              <ul className="space-y-2">
                {vanished.map((ch) => {
                  const isCash = ch.type === "deposit" || ch.type === "withdrawal";
                  const sym = ch.symbol ?? "";
                  const match = (x: ProposedChange) => x.row_index < 0 && x.symbol === ch.symbol;
                  return (
                    <li key={sym} className="flex flex-wrap items-center gap-2">
                      <span className="min-w-20 font-semibold" dir="ltr">{sym}</span>
                      <select aria-label={`${t("col.change")} ${sym}`} className="input w-36" value={ch.type} onChange={(e) => editChange(match, { type: e.target.value as ChangeType })}>
                        {(["sell", "withdrawal"] as const).map((x) => <option key={x} value={x}>{t(`vanished.type.${x}`)}</option>)}
                      </select>
                      <NumberCell
                        key={`gone-${sym}-${ch.type}`}
                        label={`${t("col.changeAmount")} ${sym}`} className="w-28"
                        value={isCash ? ch.amount : ch.quantity}
                        onValue={(n, ok) => { editChange(match, isCash ? { amount: n } : { quantity: n }); cell(`gone-${sym}`)(ok); }}
                      />
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
          {missingQuantity && <p role="status" className="text-sm text-amber-900 dark:text-amber-200">{t("notes.quantityRequiredHint")}</p>}
          {missingSymbol && <p role="status" className="text-sm text-amber-900 dark:text-amber-200">{t("error.unmatchedHint")}</p>}
          {bad.size > 0 && <p role="alert" className="text-sm text-loss">{t("error.badNumber")}</p>}
          {error && <p role="alert" className="text-sm text-loss">{errorText}</p>}
          {problemList}
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-primary" onClick={confirm} disabled={busy === "confirm" || bad.size > 0 || missingSymbol || missingQuantity || rows.length === 0}>
              {busy === "confirm" ? t("confirming") : t("confirm")}
            </button>
            <button type="button" className="btn-secondary" onClick={() => { setDraft(null); setRows([]); setChanges([]); setError(null); }}>{c("cancel")}</button>
          </div>
        </section>
      )}
      {!draft && error && <p role="alert" className="text-sm text-loss">{errorText}</p>}
      {!draft && problemList}
    </>
  );
}

export function ImportPage() {
  return <AppShell><Body /></AppShell>;
}
