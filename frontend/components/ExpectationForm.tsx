"use client";
import { useState, type FormEvent } from "react";
import { useLocale, useTranslations } from "next-intl";
import { api, ApiError, type Portfolio } from "@/lib/api";
import { formatNumber } from "@/lib/format";
import { parseLocaleNumber } from "@/lib/number";

const PCT = { min: -100, max: 500 };
const MONTHS = { min: 1, max: 120 };

/**
 * "Expected return": a percentage and a horizon in months. Both are required together (or both cleared).
 * Nothing is ever prefilled except what the user saved earlier.
 */
export function ExpectationForm({ portfolio, onSaved }: { portfolio: Portfolio; onSaved: () => void }) {
  const t = useTranslations("postmortem");
  const locale = useLocale();
  const set = portfolio.expected_return_pct != null && portfolio.expected_return_horizon_months != null;
  const [pct, setPct] = useState(set ? String(portfolio.expected_return_pct) : "");
  const [months, setMonths] = useState(set ? String(portfolio.expected_return_horizon_months) : "");
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const p = parseLocaleNumber(pct);
  const m = parseLocaleNumber(months);
  const anyFilled = pct.trim() !== "" || months.trim() !== "";
  const bothFilled = pct.trim() !== "" && months.trim() !== "";
  const inRange = p !== null && m !== null && p >= PCT.min && p <= PCT.max && Number.isInteger(m) && m >= MONTHS.min && m <= MONTHS.max;
  const hint = anyFilled && !bothFilled ? t("expPair") : bothFilled && !inRange ? t("expRange") : null;

  const send = async (body: { expected_return_pct: number | null; expected_return_horizon_months: number | null }) => {
    setBusy(true);
    setFailed(false);
    try {
      await api.patchPortfolio(portfolio.id, body);
      if (body.expected_return_pct === null) { setPct(""); setMonths(""); }
      onSaved();
    } catch (e) {
      if (!(e instanceof ApiError)) throw e;
      setFailed(true);
    } finally {
      setBusy(false);
    }
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (inRange) void send({ expected_return_pct: p as number, expected_return_horizon_months: m as number });
  };

  return (
    <form onSubmit={submit} className="space-y-3" aria-label={t("expTitle")}>
      <p className="text-sm text-muted">{t("expIntro")}</p>
      {set && <p className="text-sm" data-testid="exp-current">{t("expCurrent", { pct: `${formatNumber(portfolio.expected_return_pct as number, locale)}%`, months: portfolio.expected_return_horizon_months as number })}</p>}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label htmlFor="exp-pct" className="label">{t("expPct")}</label>
          <input id="exp-pct" className="input" inputMode="decimal" dir="ltr" value={pct} onChange={(e) => setPct(e.target.value)} />
        </div>
        <div>
          <label htmlFor="exp-months" className="label">{t("expMonths")}</label>
          <input id="exp-months" className="input" inputMode="numeric" dir="ltr" value={months} onChange={(e) => setMonths(e.target.value)} />
        </div>
      </div>
      {hint && <p className="text-sm text-warn-fg" role="status">{hint}</p>}
      {failed && <p role="alert" className="text-sm text-loss">{t("expSaveError")}</p>}
      <div className="flex flex-wrap gap-2">
        <button type="submit" className="btn-primary" disabled={busy || !inRange}>{t("expSave")}</button>
        <button type="button" className="btn-secondary" disabled={busy || (!set && !anyFilled)} onClick={() => (set ? void send({ expected_return_pct: null, expected_return_horizon_months: null }) : (setPct(""), setMonths("")))}>{t("expClear")}</button>
      </div>
    </form>
  );
}
