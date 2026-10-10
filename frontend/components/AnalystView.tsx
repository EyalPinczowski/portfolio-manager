"use client";
import { useLocale, useTranslations } from "next-intl";
import { useAnalysts } from "@/lib/hooks";
import type { AnalystsOut } from "@/lib/api";
import { formatMoney, formatNumber, formatPct } from "@/lib/format";

type Cat = "strong_buy" | "buy" | "hold" | "sell" | "strong_sell";
const CATS: { key: Cat; color: string }[] = [
  { key: "strong_buy", color: "var(--gain)" },
  { key: "buy", color: "color-mix(in srgb, var(--gain) 55%, var(--surface-2))" },
  { key: "hold", color: "var(--chart-other)" },
  { key: "sell", color: "color-mix(in srgb, var(--loss) 55%, var(--surface-2))" },
  { key: "strong_sell", color: "var(--loss)" },
];
const finite = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);

/** The analysts' own rating distribution as a 5-segment bar. Neutral: counts as reported, never the app's verdict. */
export function DistributionBar({ counts }: { counts: AnalystsOut["counts"] }) {
  const t = useTranslations("holding.analysts");
  const total = CATS.reduce((a, c) => a + counts[c.key], 0);
  const parts = CATS.map((c) => `${t(`cat.${c.key}`)} ${counts[c.key]}`).join(", ");
  return (
    <div className="space-y-2">
      <div className="flex h-3 overflow-hidden rounded-full bg-surface-2" role="img" aria-label={t("aria", { parts })} dir="ltr" data-testid="analyst-bar">
        {CATS.map((c) => counts[c.key] > 0 && total > 0 && (
          <span key={c.key} data-seg={c.key} style={{ width: `${(counts[c.key] / total) * 100}%`, background: c.color }} />
        ))}
      </div>
      <ul className="grid grid-cols-5 gap-1 text-center text-caption" data-testid="analyst-counts">
        {CATS.map((c) => (
          <li key={c.key} data-cat={c.key}>
            <span aria-hidden="true" className="mx-auto mb-0.5 block h-1.5 w-1.5 rounded-full" style={{ background: c.color }} />
            <span className="block tabular-nums font-semibold">{counts[c.key]}</span>
            <span className="block text-muted">{t(`cat.${c.key}`)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Target range track (low..high), a tick for the average and a dot for the current price. */
export function TargetLadder({ targets, price, currency }: {
  targets: NonNullable<AnalystsOut["targets"]>; price: number | null | undefined; currency: string;
}) {
  const t = useTranslations("holding.analysts");
  const locale = useLocale();
  const { low, mean, high } = targets;
  if (!finite(low) || !finite(high) || !finite(mean) || high < low) return null;
  const cur = targets.currency ?? currency;
  const vals = [low, high, ...(finite(price) ? [price] : [])];
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const pos = (v: number) => `${((v - min) / span) * 100}%`;
  const m = (v: number) => formatMoney(v, cur, locale);
  const diff = finite(price) && price > 0 ? ((mean - price) / price) * 100 : null;
  return (
    <div className="space-y-2" data-testid="target-ladder">
      <h4 className="text-sm font-semibold">{t("targets")}</h4>
      <div className="relative h-6" dir="ltr" role="img" aria-label={t("targetsAria", { low: m(low), mean: m(mean), high: m(high), price: finite(price) ? m(price) : "-" })}>
        <span className="absolute top-1/2 h-1 -translate-y-1/2 rounded-full bg-brand-soft" style={{ left: pos(low), width: `${((high - low) / span) * 100}%`, minWidth: 4 }} />
        <span className="absolute top-1/2 h-4 w-0.5 -translate-y-1/2 bg-brand" style={{ left: pos(mean) }} data-testid="target-mean" />
        {finite(price) && <span className="absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-surface bg-fg" style={{ left: pos(price) }} data-testid="target-price" />}
      </div>
      <dl className="grid grid-cols-3 gap-2 text-center text-caption">
        <div><dt className="text-muted">{t("targetLow")}</dt><dd className="font-semibold tabular-nums" dir="ltr">{m(low)}</dd></div>
        <div><dt className="text-muted">{t("targetMean")}</dt><dd className="font-semibold tabular-nums" dir="ltr">{m(mean)}</dd></div>
        <div><dt className="text-muted">{t("targetHigh")}</dt><dd className="font-semibold tabular-nums" dir="ltr">{m(high)}</dd></div>
      </dl>
      {diff !== null && <p className="text-caption text-muted">{t("meanVsPrice", { pct: formatPct(diff, locale, { signed: true }) })}</p>}
    </div>
  );
}

/** Analysts' ratings card: distribution, as-of date, analyst count, optional target ladder. States: loading, error, no coverage. */
export function AnalystView({ symbol, price, currency }: { symbol: string; price?: number | null; currency: string }) {
  const t = useTranslations("holding.analysts");
  const locale = useLocale();
  const { data, error, mutate } = useAnalysts(symbol);
  let body: React.ReactNode;
  if (error) {
    body = (
      <div className="space-y-2">
        <p role="alert" className="text-loss">{t("error")}</p>
        <button type="button" className="btn-secondary" onClick={() => mutate()}>{t("retry")}</button>
      </div>
    );
  } else if (!data) {
    body = <p role="status" className="text-muted">{t("loading")}</p>;
  } else if (data.status === "no_coverage" || data.analysts_total <= 0) {
    body = (
      <div className="space-y-1 rounded-xl bg-surface-2 p-3" role="status" data-testid="analyst-no-coverage">
        <p className="font-semibold">{t("noCoverage")}</p>
        <p className="text-sm text-muted">{t("noCoverageBody")}</p>
      </div>
    );
  } else {
    body = (
      <div className="space-y-3">
        <p className="text-caption text-muted" data-testid="analyst-meta">
          {t("total", { n: formatNumber(data.analysts_total, locale, 0) })}
          {data.as_of && <> · {t("asOf", { date: new Date(data.as_of).toLocaleDateString(locale, { dateStyle: "medium", timeZone: "UTC" }) })}</>}
          {data.source && <> · {t("source", { source: data.source })}</>}
        </p>
        <DistributionBar counts={data.counts} />
        {data.targets && <TargetLadder targets={data.targets} price={price} currency={currency} />}
        <p className="text-caption text-muted">{t("ownNote")}</p>
      </div>
    );
  }
  return (
    <section className="space-y-2" aria-label={t("title")} data-testid="analyst-view">
      <h3 className="text-heading">{t("title")}</h3>
      {body}
    </section>
  );
}
