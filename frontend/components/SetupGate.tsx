"use client";
import type { ReactNode } from "react";
import { useTranslations } from "next-intl";
import { usePathname } from "@/i18n/navigation";
import { useSetupGate } from "@/lib/setup";
import { FirstRunGuide } from "./FirstRunGuide";

/** Pages the setup steps need, so they stay reachable while setup is unfinished. Home shows the setup itself. */
const OPEN_PREFIXES = ["/import", "/terms", "/settings", "/login", "/signup"];
export const isSetupOpenPath = (pathname: string): boolean => {
  const p = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  return p === "/" || p === "" || OPEN_PREFIXES.some((x) => p === x || p.startsWith(`${x}/`));
};

/** Until the required setup is done, other pages show the setup screen with a short reason instead of the page. */
export function SetupGate({ children }: { children: ReactNode }) {
  const t = useTranslations("guide");
  const c = useTranslations("common");
  const pathname = usePathname();
  const state = useSetupGate();
  if (isSetupOpenPath(pathname) || state === "open") return <>{children}</>;
  if (state === "loading") return <p role="status" className="text-muted">{c("loading")}</p>;
  return (
    <div className="space-y-4" data-testid="setup-gate">
      <p className="card" role="status">{t("gateWhy")}</p>
      <FirstRunGuide portfolios={[]} />
    </div>
  );
}
