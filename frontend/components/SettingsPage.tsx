"use client";
import { useState, type ReactNode } from "react";
import { useLocale, useTranslations } from "next-intl";
import useSWR from "swr";
import pkg from "@/package.json";
import { api } from "@/lib/api";
import { ageOf, formatDate, QUOTES_STALE_MIN } from "@/lib/format";
import { useAdminUsers, useMe, usePortfolios, useSettings, useSummary, useTelegramStatus } from "@/lib/hooks";
import { settingsHref } from "@/lib/routes";
import { useRouter } from "@/i18n/navigation";
import { guideActions } from "@/lib/guide";
import { SaveStatus, useSettingsSave } from "./SettingsControls";
import {
  BellGlyph, BookGlyph, CalendarGlyph, ClockGlyph, CoinGlyph, GlobeGlyph, HashGlyph, KeyGlyph, LockGlyph, PulseGlyph, SendGlyph, ShieldGlyph, SparkGlyph, SunGlyph, UserGlyph,
} from "./SettingsIcons";
import { GroupItem, SettingsGroup, SettingsRow, ToggleRow } from "./SettingsUI";
import { ChevronIcon } from "./icons";
import { Link } from "@/i18n/navigation";

/** System status from /api/health, plus the FX staleness note from the summary. Never shows a verdict. */
export function SystemStatus() {
  const t = useTranslations("settings");
  const { data: health, error } = useSWR("health", () => api.health(), { refreshInterval: 60_000, shouldRetryOnError: false });
  const { data: portfolios } = usePortfolios();
  const { data: summary } = useSummary(portfolios?.[0]?.id ?? null);
  const rel = (iso: string | null | undefined) => {
    const a = ageOf(iso);
    if (!a) return t("never");
    return a.unit === "now" ? t("relNow") : t(a.unit === "minutes" ? "relMinutes" : a.unit === "hours" ? "relHours" : "relDays", { n: a.n });
  };
  if (error) return <SettingsGroup label={t("status")}><GroupItem><p role="alert" className="text-sm">{t("statusLoadError")}</p></GroupItem></SettingsGroup>;
  if (!health) return null;
  const stockOpen = !!summary && (summary.markets.US.open || summary.markets.TASE.open);
  const quotesAge = ageOf(health.last_quotes_at);
  const quotesOld = quotesAge === null || quotesAge.unit === "hours" || quotesAge.unit === "days" || (quotesAge.unit === "minutes" && quotesAge.n > QUOTES_STALE_MIN);
  const down = health.scheduler === "unavailable";
  const stale = stockOpen && quotesOld;
  const line = "flex min-h-12 items-center justify-between gap-3 px-4 py-2";
  return (
    <SettingsGroup label={t("status")} footer={t("fxNote")}>
      {down && <li className="px-4 py-2"><p role="alert" className="rounded-lg bg-warn-bg px-3 py-2 text-sm font-medium text-warn-fg">{t("schedulerDown")}</p></li>}
      {stale && <li className="px-4 py-2"><p role="alert" className="rounded-lg bg-warn-bg px-3 py-2 text-sm font-medium text-warn-fg">{t("staleWarning")}</p></li>}
      <li className={line}><span>{t("scheduler")}</span><span className="text-muted">{t(`schedulerStates.${health.scheduler}`)}</span></li>
      <li className={line}><span>{t("lastQuotes")}</span><span className="text-muted" data-testid="last-quotes">{rel(health.last_quotes_at)}</span></li>
      <li className={line}><span>{t("lastSnapshot")}</span><span className="text-muted" data-testid="last-snapshot" title={health.last_snapshot_at ? formatDate(health.last_snapshot_at) : undefined}>{rel(health.last_snapshot_at)}</span></li>
      {summary && <li className="px-4 py-3"><p className="text-sm" role="status">{summary.fx_stale ? t("fxNow") : t("fxOk")}</p></li>}
    </SettingsGroup>
  );
}

function LaunchGateNote() {
  const t = useTranslations("settings");
  const { data } = useSWR("launch-gate", () => api.launchGate(), { shouldRetryOnError: false });
  if (!data) return null;
  return (
    <SettingsGroup label={t("gate")} title={t("gate")}>
      <GroupItem>
        <p className="text-sm">{data.open ? t("gateOpen") : t("gateClosed")}</p>
        {!data.open && data.reasons.length > 0 && <ul className="mt-2 list-disc ps-5 text-sm text-muted">{data.reasons.map((r) => <li key={r}>{r}</li>)}</ul>}
      </GroupItem>
    </SettingsGroup>
  );
}

