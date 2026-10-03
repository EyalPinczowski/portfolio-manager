"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";
import { api, type ChangeType, type ImportDraft, type ImportRow, type ProposedChange } from "@/lib/api";
import { useMe, usePortfolios } from "@/lib/hooks";
import { Link, useRouter } from "@/i18n/navigation";
import { mutate } from "swr";
import { AppShell } from "./AppShell";
import { Modal } from "./Modal";

const TYPES: ChangeType[] = ["buy", "sell", "deposit", "withdrawal"];

function Body() {
  const t = useTranslations("import");
  const c = useTranslations("common");
  const router = useRouter();
  const { data: me, mutate: mutateMe } = useMe();
  const { data: portfolios } = usePortfolios();
  const [pid, setPid] = useState<number | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [declined, setDeclined] = useState(false);
  const [draft, setDraft] = useState<ImportDraft | null>(null);
  const [rows, setRows] = useState<ImportRow[]>([]);
  const [changes, setChanges] = useState<ProposedChange[]>([]);
  const [busy, setBusy] = useState<"upload" | "confirm" | null>(null);
  const [error, setError] = useState(false);
  const [done, setDone] = useState(false);

  const portfolioId = pid ?? portfolios?.[0]?.id ?? null;
  const needsConsent = !!me && !me.ocr_consent && !declined;

  const accept = async () => { await api.consentOcr(); await mutateMe(); };

  const upload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file || portfolioId === null) return;
    setBusy("upload"); setError(false);
    try {
      const d = await api.createImport(portfolioId, file);
      setDraft(d); setRows(d.rows); setChanges(d.proposed_changes);
    } catch { setError(true); } finally { setBusy(null); }
  };

  const confirm = async () => {
    if (!draft) return;
    setBusy("confirm"); setError(false);
    try {
      await api.patchImport(draft.id, { rows, proposed_changes: changes });
      await api.confirmImport(draft.id);
      await mutate(() => true);
      setDone(true);
    } catch { setError(true); } finally { setBusy(null); }
  };

  const editRow = (i: number, patch: Partial<ImportRow>) =>
    setRows((rs) => rs.map((r) => (r.index === i ? { ...r, ...patch } : r)));
  const editChange = (i: number, patch: Partial<ProposedChange>) =>
    setChanges((cs) => cs.map((x) => (x.row_index === i ? { ...x, ...patch } : x)));
  const num = (v: string): number | null => (v.trim() === "" || Number.isNaN(Number(v)) ? null : Number(v));

  if (needsConsent) {
    return (
      <Modal title={t("consentTitle")}>
        <p className="text-sm">{t("consentBody")}</p>
        <div className="flex gap-2">
          <button type="button" className="btn-primary" onClick={accept}>{t("consentAccept")}</button>
          <button type="button" className="btn-secondary" onClick={() => { setDeclined(true); router.replace("/"); }}>{t("consentDecline")}</button>
        </div>
      </Modal>
    );
  }

  if (done) {
    return (
      <div className="card space-y-3 text-center">
        <p role="status" className="text-lg font-semibold">{t("done")}</p>
        <Link href="/" className="btn-primary">{t("backHome")}</Link>
      </div>
    );
  }

  return (
    <>
      <h1 className="text-2xl font-bold">{t("title")}</h1>
      {!draft && (
        <form onSubmit={upload} className="card space-y-4">
          <p className="text-sm text-slate-700 dark:text-slate-300">{t("intro")}</p>
          <div>
            <label htmlFor="imp-portfolio" className="label">{t("portfolio")}</label>
            <select id="imp-portfolio" className="input" value={portfolioId ?? ""} onChange={(e) => setPid(Number(e.target.value))}>
              {(portfolios ?? []).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="imp-file" className="label">{t("chooseFile")}</label>
            <input id="imp-file" className="input" type="file" accept="image/*" required onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          </div>
          <button type="submit" className="btn-primary" disabled={!file || busy === "upload"}>
            {busy === "upload" ? t("uploading") : t("upload")}
          </button>
        </form>
      )}
      {draft && (
        <section className="card space-y-3" aria-label={t("reviewTitle")}>
          <h2 className="text-lg font-bold">{t("reviewTitle")}</h2>
          <p className="text-sm text-slate-700 dark:text-slate-300">{t("reviewHint")}</p>
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
                  const id = (f: string) => `r${r.index}-${f}`;
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
                      <td className="px-2 py-2"><input id={id("sym")} aria-label={`${t("col.symbol")} ${r.index + 1}`} className="input w-28" dir="ltr" value={r.symbol ?? ""} onChange={(e) => editRow(r.index, { symbol: e.target.value || null })} /></td>
                      <td className="px-2 py-2"><input aria-label={`${t("col.quantity")} ${r.index + 1}`} className="input w-24" dir="ltr" inputMode="decimal" value={r.quantity ?? ""} onChange={(e) => editRow(r.index, { quantity: num(e.target.value) })} /></td>
                      <td className="px-2 py-2"><input aria-label={`${t("col.price")} ${r.index + 1}`} className="input w-24" dir="ltr" inputMode="decimal" value={r.price ?? ""} onChange={(e) => editRow(r.index, { price: num(e.target.value) })} /></td>
                      <td className="px-2 py-2"><input aria-label={`${t("col.value")} ${r.index + 1}`} className="input w-28" dir="ltr" inputMode="decimal" value={r.value ?? ""} onChange={(e) => editRow(r.index, { value: num(e.target.value) })} /></td>
                      <td className="px-2 py-2 tabular-nums" dir="ltr">{r.currency}{r.unit === "agorot" ? " (ag.)" : ""}</td>
                      <td className="px-2 py-2">
                        {ch && (
                          <select aria-label={`${t("col.change")} ${r.index + 1}`} className="input w-32" value={ch.type} onChange={(e) => editChange(r.index, { type: e.target.value as ChangeType })}>
                            {TYPES.map((k) => <option key={k} value={k}>{t(`changeType.${k}`)}</option>)}
                          </select>
                        )}
                      </td>
                      <td className="px-2 py-2">
                        {ch && (
                          <input
                            aria-label={`${t("col.changeAmount")} ${r.index + 1}`}
                            className="input w-28" dir="ltr" inputMode="decimal"
                            value={(ch.type === "deposit" || ch.type === "withdrawal" ? ch.amount : ch.quantity) ?? ""}
                            onChange={(e) => editChange(r.index, ch.type === "deposit" || ch.type === "withdrawal" ? { amount: num(e.target.value) } : { quantity: num(e.target.value) })}
                          />
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {error && <p role="alert" className="text-sm text-red-700 dark:text-red-400">{c("errorLoad")}</p>}
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-primary" onClick={confirm} disabled={busy === "confirm"}>
              {busy === "confirm" ? t("confirming") : t("confirm")}
            </button>
            <button type="button" className="btn-secondary" onClick={() => { setDraft(null); setFile(null); }}>{c("cancel")}</button>
          </div>
        </section>
      )}
      {!draft && error && <p role="alert" className="text-sm text-red-700 dark:text-red-400">{c("errorLoad")}</p>}
    </>
  );
}

export function ImportPage() {
  return <AppShell><Body /></AppShell>;
}
