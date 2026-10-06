"use client";
import { useLocale, useTranslations } from "next-intl";
import { usePathname, useRouter } from "@/i18n/navigation";
import type { Settings } from "@/lib/api";
import { applyTheme } from "@/lib/theme";
import { SaveStatus, useSettingsSave } from "./SettingsControls";
import { CheckList } from "./SettingsUI";

/** Each of these is one phone-style page: a check list where the current choice has a checkmark, saved at once. */

export function LanguageScreen(_props: { s?: Settings }) {
  void _props;
  const t = useTranslations("prefs.appearance");
  const locale = useLocale();
  const router = useRouter();
  const pathname = usePathname();
  const { save, state, status } = useSettingsSave();
  return (
    <>
      <CheckList
        label={t("language")} footer={t("languageHelp")} value={locale === "he" ? "he" : "en"}
        options={[{ value: "he", label: "עברית" }, { value: "en", label: "English" }]}
        onPick={async (language) => { await save({ language }); if (language !== locale) router.replace(pathname, { locale: language }); }}
      />
      <SaveStatus state={state} status={status} />
    </>
  );
}

export function AppearanceScreen({ s }: { s: Settings }) {
  const t = useTranslations("prefs.appearance");
  const { save, state, status } = useSettingsSave();
  return (
    <>
      <CheckList
        label={t("theme")} footer={t("themeHelp")} value={s.theme}
        options={(["system", "light", "dark"] as const).map((v) => ({ value: v, label: t(`themes.${v}`) }))}
        onPick={async (theme) => { if (await save({ theme })) applyTheme(theme); }}
      />
      <SaveStatus state={state} status={status} />
    </>
  );
}

export function CurrencyScreen({ s }: { s: Settings }) {
  const t = useTranslations("prefs.appearance");
  const { save, state, status } = useSettingsSave();
  return (
    <>
      <CheckList
        label={t("currency")} footer={t("currencyHelp")} value={s.main_currency}
        options={(["ILS", "USD"] as const).map((v) => ({ value: v, label: t(`currencies.${v}`) }))}
        onPick={(main_currency) => void save({ main_currency })}
      />
      <SaveStatus state={state} status={status} />
    </>
  );
}

export function NumberFormatScreen({ s }: { s: Settings }) {
  const t = useTranslations("prefs.appearance");
  const { save, state, status } = useSettingsSave();
  return (
    <>
      <CheckList
        label={t("numberFormat")} footer={t("numberFormatHelp")} value={s.number_format}
        options={(["full", "compact"] as const).map((v) => ({ value: v, label: t(`formats.${v}`) }))}
        onPick={(number_format) => void save({ number_format })}
      />
      <SaveStatus state={state} status={status} />
    </>
  );
}

export function WeekStartScreen({ s }: { s: Settings }) {
  const t = useTranslations("prefs.portfolio");
  const { save, state, status } = useSettingsSave();
  return (
    <>
      <CheckList
        label={t("weekStart")} footer={t("weekStartHelp")} value={s.week_start_day}
        options={(["sunday", "monday"] as const).map((v) => ({ value: v, label: t(`days.${v}`) }))}
        onPick={(week_start_day) => void save({ week_start_day })}
      />
      <SaveStatus state={state} status={status} />
    </>
  );
}
