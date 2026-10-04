"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";
import { mutate } from "swr";
import { api, ApiError, type Horizon, type HoldingCreate, type Portfolio } from "@/lib/api";
import { parseLocaleNumber } from "@/lib/number";
import { MAX_QTY } from "@/lib/import-rows";
import { Modal } from "./Modal";
import { FundAddFlow } from "./FundAddFlow";

const HORIZONS: Horizon[] = ["1w", "1m", "3m", "6m", "1y"];
const SYMBOL_RE = /^[A-Z0-9.^=-]{1,20}$/;
type Problem = "symbol" | "quantity" | "cost" | "duplicate" | "rate" | "generic" | null;

/**
 * Manual entry of one holding (POST /portfolios/{id}/holdings), for a holding that has no screenshot. The holding
 * period is optional and has NO default: "Decide later" sends null and the app keeps asking (never guessed).
 */
export function AddHoldingForm({ portfolios, portfolioId, onClose, onAdded }: {
  portfolios: Portfolio[]; portfolioId?: number | null; onClose: () => void; onAdded?: () => void;
}) {
  const t = useTranslations("addHolding");
  const hz = useTranslations("holding");
  const c = useTranslations("common");
  const [pid, setPid] = useState<number>(portfolioId ?? portfolios[0]?.id ?? 0);
  const [symbol, setSymbol] = useState("");
  const [qty, setQty] = useState("");
  const [cost, setCost] = useState("");
  const [costCur, setCostCur] = useState<"" | "ILS" | "USD">("");
  const [horizon, setHorizon] = useState<"" | Horizon>("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem>(null);
  const [mode, setMode] = useState<"stock" | "fund">("stock");

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const sym = symbol.trim().toUpperCase();
    const q = parseLocaleNumber(qty);
    const cst = cost.trim() === "" ? null : parseLocaleNumber(cost);
    if (!SYMBOL_RE.test(sym)) return setProblem("symbol");
    if (q === null || !(q > 0) || q > MAX_QTY) return setProblem("quantity");
    if (cst !== null && (cst < 0 || cst > MAX_QTY)) return setProblem("cost");
    if (cost.trim() !== "" && cst === null) return setProblem("cost");
    const body: HoldingCreate = {
      symbol: sym, quantity: q,
      avg_cost: cst, cost_currency: cst !== null && costCur !== "" ? costCur : null,
      horizon: horizon === "" ? null : horizon,
    };
    setBusy(true); setProblem(null);
    try {
      await api.addHolding(pid, body);
      await mutate(() => true);
      onAdded?.();
      onClose();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setProblem("duplicate");
      else if (err instanceof ApiError && err.status === 422) {
        const f = err.validation.map((d) => d.loc[d.loc.length - 1]);
        setProblem(f.includes("quantity") ? "quantity" : f.includes("avg_cost") ? "cost" : "symbol");
      } else if (err instanceof ApiError && err.status === 429) setProblem("rate");
      else setProblem("generic");
    } finally { setBusy(false); }
  };

  if (mode === "fund") {
    return (
      <Modal title={t("fundTitle")} onClose={onClose}>
        <FundAddFlow portfolios={portfolios} pid={pid} onPid={setPid} onClose={onClose} onAdded={onAdded} />
        <button type="button" className="btn-secondary" onClick={() => setMode("stock")}>{t("backToStock")}</button>
      </Modal>
    );
  }
  return (
    <Modal title={t("title")} onClose={onClose}>
      <button type="button" className="btn-secondary w-full" onClick={() => setMode("fund")}>{t("fundOpen")}</button>
      <form onSubmit={submit} className="space-y-3" noValidate>
        <p className="text-sm text-muted">{t("intro")}</p>
        {portfolios.length > 1 && (
          <div>
            <label htmlFor="ah-portfolio" className="label">{t("portfolio")}</label>
            <select id="ah-portfolio" className="input" value={pid} onChange={(e) => setPid(Number(e.target.value))}>
              {portfolios.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
          </div>
        )}
        <div>
          <label htmlFor="ah-symbol" className="label">{t("symbol")}</label>
          <input id="ah-symbol" className="input" dir="ltr" autoCapitalize="characters" maxLength={20} value={symbol} onChange={(e) => setSymbol(e.target.value)} aria-invalid={problem === "symbol" || undefined} />
          <p className="mt-1 text-xs text-muted">{t("symbolHint")}</p>
        </div>
        <div>
          <label htmlFor="ah-qty" className="label">{t("quantity")}</label>
          <input id="ah-qty" className="input" dir="ltr" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} aria-invalid={problem === "quantity" || undefined} />
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label htmlFor="ah-cost" className="label">{t("cost")} <span className="text-muted">({c("optional")})</span></label>
            <input id="ah-cost" className="input" dir="ltr" inputMode="decimal" value={cost} onChange={(e) => setCost(e.target.value)} aria-invalid={problem === "cost" || undefined} />
          </div>
          <div>
            <label htmlFor="ah-cur" className="label">{t("costCurrency")}</label>
            <select id="ah-cur" className="input" value={costCur} onChange={(e) => setCostCur(e.target.value as "" | "ILS" | "USD")}>
              <option value="">{t("costCurrencyAuto")}</option>
              <option value="ILS">ILS</option>
              <option value="USD">USD</option>
            </select>
          </div>
        </div>
        <p className="text-xs text-muted">{t("costHint")}</p>
        <div>
          <label htmlFor="ah-horizon" className="label">{hz("horizonTitle")} <span className="text-muted">({c("optional")})</span></label>
          <select id="ah-horizon" className="input" value={horizon} onChange={(e) => setHorizon(e.target.value as "" | Horizon)}>
            <option value="">{t("horizonLater")}</option>
            {HORIZONS.map((x) => <option key={x} value={x}>{hz(`horizons.${x}`)}</option>)}
          </select>
        </div>
        {problem && <p role="alert" className="text-sm font-medium text-loss">{t(`error.${problem}`)}</p>}
        <div className="flex flex-wrap gap-2">
          <button type="submit" className="btn-primary" disabled={busy}>{busy ? t("adding") : t("add")}</button>
          <button type="button" className="btn-secondary" onClick={onClose}>{c("cancel")}</button>
        </div>
      </form>
    </Modal>
  );
}
