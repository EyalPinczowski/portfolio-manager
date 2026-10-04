"use client";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { settingsHref } from "@/lib/routes";
import { DeviceGlyph, LockGlyph, SparkGlyph } from "./SettingsIcons";
import { GroupItem, SettingsGroup, SettingsRow } from "./SettingsUI";

/**
 * Privacy and AI: plain-words, read-only facts. There is nothing to switch here: the rules are fixed by the app
 * (free AI sees public stock data only; screenshots are never kept), so no toggle is shown that could not be honoured.
 */
export function PrivacyScreen() {
  const t = useTranslations("prefs.privacy");
  const list = (k: string) => (t.raw(k) as string[]);
  return (
    <>
      <SettingsGroup label={t("aiTitle")} title={t("aiTitle")} footer={t("aiFooter")}>
        <GroupItem><p className="flex items-start gap-3 text-base"><span className="mt-0.5 text-brand-text"><SparkGlyph /></span><span>{t("aiLead")}</span></p></GroupItem>
        <GroupItem>
          <p className="text-sm font-medium">{t("aiSeesTitle")}</p>
          <ul className="mt-1 list-disc ps-5 text-sm text-muted" data-testid="ai-sees">{list("aiSees").map((x) => <li key={x}>{x}</li>)}</ul>
        </GroupItem>
        <GroupItem>
          <p className="text-sm font-medium">{t("aiNeverTitle")}</p>
          <ul className="mt-1 list-disc ps-5 text-sm text-muted" data-testid="ai-never">{list("aiNever").map((x) => <li key={x}>{x}</li>)}</ul>
        </GroupItem>
      </SettingsGroup>
      <SettingsGroup label={t("shotsTitle")} title={t("shotsTitle")} footer={t("shotsFooter")}>
        <GroupItem><p className="flex items-start gap-3 text-base"><span className="mt-0.5 text-brand-text"><DeviceGlyph /></span><span>{t("shotsLead")}</span></p></GroupItem>
        <GroupItem><ul className="list-disc ps-5 text-sm text-muted" data-testid="shots-rules">{list("shots").map((x) => <li key={x}>{x}</li>)}</ul></GroupItem>
      </SettingsGroup>
      <SettingsGroup label={t("yoursTitle")} title={t("yoursTitle")} footer={t("yoursFooter")}>
        <SettingsRow label={t("accountLink")} icon={<LockGlyph />} tone="indigo" href={settingsHref("account")} />
      </SettingsGroup>
      <p className="px-4 text-caption text-muted"><Link href={settingsHref("status")} className="text-brand-text hover:underline">{t("statusLink")}</Link></p>
    </>
  );
}
