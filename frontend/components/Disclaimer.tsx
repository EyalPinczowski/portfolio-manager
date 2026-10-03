"use client";
import { useTranslations } from "next-intl";

export function Disclaimer() {
  const t = useTranslations("disclaimer");
  return (
    <footer className="mx-auto max-w-5xl px-4 pb-28 pt-4 text-xs text-slate-600 md:pb-8 dark:text-slate-400">
      {t("footer")}
    </footer>
  );
}
