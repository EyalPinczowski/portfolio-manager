"use client";
import { useRef, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { mutate } from "swr";
import { api, ApiError, type ChangeType, type ImportDraft, type ImportRow, type ImportScope, type ProposedChange, type ProposedChangeType } from "@/lib/api";
import { MAX_IMPORT_IMAGES, MAX_UPLOAD_BYTES } from "@/lib/config";
import { conflictOf, currencyForUnit, effectiveFlags, needsConflictAck, needsQuantity, rowProblems, type RowProblem } from "@/lib/import-rows";
import { formatDate, formatMoney, formatTime, formatWeight } from "@/lib/format";
import { useHoldings, useMe, usePortfolios, useSummary } from "@/lib/hooks";
import { groupRows, notInScreenshots, updateTotals, type RowGroup } from "@/lib/import-groups";
import { prepareForServer, readScreenshotsOnDevice, type OcrProgress } from "@/lib/ocr/engine";
import type { LayoutChoice } from "@/lib/ocr/layouts";
import type { RowMeta } from "@/lib/ocr/types";
import { Link } from "@/i18n/navigation";
import { AppShell } from "./AppShell";
import { CreatePortfolio } from "./CreatePortfolio";
import { Modal } from "./Modal";
import { NumberCell } from "./NumberCell";

const TYPES: ChangeType[] = ["buy", "sell", "deposit", "withdrawal"];
type ErrorKind = "generic" | "noRows" | "ocr" | "tooLarge" | "type" | "rate" | "confirm" | "serverOcr" | "unmatched" | "currencyChanged" | "rows";

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
  /** Rows whose screenshot conflict the user has looked at (client-only; the server keeps the flag). */
  /** "These screenshots show my whole portfolio": off by default; on = holdings missing from them are asked about. */
  const [whole, setWhole] = useState(false);
  const [acked, setAcked] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState<"device" | "server" | "confirm" | null>(null);
  const [progress, setProgress] = useState<OcrProgress | null>(null);
  const [error, setError] = useState<{ kind: ErrorKind; wait?: number } | null>(null);
  const [problems, setProblems] = useState<(RowProblem & { name: string })[]>([]);
  const [done, setDone] = useState(false);
  const [consentOpen, setConsentOpen] = useState(false);

  const portfolioId = pid ?? portfolios?.[0]?.id ?? null;
  const scope: ImportScope = draft?.scope ?? (whole ? "full" : "partial");
  const heldNow = useHoldings(draft ? draft.portfolio_id : null, portfolios);
  const summaryNow = useSummary(draft ? draft.portfolio_id : null);

  /** Take the chosen file out of the input (the browser keeps no other reference to it). */
  const takeFiles = (): File[] => {
    const fs = Array.from(fileRef.current?.files ?? []);
    if (fileRef.current) fileRef.current.value = "";
    setFileCount(0);
    return fs;
  };
  const showDraft = (d: ImportDraft) => { synced.current = d.rows; setDraft(d); setRows(d.rows); setChanges(d.proposed_changes); setBad(new Set()); setAcked(new Set()); };
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
      if (e.status === 422 && fallback === "confirm") return setError({ kind: /currency/i.test(e.message) ? "currencyChanged" : "unmatched" });
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
      const d = await api.importRows(portfolioId, parsed, whole ? "full" : "partial");
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
      let d = await api.createImport(portfolioId, await prepareForServer(file, layout));
      if (whole) d = await api.patchImport(d.id, { scope: "full" });
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

  /** Switching scope recomputes the proposed changes on the server (the "not in these screenshots" list appears or goes). */
  const changeScope = async (next: ImportScope) => {
    if (!draft) return;
    setError(null);
    try {
      const d = await api.patchImport(draft.id, { rows, scope: next });
      synced.current = d.rows; setDraft(d); setRows(d.rows); setChanges(d.proposed_changes); setBad(new Set());
    } catch (err) { fail(err, "confirm", rows); }
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
  const setUnit = (i: number, unit: ImportRow["unit"]) => editRow(i, { unit, currency: currencyForUnit(unit) });
  /** The user has looked at the currency/unit: drop the flag the server raised (it blocks confirming until then). */
  const setCurrencyChecked = (i: number, checked: boolean) =>
    setRows((rs) => rs.map((r) => (r.index !== i ? r : {
      ...r, flags: checked ? r.flags.filter((f) => f !== "currency_changed") : r.flags.includes("currency_changed") ? r.flags : [...r.flags, "currency_changed"],
    })));
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
  const vanished = scope === "full" ? notInScreenshots(changes) : [];
  const missingSymbol = rows.some((r) => !r.symbol);
  const missingQuantity = rows.some((r) => needsQuantity(r, metaByIndex[r.index]));
  const unconfirmedCurrency = rows.some((r) => r.flags.includes("currency_changed"));
  const unackedConflict = rows.some((r) => needsConflictAck(r, metaByIndex[r.index]) && !acked.has(r.index));
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

  const renderTable = (draft: ImportDraft, list: ImportRow[]) => (
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[64rem] text-sm">
              <thead className="bg-surface-2">
                <tr>
                  {(["name", "symbol", "quantity", "price", "value", "cost", "currency", "change", "changeAmount"] as const).map((k) => (
                    <th key={k} scope="col" className="px-2 py-2 text-start font-semibold">{t(`col.${k}`)}</th>
                  ))}
                  <th scope="col" className="px-2 py-2"><span className="sr-only">{t("col.remove")}</span></th>
                </tr>
              </thead>
              <tbody>
                {list.map((r) => {
                  const ch = changes.find((x) => x.row_index === r.index);
                  const m = metaByIndex[r.index] ?? {};
                  const fl = effectiveFlags(r, m);
                  const mustEnterQty = needsQuantity(r, m);
                  const conflict = fl.has("conflict") ? conflictOf(r, m) : null;
                  const mustAck = needsConflictAck(r, m);
                  const SERVER_NOTES = ["quantity_uncertain", "quantity_fractional", "conflict", "cost_inferred", "duplicate_removed"];
                  const plainFlags = r.flags.filter((f) => !SERVER_NOTES.includes(f));
                  const flagged = plainFlags.length > 0 || mustEnterQty || fl.has("quantity_fractional") || mustAck;
                  const k = (f: string) => `${draft.id}-${r.index}-${f}`;
                  const isCash = ch?.type === "deposit" || ch?.type === "withdrawal";
                  const rowType = (ch?.type === "keep" ? undefined : ch?.type) as ChangeType | undefined;
                  return (
                    <tr key={r.index} data-flagged={flagged} className={`border-t align-top ${flagged ? "bg-amber-50 dark:bg-amber-950/40" : ""} border-line`}>
                      <td className="px-2 py-2">
                        <input aria-label={`${t("col.name")} ${r.index + 1}`} className="input min-w-32" value={r.name} onChange={(e) => editRow(r.index, { name: e.target.value })} />
                        {flagged && (
                          <ul className="mt-1 text-xs font-medium text-amber-900 dark:text-amber-200">
                            {plainFlags.map((f) => <li key={f}>⚠ {t.has(`flags.${f}`) ? t(`flags.${f}`) : f}</li>)}
                            {mustEnterQty && <li data-testid="note-quantity-uncertain">⚠ {t("notes.quantityUncertain")}</li>}
                            {fl.has("quantity_fractional") && <li data-testid="note-quantity-fractional">⚠ {t("notes.quantityFractional")}</li>}
                            {mustAck && (
                              <li data-testid="note-conflict">
                                ⚠ {t("notes.conflict", {
                                  value: conflict?.value == null ? "—" : String(conflict.value),
                                  price: conflict?.price == null ? "—" : String(conflict.price),
                                })}
                                <label className="mt-1 flex items-center gap-2 font-semibold">
                                  <input
                                    type="checkbox" checked={acked.has(r.index)}
                                    aria-label={`${t("notes.conflictAck")} ${r.index + 1}`}
                                    onChange={(e) => setAcked((cur) => { const n = new Set(cur); if (e.target.checked) n.add(r.index); else n.delete(r.index); return n; })}
                                  />
                                  {t("notes.conflictAck")}
                                </label>
                              </li>
                            )}
                          </ul>
                        )}
                        {(fl.has("cost_inferred") || fl.has("duplicate_removed")) && (
                          <ul className="mt-1 text-xs text-muted">
                            {fl.has("cost_inferred") && (
                              <li data-testid="note-cost-inferred">
                                {typeof m.pnl_pct === "number"
                                  ? t("notes.costInferred", { pnl: `${m.pnl_pct > 0 ? "+" : ""}${m.pnl_pct}%` })
                                  : t("notes.costInferredNoPct")}
                              </li>
                            )}
                            {fl.has("duplicate_removed") && <li data-testid="note-duplicate">{t("notes.duplicateRemoved")}</li>}
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
                      <td className="px-2 py-2"><NumberCell key={k("k")} label={`${t("col.cost")} ${r.index + 1}`} className="w-24" value={r.cost} onValue={(n, ok) => { setRows((rs) => rs.map((x) => (x.index === r.index ? { ...x, cost: n, flags: x.flags.filter((f) => f !== "cost_inferred") } : x))); cell(k("k"))(ok); }} /></td>
                      <td className="px-2 py-2">
                        <select aria-label={`${t("col.currency")} ${r.index + 1}`} className="input w-28" dir="ltr" value={r.unit} onChange={(e) => setUnit(r.index, e.target.value as ImportRow["unit"])}>
                          <option value="ILS">{t("unit.ILS")}</option>
                          <option value="agorot">{t("unit.agorot")}</option>
                          <option value="USD">{t("unit.USD")}</option>
                        </select>
                        {r.flags.includes("currency_changed") && (
                          <label className="mt-1 flex items-center gap-2 text-xs font-semibold">
                            <input type="checkbox" checked={false} aria-label={`${t("currencyChecked")} ${r.index + 1}`} onChange={(e) => setCurrencyChecked(r.index, e.target.checked)} />
                            {t("currencyChecked")}
                          </label>
                        )}
                      </td>
                      <td className="px-2 py-2">
                        {ch && (
                          <select aria-label={`${t("col.change")} ${r.index + 1}`} className="input w-32" value={rowType ?? "buy"} onChange={(e) => editChange((x) => x.row_index === r.index, { type: e.target.value as ChangeType })}>
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
  );

  return (
    <>
      <h1 className="text-2xl font-bold">{t("title")}</h1>
      {!draft && portfolios && portfolios.length === 0 && <CreatePortfolio />}
      {!draft && portfolios && portfolios.length > 0 && (
        <form onSubmit={readOnDevice} className="card space-y-4">
          <p className="text-sm text-muted">{t("intro")}</p>
          <p className="rounded-xl bg-brand-soft p-3 text-sm font-medium text-brand-text">{t("keepOnly")}</p>
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
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={whole} onChange={(e) => setWhole(e.target.checked)} />
            <span>
              <span className="font-semibold">{t("scope.whole")}</span>
              <span className="block text-xs text-muted">{t("scope.wholeHint")}</span>
            </span>
          </label>
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
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={scope === "full"} onChange={(e) => void changeScope(e.target.checked ? "full" : "partial")} aria-label={t("scope.whole")} />
            <span>
              <span className="font-semibold">{t("scope.whole")}</span>
              <span className="block text-xs text-muted">{scope === "full" ? t("scope.fullActive") : t("scope.partialActive")}</span>
            </span>
          </label>
          {(() => {
            const held = heldNow.data ? new Set(heldNow.data.map((h) => h.symbol)) : null;
            const g = groupRows(rows, changes, held);
            const order: RowGroup[] = ["new", "changed"];
            const fx = summaryNow.data && summaryNow.data.value.usd > 0 ? summaryNow.data.value.ils / summaryNow.data.value.usd : null;
            const totals = heldNow.data ? updateTotals(rows, changes, heldNow.data, scope, fx) : null;
            return (
              <>
                {order.map((k) => g[k].length > 0 && (
                  <div key={k} className="space-y-1" role="group" aria-label={t(`groups.${k}.title`)}>
                    <h3 className="font-semibold">{t(`groups.${k}.title`)} ({g[k].length})</h3>
                    <p className="text-xs text-muted">{t(`groups.${k}.hint`)}</p>
                    {renderTable(draft, g[k])}
                  </div>
                ))}
                {g.unchanged.length > 0 && (
                  <details className="space-y-1" data-testid="group-unchanged">
                    <summary className="cursor-pointer font-semibold">{t("groups.unchanged.title")} ({g.unchanged.length})</summary>
                    <p className="text-xs text-muted">{t("groups.unchanged.hint")}</p>
                    {renderTable(draft, g.unchanged)}
                  </details>
                )}
                {totals && (
                  <dl className="grid grid-cols-2 gap-2 rounded-xl bg-surface-2 p-3 text-sm" aria-label={t("totals.title")} data-testid="update-totals">
                    <dt className="text-muted">{t("totals.before")}</dt>
                    <dd className="tabular-nums" dir="ltr">{formatMoney(totals.before, "ILS", locale, { compact: true })}</dd>
                    <dt className="text-muted">{t("totals.after")}</dt>
                    <dd className="tabular-nums" dir="ltr">{totals.after === null ? "—" : formatMoney(totals.after, "ILS", locale, { compact: true })}</dd>
                  </dl>
                )}
              </>
            );
          })()}
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
                      <select aria-label={`${t("col.change")} ${sym}`} className="input w-36" value={ch.type} onChange={(e) => editChange(match, { type: e.target.value as ProposedChangeType })}>
                        {(["keep", "sell", "withdrawal"] as const).map((x) => <option key={x} value={x}>{t(`vanished.type.${x}`)}</option>)}
                      </select>
                      {ch.type !== "keep" && <NumberCell
                        key={`gone-${sym}-${ch.type}`}
                        label={`${t("col.changeAmount")} ${sym}`} className="w-28"
                        value={isCash ? ch.amount : ch.quantity}
                        onValue={(n, ok) => { editChange(match, isCash ? { amount: n } : { quantity: n }); cell(`gone-${sym}`)(ok); }}
                      />}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
          {missingQuantity && <p role="status" className="text-sm text-amber-900 dark:text-amber-200">{t("notes.quantityRequiredHint")}</p>}
          {unconfirmedCurrency && <p role="status" className="text-sm text-amber-900 dark:text-amber-200">{t("notes.currencyRequiredHint")}</p>}
          {unackedConflict && <p role="status" className="text-sm text-amber-900 dark:text-amber-200">{t("notes.conflictRequiredHint")}</p>}
          {missingSymbol && <p role="status" className="text-sm text-amber-900 dark:text-amber-200">{t("error.unmatchedHint")}</p>}
          {bad.size > 0 && <p role="alert" className="text-sm text-loss">{t("error.badNumber")}</p>}
          {error && <p role="alert" className="text-sm text-loss">{errorText}</p>}
          {problemList}
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-primary" onClick={confirm} disabled={busy === "confirm" || bad.size > 0 || missingSymbol || missingQuantity || unackedConflict || unconfirmedCurrency || rows.length === 0}>
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
