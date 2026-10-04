"use client";
import { useEffect, useRef, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { AnalyzeIcon, CameraIcon, CloseIcon, PortfolioIcon, ReviewIcon, SuggestIcon } from "./icons";

const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/** Bottom sheet (centered dialog on wide screens): focus trap, Escape and backdrop close, focus returns to the opener. */
export function Sheet({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const root = ref.current;
    const items = () => Array.from(root?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []);
    (items()[0] ?? root)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); onClose(); return; }
      if (e.key !== "Tab") return;
      const els = items();
      if (els.length === 0) { e.preventDefault(); return; }
      const first = els[0];
      const last = els[els.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === first || !root?.contains(active))) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (active === last || !root?.contains(active))) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("keydown", onKey); opener?.focus?.(); };
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/50 sm:items-center sm:p-4" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div
        ref={ref}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="w-full max-w-md space-y-3 rounded-t-3xl border border-line bg-surface p-4 pb-[calc(1rem+var(--safe-b))] shadow-xl sm:rounded-3xl sm:pb-4"
      >
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-heading">{title}</h2>
          <CloseButton onClose={onClose} />
        </div>
        {children}
      </div>
    </div>
  );
}

function CloseButton({ onClose }: { onClose: () => void }) {
  const t = useTranslations("common");
  return (
    <button type="button" onClick={onClose} aria-label={t("close")} className="inline-flex h-11 w-11 items-center justify-center rounded-full text-muted hover:bg-surface-2">
      <CloseIcon />
    </button>
  );
}

/** The single "+" menu. Every entry is a live screen. */
export function ActionsSheet({ onClose }: { onClose: () => void }) {
  const t = useTranslations("actions");
  const row = "flex min-h-14 w-full items-center gap-3 rounded-2xl border border-line px-3 py-2 text-start";
  return (
    <Sheet title={t("menuTitle")} onClose={onClose}>
      <ul className="space-y-2">
        <li>
          <Link href="/review" onClick={onClose} className={`${row} bg-brand-soft hover:bg-surface-2`}>
            <span className="text-brand-text"><ReviewIcon /></span>
            <span className="flex-1">
              <span className="block font-semibold text-brand-text">{t("review")}</span>
              <span className="block text-caption text-muted">{t("reviewNote")}</span>
            </span>
          </Link>
        </li>
        <li>
          <Link href="/postmortem" onClick={onClose} className={`${row} bg-brand-soft hover:bg-surface-2`}>
            <span className="text-brand-text"><PortfolioIcon /></span>
            <span className="flex-1">
              <span className="block font-semibold text-brand-text">{t("postmortem")}</span>
              <span className="block text-caption text-muted">{t("postmortemNote")}</span>
            </span>
          </Link>
        </li>
        <li>
          <Link href="/track-record" onClick={onClose} className={`${row} bg-brand-soft hover:bg-surface-2`}>
            <span className="text-brand-text"><ReviewIcon /></span>
            <span className="flex-1">
              <span className="block font-semibold text-brand-text">{t("trackRecord")}</span>
              <span className="block text-caption text-muted">{t("trackRecordNote")}</span>
            </span>
          </Link>
        </li>
        <li>
          <Link href="/analyze" onClick={onClose} className={`${row} bg-brand-soft hover:bg-surface-2`}>
            <span className="text-brand-text"><AnalyzeIcon /></span>
            <span className="flex-1">
              <span className="block font-semibold text-brand-text">{t("analyze")}</span>
              <span className="block text-caption text-muted">{t("analyzeNote")}</span>
            </span>
          </Link>
        </li>
        <li>
          <Link href="/suggest" onClick={onClose} className={`${row} bg-brand-soft hover:bg-surface-2`}>
            <span className="text-brand-text"><SuggestIcon /></span>
            <span className="flex-1">
              <span className="block font-semibold text-brand-text">{t("suggest")}</span>
              <span className="block text-caption text-muted">{t("suggestNote")}</span>
            </span>
          </Link>
        </li>
        <li>
          <Link href="/import" onClick={onClose} className={`${row} bg-brand-soft hover:bg-surface-2`}>
            <span className="text-brand-text"><CameraIcon /></span>
            <span className="flex-1">
              <span className="block font-semibold text-brand-text">{t("update")}</span>
              <span className="block text-caption text-muted">{t("updateNote")}</span>
            </span>
          </Link>
        </li>
      </ul>
    </Sheet>
  );
}
