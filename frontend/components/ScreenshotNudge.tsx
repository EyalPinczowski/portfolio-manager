"use client";
import { useLocale, useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { formatDate, formatTime } from "@/lib/format";

/**
 * "Last updated from a screenshot: <date>", plus a gentle nudge when the server says it is stale (older than the
 * configured number of days). Nothing is shown before the first screenshot update: there is no date to report.
 */
export function ScreenshotNudge({ at, stale }: { at: string | null; stale: boolean }) {
  const t = useTranslations("screenshotUpdate");
  const locale = useLocale();
  if (!at) return null;
  return (
    <div className="card flex flex-wrap items-center justify-between gap-2 text-sm" data-testid="screenshot-update">
      <p className="text-muted">{t("last", { date: `${formatDate(at)} ${formatTime(at, locale)}` })}</p>
      {stale && (
        <p role="status" className="flex flex-wrap items-center gap-2 font-medium text-amber-900 dark:text-amber-200">
          <span data-testid="screenshot-nudge">{t("nudge")}</span>
          <Link href="/import" className="btn-secondary">{t("cta")}</Link>
        </p>
      )}
    </div>
  );
}
