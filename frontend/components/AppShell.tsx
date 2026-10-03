"use client";
import type { ReactNode } from "react";
import { useTranslations } from "next-intl";
import { Link, usePathname, useRouter } from "@/i18n/navigation";
import { api } from "@/lib/api";
import { AuthGate } from "./AuthGate";
import { Disclaimer } from "./Disclaimer";

const LINKS = [
  { href: "/", key: "portfolio" },
  { href: "/xray", key: "xray" },
  { href: "/import", key: "import" },
  { href: "/settings", key: "settings" },
] as const;

export function AppShell({ children, bottomPad = false }: { children: ReactNode; bottomPad?: boolean }) {
  const t = useTranslations("nav");
  const app = useTranslations("app");
  const pathname = usePathname();
  const router = useRouter();
  return (
    <AuthGate>
      <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/90 backdrop-blur dark:border-slate-800 dark:bg-slate-900/90">
        <div className="mx-auto flex max-w-5xl items-center gap-3 px-4 py-2">
          <Link href="/" className="font-bold text-blue-800 dark:text-blue-300">{app("name")}</Link>
          <nav aria-label={t("main")} className="ms-auto flex gap-1 overflow-x-auto">
            {LINKS.map((l) => {
              const active = l.href === "/" ? pathname === "/" : pathname.startsWith(l.href);
              return (
                <Link
                  key={l.href}
                  href={l.href}
                  aria-current={active ? "page" : undefined}
                  className={`rounded-lg px-3 py-2 text-sm font-medium ${active ? "bg-blue-100 text-blue-900 dark:bg-blue-950 dark:text-blue-200" : "text-slate-700 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800"}`}
                >
                  {t(l.key)}
                </Link>
              );
            })}
          </nav>
          <button
            type="button"
            className="rounded-lg px-3 py-2 text-sm text-slate-600 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-800"
            onClick={async () => { try { await api.logout(); } finally { router.replace("/login"); } }}
          >
            {t("logout")}
          </button>
        </div>
      </header>
      <main id="main" className={`mx-auto max-w-5xl space-y-4 px-4 py-4 ${bottomPad ? "pb-32 md:pb-4" : ""}`}>{children}</main>
      <Disclaimer />
    </AuthGate>
  );
}
