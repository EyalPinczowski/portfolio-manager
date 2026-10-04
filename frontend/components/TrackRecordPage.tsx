"use client";
import { useLocale, useTranslations } from "next-intl";
import type { BenchmarkAggregate, GateProgress, TrackRecord, TrackRow } from "@/lib/api";
import { DASH, formatDate, formatTime, intlLocale } from "@/lib/format";
import { useTrackRecord } from "@/lib/hooks";
import { Link } from "@/i18n/navigation";
import { AppShell } from "./AppShell";

const fin = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);
const Txt = ({ children }: { children: string }) => <bdi dir="auto">{children}</bdi>;

/** Percentage points with an explicit sign. `tone` colours it green/red (rows); aggregates stay neutral. */
function Pp({ value, tone = false }: { value: number | null | undefined; tone?: boolean }) {
  const t = useTranslations("trackRecord");
  const locale = useLocale();
  if (!fin(value)) return <span className="text-muted">{DASH}</span>;
  const num = new Intl.NumberFormat(intlLocale(locale), { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "exceptZero" }).format(value);
  const cls = !tone || value === 0 ? "" : value > 0 ? "text-gain" : "text-loss";
  return (
    <span className={`tabular-nums ${cls}`} dir="ltr">
      {tone && value !== 0 && <span aria-hidden="true" className="me-1 text-[0.7em]">{value > 0 ? "▲" : "▼"}</span>}
      {t("ppUnit", { value: num })}
    </span>
  );
}

const pct1 = (v: number, locale: string) => `${new Intl.NumberFormat(intlLocale(locale), { maximumFractionDigits: 1 }).format(v)}%`;

