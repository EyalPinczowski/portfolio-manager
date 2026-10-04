"use client";
import { Suspense, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { AccountScreen, SessionsScreen } from "./AccountSettings";
import { AdminScreen } from "./AdminSection";
import { AppearanceScreen, CurrencyScreen, LanguageScreen, NumberFormatScreen, WeekStartScreen } from "./AppearanceSettings";
import { AppShell } from "./AppShell";
import { IdeaAlertsForm } from "./IdeaAlertsForm";
import { QuietHoursScreen, TelegramScreen, WeeklyReviewScreen } from "./NotificationSettings";
import { RiskScreen } from "./RiskSettings";
import { WithSettings } from "./SettingsControls";
import { SettingsHub, StatusScreen } from "./SettingsPage";
import { ChevronIcon } from "./icons";
import { SETTINGS_SECTIONS, settingsHref, type SettingsSection } from "@/lib/routes";

const isSection = (v: string | null): v is SettingsSection => (SETTINGS_SECTIONS as readonly string[]).includes(v ?? "");
/** Message key under prefs.sections for each page title. */
const NAMES: Record<SettingsSection, string> = {
  account: "account", sessions: "sessions", language: "language", appearance: "appearance", currency: "currency", numberFormat: "numberFormat", weekStart: "weekStart",
  weeklyReview: "weeklyReview", quietHours: "quietHours", telegram: "telegram", ideas: "ideaAlerts", risk: "risk", status: "status", admin: "admin",
};
/** Pages that were opened from another sub-page go back to it; everything else goes back to the hub. */
const PARENT: Partial<Record<SettingsSection, SettingsSection>> = { sessions: "account" };

function Body({ section }: { section: SettingsSection }) {
  switch (section) {
    case "admin": return <AdminScreen />;
    case "account": return <AccountScreen />;
    case "sessions": return <SessionsScreen />;
    case "telegram": return <TelegramScreen />;
    case "status": return <StatusScreen />;
    case "risk": return <RiskScreen />;
    default:
      return (
        <WithSettings>
          {(s) => {
            switch (section) {
              case "language": return <LanguageScreen s={s} />;
              case "appearance": return <AppearanceScreen s={s} />;
              case "currency": return <CurrencyScreen s={s} />;
              case "numberFormat": return <NumberFormatScreen s={s} />;
              case "weekStart": return <WeekStartScreen s={s} />;
              case "weeklyReview": return <WeeklyReviewScreen s={s} />;
              case "quietHours": return <QuietHoursScreen s={s} />;
              default: return <IdeaAlertsForm s={s} />;
            }
          }}
        </WithSettings>
      );
  }
}

/** The shared frame: a narrow centred column (phone width), with a gentle slide-in per page. */
function Frame({ children, k }: { children: ReactNode; k: string }) {
  return <AppShell><div key={k} className="settings-slide mx-auto w-full max-w-xl space-y-5">{children}</div></AppShell>;
}

function Screen({ section }: { section: SettingsSection }) {
  const t = useTranslations("prefs");
  const s = useTranslations("settings");
  const parent = PARENT[section];
  return (
    <Frame k={section}>
      <div className="space-y-1">
        <Link href={settingsHref(parent)} className="-ms-2 inline-flex min-h-11 items-center gap-1 rounded-lg px-2 text-base text-brand-text hover:bg-surface-2">
          <ChevronIcon className="h-5 w-5 rotate-180" />
          {parent ? t(`sections.${NAMES[parent]}`) : s("title")}
        </Link>
        <h1 className="text-3xl font-bold">{t(`sections.${NAMES[section]}`)}</h1>
      </div>
      <Body section={section} />
    </Frame>
  );
}

function FromQuery() {
  const raw = useSearchParams().get("section");
  return isSection(raw) ? <Screen section={raw} /> : <Frame k="hub"><SettingsHub /></Frame>;
}

/** `/settings` = the hub; `/settings?section=telegram` = one page (query-based, so it works in a static export). */
export function SettingsRoute() {
  const c = useTranslations("common");
  return <Suspense fallback={<p className="p-6 text-center text-muted" role="status">{c("loading")}</p>}><FromQuery /></Suspense>;
}
