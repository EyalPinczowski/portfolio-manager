"use client";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { HoldingPage } from "./HoldingPage";
import { AppShell } from "./AppShell";

function FromQuery() {
  const t = useTranslations("holding");
  const raw = useSearchParams().get("id") ?? "";
  if (!/^\d+$/.test(raw)) return <AppShell><p role="alert">{t("notFound")}</p></AppShell>;
  return <HoldingPage id={Number(raw)} />;
}

/** `/[locale]/holding?id=123`; useSearchParams needs a Suspense boundary for static prerendering. */
export function HoldingRoute() {
  const t = useTranslations("common");
  return (
    <Suspense fallback={<p className="p-6 text-center text-slate-500" role="status">{t("loading")}</p>}>
      <FromQuery />
    </Suspense>
  );
}
