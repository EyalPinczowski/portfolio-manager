"use client";
import { useLocale, useTranslations } from "next-intl";
import type { ExitLevel, ExitLevelsResult } from "@/lib/api";
import { DASH, formatMoney, formatNumber } from "@/lib/format";
import { PnlText } from "./Pnl";

const finite = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);

type Row =
  | { key: string; kind: "stop" | "trailing_stop" | "breakeven" | "take_profit"; lv: ExitLevel; n?: number }
  | { key: string; kind: "entry" | "price"; price: number };

/** One compact table of every level: stop, trailing, break-even, entry, price, take-profits. No per-level cards. */
export function LevelLadder({ r, entry, currency }: { r: ExitLevelsResult; entry?: number | null; currency?: string }) {
  const t = useTranslations("exit");
  const locale = useLocale();
  const cur = currency ?? r.currency ?? "ILS";
  const rows: Row[] = [];
  for (const k of ["stop", "trailing_stop", "breakeven"] as const) {
    const lv = r[k];
    if (lv) rows.push({ key: k, kind: k, lv });
  }
  if (finite(entry)) rows.push({ key: "entry", kind: "entry", price: entry });
  if (finite(r.price)) rows.push({ key: "price", kind: "price", price: r.price });
  (r.take_profits ?? []).forEach((lv, i) => rows.push({ key: `tp${i}`, kind: "take_profit", lv, n: i + 1 }));
  if (rows.length === 0) return null;
  const px = r.price;
  const from = (price: number) => (finite(px) && px > 0 ? ((price - px) / px) * 100 : null);

  return (
    <div className="overflow-x-auto" data-testid="level-ladder">
      <table className="w-full text-start text-sm">
        <caption className="sr-only">{t("ladderTitle")}</caption>
        <thead className="text-caption text-muted">
          <tr>
            <th scope="col" className="py-1 pe-2 text-start font-medium">{t("colLevel")}</th>
            <th scope="col" className="px-1 text-start font-medium">{t("colPrice")}</th>
            <th scope="col" className="px-1 text-start font-medium">{t("colFrom")}</th>
            <th scope="col" className="ps-1 text-start font-medium">{t("colPnl")}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            if (!("lv" in row)) {
              const label = row.kind === "entry" ? t("entryLevel") : t("priceLevel");
              const d = row.kind === "entry" ? from(row.price) : null;
              return (
                <tr key={row.key} className={`border-t border-line ${row.kind === "price" ? "bg-surface-2 font-semibold" : ""}`} data-testid={`level-${row.kind}`}>
                  <th scope="row" className="py-1.5 pe-2 text-start font-normal">{label}</th>
                  <td className="px-1 tabular-nums" dir="ltr">{formatMoney(row.price, cur, locale)}</td>
                  <td className="px-1">{d !== null ? <PnlText pct={d} locale={locale} /> : DASH}</td>
                  <td className="ps-1 text-muted">{DASH}</td>
                </tr>
              );
            }
            const lv = row.lv;
            const hasPnl = finite(lv.pnl_ils);
            return (
              <tr key={row.key} className="border-t border-line align-top" data-testid={`level-${row.kind}`}>
                <th scope="row" className="py-1.5 pe-2 text-start font-normal">
                  {row.kind === "take_profit" ? t("tpN", { n: row.n ?? 1 }) : t(`kind.${row.kind}`)}
                  {lv.reached && <span className="chip-warn ms-1">{t("reached")}</span>}
                  {row.kind === "take_profit" && finite(lv.rr) && <span className="block text-caption text-muted" dir="ltr">{t("rr")} 1:{formatNumber(lv.rr, locale, 2)}</span>}
                </th>
                <td className="px-1 tabular-nums" dir="ltr">{formatMoney(lv.price, cur, locale)}</td>
                <td className="px-1"><PnlText pct={lv.distance_pct} locale={locale} /></td>
                <td className="ps-1">
                  {hasPnl
                    ? <PnlText value={lv.pnl_ils} currency="ILS" locale={locale} />
                    : <span className="text-muted" title={t("pnlUnknown")}>{DASH}<span className="sr-only"> {t("pnlUnknown")}</span></span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