function Aggregates({ list }: { list: BenchmarkAggregate[] }) {
  const t = useTranslations("trackRecord");
  const locale = useLocale();
  if (list.length === 0) return null;
  return (
    <section className="space-y-3" aria-label={t("aggTitle")}>
      <h2 className="text-heading">{t("aggTitle")}</h2>
      <ul className="grid gap-3 sm:grid-cols-2">
        {list.map((b) => (
          <li key={b.name} className="card space-y-2" data-testid="aggregate">
            <h3 className="font-semibold"><Txt>{b.name}</Txt></h3>
            <dl className="space-y-1 text-sm">
              <div className="flex justify-between gap-2"><dt className="text-muted">{t("hitRate")}</dt><dd className="tabular-nums" dir="ltr">{fin(b.hit_rate_pct) ? pct1(b.hit_rate_pct, locale) : DASH}</dd></div>
              <div className="flex justify-between gap-2"><dt className="text-muted">{t("avgExcess")}</dt><dd><Pp value={b.avg_excess_pct} /></dd></div>
            </dl>
            <p className="text-caption text-muted">{t("aggN", { count: b.count })}{b.count < 10 && ` · ${t("aggFew")}`}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

function Gate({ g }: { g: GateProgress }) {
  const t = useTranslations("trackRecord");
  const locale = useLocale();
  const num = (v: number) => new Intl.NumberFormat(intlLocale(locale), { maximumFractionDigits: 1 }).format(v);
  const row = (label: string, value: string, key: string) => (
    <div className="flex justify-between gap-2" data-testid={key}><dt className="text-muted">{label}</dt><dd className="tabular-nums" dir="ltr">{value}</dd></div>
  );
  return (
    <section className="card space-y-2" aria-label={t("gateTitle")}>
      <h2 className="text-heading">{t("gateTitle")}</h2>
      <p className="text-sm font-medium" role="status">{g.open ? t("gateOpen") : t("gateClosed")}</p>
      <dl className="space-y-1 text-sm">
        {row(t("gateWeeks"), t("gateOutOf", { have: num(g.weeks_running), need: g.weeks_required }), "gate-weeks")}
        {row(t("gateResolved"), t("gateOutOf", { have: g.resolved_at_1m, need: g.resolved_required }), "gate-resolved")}
        {row(t("gateRecorded"), String(g.recorded), "gate-recorded")}
        {row(t("gateErrors"), String(g.critical_errors), "gate-errors")}
      </dl>
      {g.reasons.length > 0 && (
        <div className="border-t border-line pt-2">
          <h3 className="text-sm font-semibold">{t("gateReasons")}</h3>
          <ul className="list-disc ps-5 text-sm text-muted">{g.reasons.map((r) => <li key={r}><Txt>{r}</Txt></li>)}</ul>
        </div>
      )}
    </section>
  );
}

function CallsTable({ rows, names }: { rows: TrackRow[]; names: string[] }) {
  const t = useTranslations("trackRecord");
  const locale = useLocale();
  const fmt = (v: number) => `${new Intl.NumberFormat(intlLocale(locale), { minimumFractionDigits: 2, maximumFractionDigits: 2, signDisplay: "exceptZero" }).format(v)}%`;
  return (
    <section className="space-y-2" aria-label={t("tableTitle")}>
      <h2 className="text-heading">{t("tableTitle")}</h2>
      <div className="card overflow-x-auto p-0">
        <table className="w-full text-start text-sm">
          <thead>
            <tr className="border-b border-line text-caption text-muted">
              <th scope="col" className="p-2 text-start font-medium">{t("colSymbol")}</th>
              <th scope="col" className="p-2 text-start font-medium">{t("colMade")}</th>
              <th scope="col" className="p-2 text-start font-medium">{t("colHorizon")}</th>
              <th scope="col" className="p-2 text-start font-medium">{t("colOutcome")}</th>
              <th scope="col" className="p-2 text-start font-medium">{t("colReturn")}</th>
              {names.map((n) => <th key={n} scope="col" className="p-2 text-start font-medium">{t("colVs", { name: n })}</th>)}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={`${r.symbol}-${r.made_on}-${r.horizon}`} className="border-b border-line last:border-0" data-testid="call-row">
                <td className="p-2 font-medium" dir="ltr">
                  {r.symbol}
                  {!r.active_weights && <span className="chip-warn ms-2" data-testid="other-weights" title={t("otherWeightsNote")}>{t("otherWeights")}</span>}
                </td>
                <td className="p-2 tabular-nums" dir="ltr">{formatDate(r.made_on)}</td>
                <td className="p-2" dir="ltr">{r.horizon}</td>
                <td className="p-2">{t(`outcome.${r.outcome}`)}</td>
                <td className="p-2 tabular-nums" dir="ltr">{fmt(r.return_pct)}</td>
                {names.map((n) => {
                  const b = r.benchmarks.find((x) => x.name === n);
                  return <td key={n} className="p-2"><Pp value={b?.excess_pct} tone /></td>;
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.some((r) => !r.active_weights) && <p className="text-caption text-muted">{t("otherWeightsNote")}</p>}
    </section>
  );
}

function Body() {
  const t = useTranslations("trackRecord");
  const c = useTranslations("common");
  const locale = useLocale();
  const { data, error } = useTrackRecord();
  const d: TrackRecord | undefined = data;
  return (
    <>
      <div>
        <Link href="/" className="text-sm text-brand-text hover:underline">← {c("back")}</Link>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-sm text-muted">{t("intro")}</p>
      </div>
      {error ? <p role="alert">{t("error")}</p> : !d ? <p role="status" className="text-muted">{t("loading")}</p> : (
        <>
          {d.state === "not_started" && <p className="card" role="status" data-testid="state-not-started">{t("notStarted")}</p>}
          {d.state === "none_ended" && <p className="card" role="status" data-testid="state-none-ended">{t("noneEnded")}</p>}
          <p className="text-caption text-muted">{t("asOf", { time: `${formatDate(d.as_of)} ${formatTime(d.as_of, locale)}` })}</p>
          {d.state === "ready" && (
            <>
              <Aggregates list={d.benchmarks} />
              <CallsTable rows={d.rows} names={d.benchmarks.map((b) => b.name)} />
              {d.truncated && <p className="text-caption text-muted">{t("truncated")}</p>}
            </>
          )}
          <Gate g={d.gate} />
          <section className="card space-y-1" aria-label={t("countsTitle")}>
            <h2 className="text-heading">{t("countsTitle")}</h2>
            <p className="text-sm" data-testid="awaiting">{t("awaiting", { count: d.awaiting_resolution })}</p>
            <p className="text-sm" data-testid="excluded">{t("excluded", { count: d.excluded_errors })}</p>
            <p className="text-caption text-muted">{t("countsHint")}</p>
          </section>
          <section className="card space-y-2" aria-label={t("methodologyTitle")} data-testid="methodology">
            <h2 className="text-heading">{t("methodologyTitle")}</h2>
            <ul className="list-disc space-y-1 ps-5 text-sm text-muted">{d.methodology.map((m) => <li key={m}><Txt>{m}</Txt></li>)}</ul>
          </section>
        </>
      )}
    </>
  );
}

export function TrackRecordPage() {
  return <AppShell><Body /></AppShell>;
}
