"use client";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { AnalyzeHome } from "./AnalyzeHome";
import { AnalyzeResult } from "./AnalyzeResult";
import { AppShell } from "./AppShell";
import { analyzeSymbol } from "@/lib/routes";

function FromQuery() {
  const symbol = analyzeSymbol(useSearchParams().get("symbol"));
  return <AppShell>{symbol ? <AnalyzeResult key={symbol} symbol={symbol} /> : <AnalyzeHome />}</AppShell>;
}

/** `/[locale]/analyze` = search + lists; `/analyze?symbol=TEVA.TA` = one analysis (query-based, so it works in a static export). */
export function AnalyzeRoute() {
  const t = useTranslations("common");
  return (
    <Suspense fallback={<p className="p-6 text-center text-muted" role="status">{t("loading")}</p>}>
      <FromQuery />
    </Suspense>
  );
}
