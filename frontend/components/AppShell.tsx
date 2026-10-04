"use client";
import { Fragment, useCallback, useEffect, useState, type ComponentType, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { Link, usePathname, useRouter } from "@/i18n/navigation";
import { api } from "@/lib/api";
import { useSettings } from "@/lib/hooks";
import { setFormatPrefs } from "@/lib/format";
import { applyTheme } from "@/lib/theme";
import { ActionsSheet } from "./ActionsSheet";
import { AuthGate } from "./AuthGate";
import { Disclaimer } from "./Disclaimer";
import { AnalyzeIcon, BellIcon, HomeIcon, PlusIcon, PortfolioIcon, SettingsIcon } from "./icons";

export type TabKey = "home" | "portfolio" | "analyze" | "alerts" | "settings";
export const TABS: { key: TabKey; href: string; icon: ComponentType<{ className?: string }>; match: string[] }[] = [
  { key: "home", href: "/", icon: HomeIcon, match: [] },
  { key: "portfolio", href: "/xray", icon: PortfolioIcon, match: ["/xray", "/import", "/holding", "/review", "/suggest"] },
  { key: "analyze", href: "/analyze", icon: AnalyzeIcon, match: ["/analyze"] },
  { key: "alerts", href: "/alerts", icon: BellIcon, match: ["/alerts"] },
  { key: "settings", href: "/settings", icon: SettingsIcon, match: ["/settings"] },
];

/** Which tab a path belongs to. `/` is Home; xray, import and a holding page belong to Portfolio. */
export function activeTab(pathname: string): TabKey | null {
  const p = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  if (p === "/" || p === "") return "home";
  return TABS.find((t) => t.match.some((m) => p === m || p.startsWith(`${m}/`)))?.key ?? null;
}

export function TabBar() {
  const t = useTranslations("nav");
  const active = activeTab(usePathname());
  return (
    <nav
      aria-label={t("tabs")}
      className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-surface/95 pb-[var(--safe-b)] backdrop-blur md:hidden"
    >
      <ul className="mx-auto flex max-w-lg">
        {TABS.map(({ key, href, icon: Icon }) => {
          const on = active === key;
          return (
            <li key={key} className="flex-1">
              <Link
                href={href}
                aria-current={on ? "page" : undefined}
                className={`flex min-h-[var(--tabbar-h)] min-w-11 flex-col items-center justify-center gap-0.5 px-1 text-[0.6875rem] font-medium ${on ? "text-brand-text" : "text-muted"}`}
              >
                <span className={`flex h-7 w-12 items-center justify-center rounded-full ${on ? "bg-brand-soft" : ""}`}><Icon className="h-5 w-5" /></span>
                <span>{t(key)}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

function DesktopNav() {
  const t = useTranslations("nav");
  const active = activeTab(usePathname());
  return (
    <nav aria-label={t("main")} className="ms-4 hidden flex-1 gap-1 md:flex">
      {TABS.map(({ key, href, icon: Icon }) => {
        const on = active === key;
        return (
          <Link
            key={key}
            href={href}
            aria-current={on ? "page" : undefined}
            className={`inline-flex min-h-11 items-center gap-2 rounded-xl px-3 text-sm font-medium ${on ? "bg-brand-soft text-brand-text" : "text-muted hover:bg-surface-2"}`}
          >
            <Icon className="h-5 w-5" />
            {t(key)}
          </Link>
        );
      })}
    </nav>
  );
}

/** Applies the saved theme on every screen (the phone's setting decides while it is "system" or not yet loaded). */
function ThemeSync() {
  const { data } = useSettings();
  useEffect(() => { if (data) applyTheme(data.theme); }, [data]);
  return null;
}

/**
 * Applies the saved main currency and number format to the shared formatter, and re-keys the page content when
 * they change so every figure on screen re-renders. `stable` (Settings pages show no money) keeps state such as
 * the "saved" confirmation.
 */
function FormatKeyed({ children, stable }: { children: ReactNode; stable?: boolean }) {
  const { data } = useSettings();
  // Set before the children render (silently: this is a render), then the key re-mounts them with the new look.
  if (data) setFormatPrefs({ mainCurrency: data.main_currency, numberFormat: data.number_format }, { notify: false });
  const key = data ? `${data.main_currency}-${data.number_format}` : "ILS-full";
  return <Fragment key={stable ? "stable" : key}>{children}</Fragment>;
}

export function AppShell({ children, stable }: { children: ReactNode; stable?: boolean }) {
  const t = useTranslations("nav");
  const a = useTranslations("actions");
  const app = useTranslations("app");
  const router = useRouter();
  const [menu, setMenu] = useState(false);
  const close = useCallback(() => setMenu(false), []);
  return (
    <AuthGate>
      <ThemeSync />
      <div className="min-h-dvh pb-[calc(var(--tabbar-h)+var(--safe-b))] md:pb-0">
        <header className="sticky top-0 z-20 border-b border-line bg-surface/90 backdrop-blur">
          <div className="mx-auto flex max-w-5xl items-center gap-2 px-4 py-2">
            <Link href="/" className="min-h-11 content-center font-bold text-brand-text">{app("name")}</Link>
            <DesktopNav />
            <div className="ms-auto hidden md:block">
              <button type="button" aria-haspopup="dialog" aria-label={a("open")} onClick={() => setMenu(true)} className="btn-primary">
                <PlusIcon className="h-5 w-5" />
                {a("openShort")}
              </button>
            </div>
            <button
              type="button"
              className="ms-auto min-h-11 rounded-xl px-3 text-sm text-muted hover:bg-surface-2 md:ms-0"
              onClick={async () => { try { await api.logout(); } finally { router.replace("/login"); } }}
            >
              {t("logout")}
            </button>
          </div>
        </header>
        <main id="main" className="mx-auto max-w-5xl space-y-4 px-4 py-4 pb-24 md:pb-4"><FormatKeyed stable={stable}>{children}</FormatKeyed></main>
        <Disclaimer />
      </div>
      <button
        type="button"
        aria-haspopup="dialog"
        aria-label={a("open")}
        onClick={() => setMenu(true)}
        className="fixed bottom-[calc(var(--tabbar-h)+var(--safe-b)+0.75rem)] end-4 z-30 flex h-14 w-14 items-center justify-center rounded-full bg-brand text-brand-on shadow-lg hover:bg-brand-hover md:hidden"
      >
        <PlusIcon className="h-7 w-7" />
      </button>
      <TabBar />
      {menu && <ActionsSheet onClose={close} />}
    </AuthGate>
  );
}