/** App version, data-sources credit and the disclaimer: plain read-only facts. */
export function AboutGroup() {
  const t = useTranslations("settings.about");
  const d = useTranslations("disclaimer");
  const line = "flex min-h-12 items-center justify-between gap-3 px-4 py-2";
  return (
    <SettingsGroup label={t("title")} title={t("title")}>
      <li className={line}><span>{t("webVersion")}</span><span className="text-muted tabular-nums" dir="ltr" data-testid="web-version">{pkg.version}</span></li>
      <GroupItem><p className="text-sm font-medium">{t("sourcesTitle")}</p><p className="text-sm text-muted" data-testid="data-sources">{t("sources")}</p><p className="mt-1 text-caption text-muted">{t("sourcesNote")}</p></GroupItem>
      <GroupItem><p className="text-caption text-muted">{d("footer")}</p></GroupItem>
    </SettingsGroup>
  );
}

export function StatusScreen() {
  return <><SystemStatus /><LaunchGateNote /><AboutGroup /></>;
}

type HubRow = { id: string; label: string; node: ReactNode };
type HubGroup = { id: string; title?: string; footer?: string; rows: HubRow[] };

/** The Settings home: a search field, the account row and grouped rows. Each row shows its current value and opens its own page. */
export function SettingsHub() {
  const t = useTranslations("prefs");
  const p = useTranslations("settings");
  const ap = useTranslations("prefs.appearance");
  const nf = useTranslations("prefs.notifications");
  const tg = useTranslations("prefs.telegram");
  const idea = useTranslations("prefs.ideas");
  const wk = useTranslations("prefs.portfolio");
  const router = useRouter();
  const locale = useLocale();
  const [q, setQ] = useState("");
  const { data: s } = useSettings();
  const { data: me } = useMe();
  const { data: adminData } = useAdminUsers();
  const { data: tgStatus } = useTelegramStatus();
  const { data: portfolios } = usePortfolios();
  const { save, state, status } = useSettingsSave();
  const sec = (k: string) => t(`sections.${k}`);

  const risk = portfolios?.[0]?.risk_filter?.preset;
  const riskValue = risk ? (p.has(`presets.${risk}`) ? p(`presets.${risk}`) : risk) : p("custom");
  const telegramValue = tgStatus ? (tgStatus.linked ? tg("connected") : tg("notConnected")) : undefined;

  const groups: HubGroup[] = (() => {
    if (!s) return [];
    const w = s.weekly_review;
    const r = (id: string, label: string, node: ReactNode): HubRow => ({ id, label, node });
    const g: HubGroup[] = [
      {
        id: "general", footer: t("hubIntro"),
        rows: [
          r("language", sec("language"), <SettingsRow key="language" label={sec("language")} value={locale === "he" ? "עברית" : "English"} icon={<GlobeGlyph />} tone="blue" href={settingsHref("language")} />),
          r("appearance", sec("appearance"), <SettingsRow key="appearance" label={sec("appearance")} value={ap(`themes.${s.theme}`)} icon={<SunGlyph />} tone="indigo" href={settingsHref("appearance")} />),
          r("currency", sec("currency"), <SettingsRow key="currency" label={sec("currency")} value={ap(`currencies.${s.main_currency}`)} icon={<CoinGlyph />} tone="green" href={settingsHref("currency")} />),
          r("numberFormat", sec("numberFormat"), <SettingsRow key="numberFormat" label={sec("numberFormat")} value={ap(`formats.${s.number_format}`)} icon={<HashGlyph />} tone="gray" href={settingsHref("numberFormat")} />),
          r("weekStart", sec("weekStart"), <SettingsRow key="weekStart" label={sec("weekStart")} value={wk(`days.${s.week_start_day}`)} icon={<CalendarGlyph />} tone="orange" href={settingsHref("weekStart")} />),
        ],
      },
      {
        id: "notifications", title: t("hub.groupNotifications"), footer: t("hub.footerNotifications"),
        rows: [
          r("priceAlerts", sec("priceAlerts"), <ToggleRow key="priceAlerts" label={sec("priceAlerts")} checked={s.price_alerts_enabled} onChange={(v) => void save({ price_alerts_enabled: v })} icon={<BellGlyph />} tone="red" />),
          r("weeklyOn", nf("weeklyOn"), <ToggleRow key="weeklyOn" label={nf("weeklyOn")} checked={w.enabled} onChange={(enabled) => void save({ weekly_review: { enabled } })} icon={<CalendarGlyph />} tone="orange" />),
          r("weeklyReview", sec("weeklyReview"), <SettingsRow key="weeklyReview" label={sec("weeklyReview")} value={`${nf(`weekdays.${w.day}`)} ${w.time}`} icon={<ClockGlyph />} tone="orange" href={settingsHref("weeklyReview")} />),
          r("quietHours", sec("quietHours"), <SettingsRow key="quietHours" label={sec("quietHours")} value={s.quiet_hours ? `${s.quiet_hours.start}–${s.quiet_hours.end}` : t("hub.off")} icon={<ClockGlyph />} tone="purple" href={settingsHref("quietHours")} />),
          r("telegram", sec("telegram"), <SettingsRow key="telegram" label={sec("telegram")} value={telegramValue} icon={<SendGlyph />} tone="blue" href={settingsHref("telegram")} />),
          r("ideas", sec("ideaAlerts"), <SettingsRow key="ideas" label={sec("ideaAlerts")} value={s.idea_alerts.state === "set" ? idea("rowSet") : idea("rowNotSet")} icon={<SparkGlyph />} tone="teal" href={settingsHref("ideas")} />),
        ],
      },
      {
        id: "investing", title: t("hub.groupInvesting"), footer: t("hub.footerInvesting"),
        rows: [r("risk", sec("risk"), <SettingsRow key="risk" label={sec("risk")} value={riskValue} icon={<ShieldGlyph />} tone="green" href={settingsHref("risk")} />)],
      },
      {
        id: "help", title: t("hub.groupHelp"), footer: t("hub.footerHelp"),
        rows: [
          r("privacy", sec("privacy"), <SettingsRow key="privacy" label={sec("privacy")} icon={<LockGlyph />} tone="indigo" href={settingsHref("privacy")} />),
          r("status", sec("status"), <SettingsRow key="status" label={sec("status")} icon={<PulseGlyph />} tone="pink" href={settingsHref("status")} />),
          r("guide", sec("guide"), <SettingsRow key="guide" label={sec("guide")} icon={<BookGlyph />} tone="gray" chevron onClick={() => { guideActions.reopen(); router.push("/"); }} />),
        ],
      },
      ...(adminData ? [{ id: "admin", rows: [r("admin", sec("admin"), <SettingsRow key="admin" label={sec("admin")} icon={<KeyGlyph />} tone="gray" href={settingsHref("admin")} />)] }] : []),
    ];
    return g;
  })();

  const needle = q.trim().toLowerCase();
  const shown = groups
    .map((gr) => ({ ...gr, rows: needle ? gr.rows.filter((x) => x.label.toLowerCase().includes(needle)) : gr.rows }))
    .filter((gr) => gr.rows.length > 0);
  const accountMatch = !needle || [sec("account"), me?.email ?? ""].some((x) => x.toLowerCase().includes(needle));

  return (
    <nav aria-label={t("hubTitle")} className="space-y-5">
      <h1 className="text-3xl font-bold">{p("title")}</h1>
      <input type="search" aria-label={t("hub.searchLabel")} placeholder={t("hub.searchPlaceholder")} value={q} onChange={(e) => setQ(e.target.value)} className="min-h-11 w-full rounded-xl border border-line bg-surface-2 px-4 text-base text-fg" />
      {accountMatch && (
        <section aria-label={sec("account")}>
          <ul className="overflow-hidden rounded-2xl border border-line bg-surface">
            <li>
              <Link href={settingsHref("account")} className="flex min-h-16 items-center gap-3 px-4 py-3 hover:bg-surface-2">
                <span aria-hidden="true" className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-brand text-brand-on"><UserGlyph /></span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-semibold" dir="ltr">{me?.email ?? sec("account")}</span>
                  <span className="block text-caption text-muted">{t("hub.accountNote")}</span>
                </span>
                <ChevronIcon className="h-4 w-4 shrink-0 text-muted" />
              </Link>
            </li>
          </ul>
        </section>
      )}
      {!s && <p role="status" className="text-muted">…</p>}
      {shown.map((gr) => <SettingsGroup key={gr.id} title={gr.title} label={gr.title ?? sec(gr.rows[0].id)} footer={needle ? undefined : gr.footer}>{gr.rows.map((x) => x.node)}</SettingsGroup>)}
      {s && shown.length === 0 && !accountMatch && <p role="status" className="px-4 text-muted">{t("hub.noResults", { q })}</p>}
      <SaveStatus state={state} status={status} />
    </nav>
  );
}

