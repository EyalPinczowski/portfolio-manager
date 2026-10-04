"use client";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { AppShell } from "./AppShell";
import { AnalyzeIcon, BellIcon } from "./icons";

export function ComingSoon({ page }: { page: "analyze" | "alerts" }) {
  const t = useTranslations("comingSoon");
  const n = useTranslations("nav");
  const Icon = page === "analyze" ? AnalyzeIcon : BellIcon;
  return (
    <AppShell>
      <section className="card mx-auto mt-6 max-w-md space-y-3 py-8 text-center" aria-label={n(page)}>
        <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-brand-soft text-brand-text"><Icon className="h-7 w-7" /></span>
        <h1 className="text-title">{n(page)}</h1>
        <p className="chip-brand">{t("badge")}</p>
        <p className="text-muted">{t(page)}</p>
        <Link href="/" className="btn-secondary">{t("home")}</Link>
      </section>
    </AppShell>
  );
}
