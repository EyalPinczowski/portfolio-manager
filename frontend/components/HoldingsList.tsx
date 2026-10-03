"use client";
import { useLocale, useTranslations } from "next-intl";
import type { Holding } from "@/lib/api";
import { formatMoney, formatNumber, formatPct } from "@/lib/format";
import { Link } from "@/i18n/navigation";
import { PnlText } from "./Pnl";

function ScoreBar({ score, label }: { score: number; label: string }) {
  const w = Math.min(100, Math.abs(score)) / 2;
  const pos = score >= 0;
  return (
    <span className="inline-flex items-center gap-2" role="img" aria-label={label}>
      <span className="relative h-2 w-20 rounded bg-slate-200 dark:bg-slate-700" dir="ltr">
        <span className="absolute inset-y-0 left-1/2 w-px bg-slate-500" />
        <span
          className={`absolute inset-y-0 rounded ${pos ? "bg-emerald-600" : "bg-red-600"}`}
          style={pos ? { left: "50%", width: `${w}%` } : { right: "50%", width: `${w}%` }}
        />
      </span>
      <span className="w-9 text-xs tabular-nums" dir="ltr">{score > 0 ? "+" : ""}{score}</span>
    </span>
  );
}

function StatusChip({ h }: { h: Holding }) {
  const t = useTranslations("holdings");
  const needs = h.stop_tp_status === "needs_horizon";
  return (
    <span className={`chip ${needs ? "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200" : "bg-slate-200 text-slate-800 dark:bg-slate-700 dark:text-slate-100"}`}>
      {needs ? t("needsHorizon") : t("stopMissing")}
    </span>
  );
}

export function HoldingsList({ holdings }: { holdings: Holding[] }) {
  const t = useTranslations("holdings");
  const locale = useLocale();
  const name = (h: Holding) => (locale === "he" ? h.name_he : h.name_en);
  const price = (h: Holding) => formatMoney(h.price, h.currency, locale);
  const score = (h: Holding) => t("scoreValue", { score: h.score_card.total });
  return (
    <section aria-label={t("title")}>
      <h2 className="mb-2 text-lg font-bold">{t("title")}</h2>
      {/* Mobile cards */}
      <ul className="space-y-2 md:hidden">
        {holdings.map((h) => (
          <li key={h.id}>
            <Link href={`/holding/${h.id}`} aria-label={t("open", { name: name(h) })} className="card block space-y-2 hover:border-blue-400">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="font-semibold">{name(h)}</p>
                  <p className="text-xs text-slate-600 dark:text-slate-400" dir="ltr">{h.symbol}</p>
                </div>
                <div className="text-end">
                  <p className="tabular-nums" dir="ltr">{price(h)}</p>
                  <PnlText pct={h.day_change_pct} locale={locale} className="text-sm" />
                </div>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span><span className="text-slate-600 dark:text-slate-400">{t("pnl")}: </span><PnlText value={h.pnl.ils} pct={h.pnl.pct} currency="ILS" locale={locale} /></span>
                <span className="tabular-nums" dir="ltr">{formatNumber(h.weight_pct, locale, 1)}%</span>
              </div>
              <div className="flex items-center justify-between">
                <StatusChip h={h} />
                <ScoreBar score={h.score_card.total} label={score(h)} />
              </div>
            </Link>
          </li>
        ))}
      </ul>
      {/* Desktop table */}
      <div className="card hidden overflow-x-auto p-0 md:block">
        <table className="w-full text-sm">
          <thead className="bg-slate-100 text-start dark:bg-slate-800">
            <tr>
              {(["name", "price", "dayChange", "pnl", "weight", "status", "score"] as const).map((c) => (
                <th key={c} scope="col" className="px-3 py-2 text-start font-semibold">{t(c)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {holdings.map((h) => (
              <tr key={h.id} className="border-t border-slate-200 hover:bg-slate-50 dark:border-slate-800 dark:hover:bg-slate-800/50">
                <td className="px-3 py-2">
                  <Link href={`/holding/${h.id}`} className="font-semibold text-blue-800 hover:underline dark:text-blue-300" aria-label={t("open", { name: name(h) })}>{name(h)}</Link>
                  <div className="text-xs text-slate-600 dark:text-slate-400" dir="ltr">{h.symbol}</div>
                </td>
                <td className="px-3 py-2 tabular-nums" dir="ltr">{price(h)}</td>
                <td className="px-3 py-2"><PnlText pct={h.day_change_pct} locale={locale} /></td>
                <td className="px-3 py-2"><PnlText value={h.pnl.ils} pct={h.pnl.pct} currency="ILS" locale={locale} /></td>
                <td className="px-3 py-2 tabular-nums" dir="ltr">{formatPct(h.weight_pct, locale)}</td>
                <td className="px-3 py-2"><StatusChip h={h} /></td>
                <td className="px-3 py-2"><ScoreBar score={h.score_card.total} label={score(h)} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
