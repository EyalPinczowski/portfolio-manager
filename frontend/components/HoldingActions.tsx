"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { mutate } from "swr";
import { api, ApiError, type Horizon, type Holding } from "@/lib/api";
import { parseLocaleNumber } from "@/lib/number";
import { MAX_QTY } from "@/lib/import-rows";
import { formatMoney, formatNumber, isTaseSymbol, toAgorot } from "@/lib/format";
import { Modal } from "./Modal";

const HORIZONS: Horizon[] = ["1w", "1m", "3m", "6m", "1y"];
type Problem = "quantity" | "cost" | "rate" | "notFound" | "generic" | null;
const fmt = (n: number | null | undefined) => (typeof n === "number" ? String(n) : "");

type Target = Pick<Holding, "id" | "quantity" | "horizon"> & {
  avg_cost?: number | null; cost_currency?: string | null; price?: number; currency?: string; symbol?: string;
};

/** Edit form for one holding (PATCH). Only the user's own action saves; the holding period has no default and may stay unset. */
function EditHoldingForm({ h, portfolioId, onClose, onSaved }: {
  h: Target; portfolioId: number; onClose: () => void; onSaved?: () => void;
}) {
  const t = useTranslations("holdingEdit");
  const ad = useTranslations("addHolding");
  const hz = useTranslations("holding");
  const c = useTranslations("common");
  const locale = useLocale();
  const [qty, setQty] = useState(fmt(h.quantity));
  const [cost, setCost] = useState(fmt(h.avg_cost));
  const [costCur, setCostCur] = useState<"" | "ILS" | "USD">(h.cost_currency === "ILS" || h.cost_currency === "USD" ? h.cost_currency : "");
  const [horizon, setHorizon] = useState<"" | Horizon>(h.horizon ?? "");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem>(null);

  const liveQty = parseLocaleNumber(qty);
  const hasPrice = typeof h.price === "number" && Number.isFinite(h.price) && h.price > 0 && !!h.currency;
  const tase = hasPrice && !!h.symbol && isTaseSymbol(h.symbol) && h.currency === "ILS";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const q = parseLocaleNumber(qty);
    const cst = cost.trim() === "" ? null : parseLocaleNumber(cost);
    if (q === null || !(q > 0) || q > MAX_QTY) return setProblem("quantity");
    if (cost.trim() !== "" && (cst === null || cst < 0 || cst > MAX_QTY)) return setProblem("cost");
    const body: Parameters<typeof api.patchHolding>[2] = { quantity: q, avg_cost: cst };
    if (cst !== null && costCur !== "") body.cost_currency = costCur;
    if ((horizon === "" ? null : horizon) !== (h.horizon ?? null)) body.horizon = horizon === "" ? null : horizon;
    setBusy(true); setProblem(null);
    try {
      await api.patchHolding(portfolioId, h.id, body);
      await mutate(() => true);
      onSaved?.();
      onClose();
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) setProblem("notFound");
      else if (err instanceof ApiError && err.status === 422) {
        const f = err.validation.map((d) => d.loc[d.loc.length - 1]);
        setProblem(f.includes("avg_cost") ? "cost" : "quantity");
      } else if (err instanceof ApiError && err.status === 429) setProblem("rate");
      else setProblem("generic");
    } finally { setBusy(false); }
  };

  return (
    <form onSubmit={submit} className="space-y-3" noValidate>
      <p className="text-sm text-muted">{t("intro")}</p>
      <div>
        <label htmlFor="he-qty" className="label">{ad("quantity")}</label>
        <input id="he-qty" className="input" dir="ltr" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} aria-invalid={problem === "quantity" || undefined} />
      </div>
      {hasPrice && (
        <div className="rounded-xl bg-surface-2 p-3 text-sm" data-testid="edit-computed-value">
          <p className="font-semibold tabular-nums" dir="ltr">
            {t("computedValue", { value: liveQty !== null && liveQty > 0 ? formatMoney(liveQty * (h.price as number), h.currency as string, locale) : "—" })}
          </p>
          <p className="text-caption text-muted tabular-nums" dir="ltr">
            {t("computedValueNote", { qty: liveQty !== null ? formatNumber(liveQty, locale, 4) : "—", price: formatMoney(h.price as number, h.currency as string, locale) })}
          </p>
          {tase && <p className="text-caption text-muted" data-testid="edit-price-unit">{t("priceUnitTase", { agorot: formatNumber(toAgorot(h.price as number), locale, 0) })}</p>}
        </div>
      )}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor="he-cost" className="label">{ad("cost")} <span className="text-muted">({c("optional")})</span></label>
          <input id="he-cost" className="input" dir="ltr" inputMode="decimal" value={cost} onChange={(e) => setCost(e.target.value)} aria-invalid={problem === "cost" || undefined} />
        </div>
        <div>
          <label htmlFor="he-cur" className="label">{ad("costCurrency")}</label>
          <select id="he-cur" className="input" value={costCur} onChange={(e) => setCostCur(e.target.value as "" | "ILS" | "USD")}>
            <option value="">{ad("costCurrencyAuto")}</option>
            <option value="ILS">ILS</option>
            <option value="USD">USD</option>
          </select>
        </div>
      </div>
      <p className="text-xs text-muted">{t("costHint")}</p>
      <div>
        <label htmlFor="he-horizon" className="label">{hz("horizonTitle")} <span className="text-muted">({c("optional")})</span></label>
        <select id="he-horizon" className="input" value={horizon} onChange={(e) => setHorizon(e.target.value as "" | Horizon)}>
          <option value="">{ad("horizonLater")}</option>
          {HORIZONS.map((x) => <option key={x} value={x}>{hz(`horizons.${x}`)}</option>)}
        </select>
      </div>
      {problem && <p role="alert" className="text-sm font-medium text-loss">{t(`error.${problem}`)}</p>}
      <div className="flex flex-wrap gap-2">
        <button type="submit" className="btn-primary" disabled={busy}>{busy ? t("saving") : t("save")}</button>
        <button type="button" className="btn-secondary" onClick={onClose}>{c("cancel")}</button>
      </div>
    </form>
  );
}

