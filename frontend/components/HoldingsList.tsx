"use client";
import { useLocale, useTranslations } from "next-intl";
import type { Holding } from "@/lib/api";
import { DASH, formatDate, formatMoney, formatNumber, formatWeight, isTaseSymbol, toAgorot } from "@/lib/format";
import { holdingHref } from "@/lib/routes";
import { Link } from "@/i18n/navigation";
import { ChevronIcon } from "./icons";
import { PnlText } from "./Pnl";
import { HoldingActions } from "./HoldingActions";

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

/** "Check these numbers": the figures look unusual (never auto-corrected). */
export function CheckNumbersChip({ h }: { h: Holding }) {
  const t = useTranslations("holdings");
  if (!h.check_numbers) return null;
  return <span className="chip-warn" title={t("checkNumbersHint")} data-testid="check-numbers">{t("checkNumbers")}</span>;
}

/** Headline = the position's value in the holding's own currency; below it "quantity x price" with the unit explicit for TASE. */
export function PositionValue({ h }: { h: Holding }) {
  const t = useTranslations("holdings");
  const locale = useLocale();
  const hasPrice = finite(h.price) && h.price > 0;
  const price = hasPrice ? formatMoney(h.price, h.currency, locale) : DASH;
  const tase = hasPrice && isTaseSymbol(h.symbol) && h.currency === "ILS";
  const unit = tase ? `${price} (${t("priceAgorot", { agorot: formatNumber(toAgorot(h.price), locale, 0) })})` : price;
  return (
    <div className="shrink-0 text-end">
      <p className="font-semibold tabular-nums" dir="ltr" data-testid="position-value">{hasPrice && finite(h.value_native) ? formatMoney(h.value_native, h.currency, locale) : DASH}</p>
      <p className="text-caption text-muted tabular-nums" dir="ltr" data-testid="qty-price">{t("qtyTimesPrice", { qty: formatNumber(h.quantity, locale, 4), price: unit })}</p>
    </div>
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
  const hasPnl = !!h.pnl && (finite(h.pnl.ils) || finite(h.pnl.pct));
  return (
    <div className="flex h-full flex-col gap-2">
    <Link
      href={holdingHref(h.id)}
      aria-label={t("open", { name })}
      className="card flex flex-1 flex-col gap-2 hover:border-brand"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="break-words font-semibold">{name}</p>
          <p className="text-caption text-muted" dir="ltr">{h.symbol}</p>
        </div>
        {h.fund ? <FundValue h={h} /> : (
          <div className="shrink-0 text-end">
            <PositionValue h={h} />
            <p className="text-sm"><PnlText pct={h.day_change_pct} locale={locale} /> <span className="text-caption text-muted">{t("today")}</span></p>
          </div>
        )}
      </div>
      <div className="flex items-center justify-between gap-3 border-t border-line pt-2 text-sm">
        <span className="text-muted">{t("pnl")}</span>
        {hasPnl ? (
          <PnlText value={h.pnl_native ?? (h.currency === "ILS" ? h.pnl?.ils : undefined)} pct={h.pnl?.pct} currency={h.currency} locale={locale} className="font-semibold" />
        ) : (
          <span className="text-end">
            <span className="text-muted">{DASH}<span className="sr-only"> {t("pnlUnavailable")}</span></span>
            <span className="block text-caption text-muted">{t("pnlHint")}</span>
          </span>
        )}
      </div>
      <div className="mt-auto flex items-end justify-between gap-2">
        <span className="flex flex-wrap gap-1.5"><StatusChips h={h} /><CheckNumbersChip h={h} /></span>
        <span className="flex shrink-0 items-center gap-1 text-caption text-muted">
          {finite(h.weight_pct) && <span className="tabular-nums" dir="ltr" title={t("weight")}>{formatWeight(h.weight_pct, locale)}</span>}
          <ChevronIcon className="h-4 w-4" />
        </span>
      </div>
    </Link>
    {h.portfolio_id !== undefined && <HoldingActions h={h} portfolioId={h.portfolio_id} name={name} />}
    </div>
  );
}

/** One compact line per holding: name + symbol, value, P&L %, a thin weight bar and the check-numbers chip. The whole row opens the holding page. */
export function HoldingRow({ h }: { h: Holding }) {
  const t = useTranslations("holdings");
  const locale = useLocale();
  const name = locale === "he" ? h.name_he : h.name_en;
  const hasPrice = finite(h.price) && h.price > 0;
  const fundIls = h.fund?.value_basis === "manual_value" && finite(h.fund.manual_value_ils) ? h.fund.manual_value_ils : null;
  const value = h.fund ? (fundIls !== null ? formatMoney(fundIls, "ILS", locale) : DASH) : hasPrice && finite(h.value_native) ? formatMoney(h.value_native, h.currency, locale) : DASH;
  const pct = h.pnl && finite(h.pnl.pct) ? h.pnl.pct : null;
  const w = finite(h.weight_pct) ? Math.min(100, Math.max(0, h.weight_pct)) : 0;
  return (
    <Link href={holdingHref(h.id)} aria-label={t("open", { name })} data-testid="holding-row" className="flex min-h-14 flex-col justify-center gap-1 px-3 py-2 hover:bg-surface-2">
      <span className="flex items-center justify-between gap-3">
        <span className="min-w-0">
          <span className="block truncate font-semibold"><bdi>{name}</bdi></span>
          <span className="block text-caption text-muted" dir="ltr">{h.symbol}</span>
        </span>
        <span className="flex shrink-0 items-center gap-2 text-end">
          <span className="block">
            <span className="block font-semibold tabular-nums" dir="ltr">{value}</span>
            <span className="block text-caption"><PnlText pct={pct} locale={locale} />{pct === null && <span className="sr-only"> {t("pnlUnavailable")}</span>}</span>
          </span>
          <ChevronIcon className="h-4 w-4 text-muted" />
        </span>
      </span>
      <span className="flex items-center gap-2">
        <span aria-hidden="true" className="h-1 flex-1 overflow-hidden rounded-full bg-surface-2"><span className="block h-full rounded-full bg-brand" style={{ width: `${w}%` }} /></span>
        {finite(h.weight_pct) && <span className="text-caption text-muted tabular-nums" dir="ltr" title={t("weight")}>{formatWeight(h.weight_pct, locale)}</span>}
        {h.price_stale && <span className="chip-warn" title={t(h.fund ? "fundStaleHint" : "staleHint")}>{t("stale")}</span>}
        {(h.score_card?.confidence === 0 || !hasPrice) && <span className="chip-neutral">{t("noData")}</span>}
        <CheckNumbersChip h={h} />
      </span>
    </Link>
  );
}

export function HoldingsList({ holdings }: { holdings: Holding[] }) {
  const t = useTranslations("holdings");
  return (
    <section aria-label={t("title")}>
      <h2 className="mb-2 text-heading">{t("title")}</h2>
      <ul className="card divide-y divide-line !p-0 overflow-hidden">
        {holdings.map((h) => <li key={h.id}><HoldingRow h={h} /></li>)}
      </ul>
    </section>
  );
}
