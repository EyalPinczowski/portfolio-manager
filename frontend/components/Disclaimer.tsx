"use client";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";

export function Disclaimer() {
  const t = useTranslations("disclaimer");
  const terms = useTranslations("terms");
  // Extra bottom room on phones so the floating "+" button never covers the last line.
  return (
    <footer className="mx-auto max-w-5xl px-4 pb-24 pt-4 text-caption text-muted md:pb-8">
      {t("footer")}{" "}
      <Link href="/terms" className="underline hover:text-fg">{terms("link")}</Link>
    </footer>
  );
}
