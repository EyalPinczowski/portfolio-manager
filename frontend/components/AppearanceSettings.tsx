"use client";
import { useLocale, useTranslations } from "next-intl";
import { usePathname, useRouter } from "@/i18n/navigation";
import type { Settings } from "@/lib/api";
import { applyTheme } from "@/lib/theme";
import { Choice, SaveStatus, SettingsCard, useSettingsSave } from "./SettingsControls";

export function AppearanceScreen({ s }: { s: Settings }) {
  const t = useTranslations("prefs.appearance");
  const locale = useLocale();
  const router = useRouter();
  const pathname = usePathname();
  const { save, state, status } = useSettingsSave();
  return (
    <SettingsCard title={t("language")}>
      <Choice
        label={t("language")} help={t("languageHelp")} value={s.language}
        options={[{ value: "he", label: "עברית" }, { value: "en", label: "English" }]}
        onPick={async (language) => { if (await save({ language }) && language !== locale) router.replace(pathname, { locale: language }); }}
      />
      <Choice
        label={t("theme")} help={t("themeHelp")} value={s.theme}
        options={(["system", "light", "dark"] as const).map((v) => ({ value: v, label: t(`themes.${v}`) }))}
        onPick={async (theme) => { if (await save({ theme })) applyTheme(theme); }}
      />
      <Choice
        label={t("currency")} help={t("currencyHelp")} value={s.main_currency}
        options={(["ILS", "USD"] as const).map((v) => ({ value: v, label: t(`currencies.${v}`) }))}
        onPick={(main_currency) => void save({ main_currency })}
      />
      <Choice
        label={t("numberFormat")} help={t("numberFormatHelp")} value={s.number_format}
        options={(["full", "compact"] as const).map((v) => ({ value: v, label: t(`formats.${v}`) }))}
        onPick={(number_format) => void save({ number_format })}
      />
      <SaveStatus state={state} status={status} />
    </SettingsCard>
  );
}

export function PortfolioPrefsScreen({ s }: { s: Settings }) {
  const t = useTranslations("prefs.portfolio");
  const { save, state, status } = useSettingsSave();
  return (
    <SettingsCard title={t("weekStart")}>
      <Choice
        label={t("weekStart")} help={t("weekStartHelp")} value={s.week_start_day}
        options={(["sunday", "monday"] as const).map((v) => ({ value: v, label: t(`days.${v}`) }))}
        onPick={(week_start_day) => void save({ week_start_day })}
      />
      <SaveStatus state={state} status={status} />
    </SettingsCard>
  );
}
