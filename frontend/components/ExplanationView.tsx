"use client";
import { useLocale, useTranslations } from "next-intl";
import type { Explanation } from "@/lib/api";
import { DASH, formatDate, formatMoney, formatTime, formatWeight } from "@/lib/format";

const stamp = (iso: string, locale: string) => `${formatDate(iso)} ${formatTime(iso, locale)}`;

/**
 * The typed "Why?" (docs: Explanation). Every field except `summary` may be missing (score cards cached before
 * v1), so each section renders only when it has content. Shows facts and reasons only, never a verdict.
 */
export function ExplanationView({ e, reasons, asOf, currency, showContributions = true }: {
  e: Explanation;
  reasons?: string[];
  asOf?: string;
  currency?: string;
  showContributions?: boolean;
}) {
  const t = useTranslations("holding");
  const locale = useLocale();
  const inputs = Object.entries(e.inputs ?? {});
  const contributions = showContributions ? (e.contributions ?? []) : [];
  const annotations = e.annotations ?? [];
  const rules = e.rules_applied ?? [];
  const riskRules = e.risk_rules_applied ?? [];
  const risks = e.invalidation_risks ?? [];
  const sources = e.sources ?? [];
  const time = asOf ?? e.as_of ?? undefined;
  const h = "font-semibold";
  const muted = "text-slate-600 dark:text-slate-400";
  return (
    <div className="space-y-3 text-sm" data-testid="explanation">
      <div>
        <h4 className={h}>{t("whySummary")}</h4>
        <p>{e.summary || DASH}</p>
      </div>
      {reasons && reasons.length > 0 && (
        <div>
          <h4 className={h}>{t("whyReasons")}</h4>
          <ul className="list-disc ps-5">{reasons.map((r) => <li key={r}>{r}</li>)}</ul>
        </div>
      )}
      {contributions.length > 0 && (
        <div>
          <h4 className={h}>{t("whyContributions")}</h4>
          <div className="overflow-x-auto">
            <table className="w-full text-start">
              <thead className={`text-xs ${muted}`}>
                <tr>
                  <th scope="col" className="py-1 pe-2 text-start font-medium">{t("colSignal")}</th>
                  <th scope="col" className="px-2 text-start font-medium">{t("colScore")}</th>
                  <th scope="col" className="px-2 text-start font-medium">{t("colWeight")}</th>
                  <th scope="col" className="px-2 text-start font-medium">{t("colConfidence")}</th>
                  <th scope="col" className="ps-2 text-start font-medium"><span className="sr-only">{t("colBar")}</span></th>
                </tr>
              </thead>
              <tbody>
                {contributions.map((c) => {
                  const none = c.confidence === 0;
                  const pct = Math.min(100, Math.max(0, Math.abs(c.score)));
                  return (
                    <tr key={c.name} className="border-t border-slate-200 dark:border-slate-700">
                      <th scope="row" className="py-1 pe-2 text-start font-normal">{t.has(`signal.${c.name}`) ? t(`signal.${c.name}`) : c.name}</th>
                      <td className="px-2 tabular-nums" dir="ltr">{none ? DASH : `${c.score > 0 ? "+" : ""}${c.score}`}</td>
                      <td className="px-2 tabular-nums" dir="ltr">{formatWeight(c.weight, locale, 0)}</td>
                      <td className="px-2 tabular-nums" dir="ltr">{formatWeight(c.confidence * 100, locale, 0)}</td>
                      <td className="ps-2">
                        <div className="h-2 w-24 overflow-hidden rounded bg-slate-200 dark:bg-slate-700" role="presentation">
                          {!none && <div data-testid="contribution-bar" className={`h-full ${c.score >= 0 ? "bg-emerald-600" : "bg-red-600"}`} style={{ width: `${pct}%` }} />}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {inputs.length > 0 && (
        <div>
          <h4 className={h}>{t("whyInputs")}</h4>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
            {inputs.map(([k, v]) => (
              <div key={k} className="contents">
                <dt className={muted}>{k}</dt>
                <dd className="tabular-nums" dir="ltr">{String(v)}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}
      {annotations.length > 0 && (
        <div>
          <h4 className={h}>{t("whyAnnotations")}</h4>
          <ul className="list-disc ps-5">
            {annotations.map((a, i) => (
              <li key={`${a.kind}-${a.label}-${i}`}>
                <span className="font-medium">{t(`annotationKinds.${a.kind}`)}</span>: {a.label}
                {a.price != null && <> · <span dir="ltr">{currency ? formatMoney(a.price, currency, locale) : a.price}</span></>}
                {a.as_of && <span className={muted}> · {formatDate(a.as_of)}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {rules.length > 0 && (
        <div>
          <h4 className={h}>{t("whyRules")}</h4>
          <ul className="list-disc ps-5">{rules.map((r) => <li key={r}>{r}</li>)}</ul>
        </div>
      )}
      {riskRules.length > 0 && (
        <div>
          <h4 className={h}>{t("whyRiskRules")}</h4>
          <ul className="list-disc ps-5">{riskRules.map((r) => <li key={r}>{r}</li>)}</ul>
        </div>
      )}
      {risks.length > 0 && (
        <div>
          <h4 className={h}>{t("whyInvalidation")}</h4>
          <ul className="list-disc ps-5">{risks.map((r) => <li key={r}>{r}</li>)}</ul>
        </div>
      )}
      {sources.length > 0 && (
        <div>
          <h4 className={h}>{t("whySources")}</h4>
          <ul className="list-disc ps-5">
            {sources.map((s, i) => (
              <li key={`${s.name}-${i}`}>
                {s.name}
                {s.detail && <span className={muted}> ({s.detail})</span>}
                {s.as_of && <span className={muted}> · {t("asOf", { time: stamp(s.as_of, locale) })}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {time && <p className={`text-xs ${muted}`}>{t("asOf", { time: stamp(time, locale) })}</p>}
    </div>
  );
}
