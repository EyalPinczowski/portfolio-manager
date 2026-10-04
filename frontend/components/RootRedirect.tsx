"use client";
import { useEffect } from "react";
import { routing } from "@/i18n/routing";

/** Static hosts have no middleware, so `/` picks a locale in the browser: English browsers get /en, others /he. */
export function RootRedirect() {
  useEffect(() => {
    const lang = (typeof navigator !== "undefined" ? navigator.language : "").toLowerCase();
    const locale = routing.locales.find((l) => lang.startsWith(l)) ?? routing.defaultLocale;
    window.location.replace(`/${locale}/`);
  }, []);
  return null;
}
