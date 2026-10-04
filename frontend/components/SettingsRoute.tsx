"use client";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { AdminScreen } from "./AdminSection";
import { AppearanceScreen, PortfolioPrefsScreen } from "./AppearanceSettings";
import { AppShell } from "./AppShell";
import { IdeaAlertsForm } from "./IdeaAlertsForm";
import { NotificationsScreen } from "./NotificationSettings";
import { WithSettings } from "./SettingsControls";
import { SettingsPage } from "./SettingsPage";
import { settingsHref, type SettingsSection } from "@/lib/routes";

const SECTIONS: SettingsSection[] = ["appearance", "portfolio", "notifications", "ideas", "admin"];
const isSection = (v: string | null): v is SettingsSection => SECTIONS.includes(v as SettingsSection);
const NAMES: Record<SettingsSection, string> = { appearance: "appearance", portfolio: "portfolio", notifications: "notifications", ideas: "ideaAlerts", admin: "admin" };

function Screen({ section }: { section: SettingsSection }) {
  const t = useTranslations("prefs");
  return (
    <AppShell>
      <div>
        <Link href={settingsHref()} className="text-sm text-brand-text hover:underline">← {t("back")}</Link>
        <h1 className="text-2xl font-bold">{t(`sections.${NAMES[section]}`)}</h1>
      </div>
      {section === "admin" ? <AdminScreen /> : (
        <WithSettings>
          {(s) => section === "appearance" ? <AppearanceScreen s={s} /> : section === "portfolio" ? <PortfolioPrefsScreen s={s} />
            : section === "notifications" ? <NotificationsScreen s={s} /> : <IdeaAlertsForm s={s} />}
        </WithSettings>
      )}
    </AppShell>
  );
}

function FromQuery() {
  const raw = useSearchParams().get("section");
  return isSection(raw) ? <Screen section={raw} /> : <SettingsPage />;
}

/** `/settings` = the hub; `/settings?section=notifications` = one section (query-based, so it works in a static export). */
export function SettingsRoute() {
  const c = useTranslations("common");
  return <Suspense fallback={<p className="p-6 text-center text-muted" role="status">{c("loading")}</p>}><FromQuery /></Suspense>;
}
