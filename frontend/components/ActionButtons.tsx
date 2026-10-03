"use client";
import { useTranslations } from "next-intl";

/** Phase 1: the three big actions are present but disabled. Sticky bottom bar on mobile. */
export function ActionButtons() {
  const t = useTranslations("actions");
  const items = ["review", "suggest", "analyze"] as const;
  return (
    <nav
      aria-label={t("label")}
      className="fixed inset-x-0 bottom-0 z-30 border-t border-slate-200 bg-white/95 p-3 backdrop-blur md:static md:z-auto md:border-0 md:bg-transparent md:p-0 dark:border-slate-800 dark:bg-slate-900/95 md:dark:bg-transparent"
    >
      <div className="mx-auto grid max-w-5xl grid-cols-3 gap-2">
        {items.map((k) => (
          <button
            key={k}
            type="button"
            disabled
            title={`${t("soon")}. ${t("soonNote")}`}
            className="btn-primary flex-col gap-0.5 py-3 text-xs md:text-base"
          >
            <span>{t(k)}</span>
            <span className="text-[10px] font-normal opacity-90 md:text-xs">{t("soon")}</span>
          </button>
        ))}
      </div>
    </nav>
  );
}
