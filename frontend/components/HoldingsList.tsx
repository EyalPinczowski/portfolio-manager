"use client";
import { useLocale, useTranslations } from "next-intl";
import type { Holding } from "@/lib/api";
import { DASH, formatDate, formatMoney, formatWeight } from "@/lib/format";
import { holdingHref } from "@/lib/routes";
import { Link } from "@/i18n/navigation";
import { ChevronIcon } from "./icons";
import { PnlText } from "./Pnl";

const finite = (n: number | null | undefined): n is number => typeof n === "number" && Number.isFinite(n);

/** Status chips of a holding: holding period, stop, stale price, no data. Neutral colours, amber for "needs action". */
export function StatusChips({ h }: { h: Holding }) {
  const t = useTranslations("holdings");
  const noData = h.score_card?.confidence === 0 || !finite(h.price) || h.price <= 0;
  if (h.fund) {
    const basis = h.fund.value_basis;
    return (
      <ul className="flex flex-wrap gap-1.5" aria-label={t("status")}>
        <li className="chip-neutral">{t("fundChip")}</li>
        {basis === "manual_value" && <li className="chip-brand">{t("manualChip")}</li>}
        {basis === "cost_only" && <li className="chip-warn">{t("costOnlyChip")}</li>}
        {basis === "no_value" && <li className="chip-warn">{t("noValueChip")}</li>}
        {h.price_stale && <li className="chip-warn" title={t("fundStaleHint")}>{t("stale")}</li>}
      </ul>
    );
  }
  const needsHorizon = h.stop_tp_status === "needs_horizon";
  const stopMissing = h.stop_tp_status === "missing";
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label={t("status")}>
      {needsHorizon && <li className="chip-warn">{t("needsHorizon")}</li>}
      {stopMissing && <li className="chip-neutral">{t("stopMissing")}</li>}
      {!needsHorizon && !stopMissing && <li className="chip-brand">{t("stopSet")}</li>}
      {h.price_stale && <li className="chip-warn" title={t("staleHint")}>{t("stale")}</li>}
      {noData && <li className="chip-neutral">{t("noData")}</li>}
    </ul>
  );
}

/** A fund has no unit price: show the value the user entered and the date it refers to, never a made-up price. */
function FundValue({ h }: { h: Holding }) {
  const t = useTranslations("holdings");
  const locale = useLocale();
  const f = h.fund;
  if (!f) return null;
  return (
    <div className="shrink-0 text-end" data-testid="fund-value">
      {f.value_basis === "manual_value" && finite(f.manual_value_ils) ? (
        <>
          <p className="font-semibold tabular-nums" dir="ltr">{formatMoney(f.manual_value_ils, "ILS", locale)}</p>
          <p className="text-caption text-muted">{t("manualAsOf", { date: formatDate(f.manual_value_as_of) })}</p>
        </>
      ) : (
        <p className="text-sm text-muted">{DASH}<span className="block text-caption">{f.value_basis === "cost_only" ? t("fundCostOnly") : t("fundNoValue")}</span></p>
      )}
      {f.track && <p className="text-caption text-muted"><bdi dir="auto">{f.track}</bdi></p>}
    </div>
  );
}

export function HoldingCard({ h }: { h: Holding }) {
  const t = useTranslations("holdings");
  const locale = useLocale();
  const name = locale === "he" ? h.name_he : h.name_en;
  const hasPrice = finite(h.price) && h.price > 0;
  const hasPnl = !!h.pnl && (finite(h.pnl.ils) || finite(h.pnl.pct));
  return (
    <Link
      href={holdingHref(h.id)}
      aria-label={t("open", { name })}
      className="card flex h-full flex-col gap-2 hover:border-brand"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="break-words font-semibold">{name}</p>
          <p className="text-caption text-muted" dir="ltr">{h.symbol}</p>
        </div>
        {h.fund ? <FundValue h={h} /> : (
          <div className="shrink-0 text-end">
            <p className="font-semibold tabular-nums" dir="ltr">{hasPrice ? formatMoney(h.price, h.currency, locale) : DASH}</p>
            <p className="text-sm"><PnlText pct={h.day_change_pct} locale={locale} /> <span className="text-caption text-muted">{t("today")}</span></p>
          </div>
        )}
      </div>
      <div className="flex items-center justify-between gap-3 border-t border-line pt-2 text-sm">
        <span className="text-muted">{t("pnl")}</span>
        {hasPnl ? (
          <PnlText value={h.pnl?.ils} pct={h.pnl?.pct} currency="ILS" locale={locale} className="font-semibold" />
        ) : (
          <span className="text-end">
            <span className="text-muted">{DASH}<span className="sr-only"> {t("pnlUnavailable")}</span></span>
            <span className="block text-caption text-muted">{t("pnlHint")}</span>
          </span>
        )}
      </div>
      <div className="mt-auto flex items-end justify-between gap-2">
        <StatusChips h={h} />
        <span className="flex shrink-0 items-center gap-1 text-caption text-muted">
          {finite(h.weight_pct) && <span className="tabular-nums" dir="ltr" title={t("weight")}>{formatWeight(h.weight_pct, locale)}</span>}
          <ChevronIcon className="h-4 w-4" />
        </span>
      </div>
    </Link>
  );
}

export function HoldingsList({ holdings }: { holdings: Holding[] }) {
  const t = useTranslations("holdings");
  return (
    <section aria-label={t("title")}>
      <h2 className="mb-2 text-heading">{t("title")}</h2>
      <ul className="grid gap-3 md:grid-cols-2">
        {holdings.map((h) => <li key={h.id}><HoldingCard h={h} /></li>)}
      </ul>
    </section>
  );
}