/** "Edit" and "Remove" for one holding, with no screenshot. Remove always asks first; nothing changes until the user confirms. */
export function HoldingActions({ h, portfolioId, name, onRemoved, menu = false }: {
  h: Target; portfolioId: number; name: string; onRemoved?: () => void; /** Show a small "more" menu instead of two buttons. */ menu?: boolean;
}) {
  const t = useTranslations("holdingEdit");
  const c = useTranslations("common");
  const [mode, setMode] = useState<"edit" | "remove" | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  const remove = async () => {
    setBusy(true); setFailed(false);
    try {
      await api.deleteHolding(portfolioId, h.id);
      await mutate(() => true);
      setMode(null);
      onRemoved?.();
    } catch { setFailed(true); }
    finally { setBusy(false); }
  };

  return (
    <>
      {menu ? (
        <div className="relative" onKeyDown={(e) => { if (e.key === "Escape") setMenuOpen(false); }}>
          <button type="button" className="btn-secondary min-h-11 min-w-11" aria-label={t("moreAria", { name })} aria-haspopup="true" aria-expanded={menuOpen} aria-controls={`holding-menu-${h.id}`} onClick={() => setMenuOpen((v) => !v)}>
            <span aria-hidden="true">⋯</span>
          </button>
          {menuOpen && (
            <div id={`holding-menu-${h.id}`} className="absolute end-0 z-20 mt-1 flex min-w-36 flex-col gap-1 rounded-xl border border-line bg-surface p-2 shadow-lg">
              <button type="button" className="btn-secondary" aria-label={t("editAria", { name })} onClick={() => { setMenuOpen(false); setMode("edit"); }}>{t("edit")}</button>
              <button type="button" className="btn-secondary" aria-label={t("removeAria", { name })} onClick={() => { setMenuOpen(false); setFailed(false); setMode("remove"); }}>{t("remove")}</button>
            </div>
          )}
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn-secondary" aria-label={t("editAria", { name })} onClick={() => setMode("edit")}>{t("edit")}</button>
          <button type="button" className="btn-secondary" aria-label={t("removeAria", { name })} onClick={() => { setFailed(false); setMode("remove"); }}>{t("remove")}</button>
        </div>
      )}
      {mode === "edit" && (
        <Modal title={t("editTitle", { name })} onClose={() => setMode(null)}>
          <EditHoldingForm h={h} portfolioId={portfolioId} onClose={() => setMode(null)} />
        </Modal>
      )}
      {mode === "remove" && (
        <Modal title={t("removeTitle", { name })} onClose={() => setMode(null)}>
          <div className="space-y-3">
            <p className="text-sm">{t("removeBody", { name })}</p>
            {failed && <p role="alert" className="text-sm font-medium text-loss">{t("removeError")}</p>}
            <div className="flex flex-wrap gap-2">
              <button type="button" className="btn-primary" disabled={busy} onClick={remove}>{busy ? t("removing") : t("removeConfirm")}</button>
              <button type="button" className="btn-secondary" onClick={() => setMode(null)}>{c("cancel")}</button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
