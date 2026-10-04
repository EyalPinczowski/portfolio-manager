import { defineRouting } from "next-intl/routing";

export const routing = defineRouting({
  locales: ["he", "en"],
  defaultLocale: "he",
});

export type Locale = (typeof routing.locales)[number];
export const isRtl = (l: string): boolean => l === "he";
