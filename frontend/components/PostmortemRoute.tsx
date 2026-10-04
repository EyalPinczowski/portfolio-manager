"use client";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { PostmortemPage } from "./PostmortemPage";

function FromQuery() {
  const q = useSearchParams();
  const id = q.get("id") ?? "";
  return <PostmortemPage initialId={/^\d+$/.test(id) ? Number(id) : null} initialStart={q.get("start")} initialEnd={q.get("end")} />;
}

/** `/[locale]/postmortem?id=1&start=&end=`; useSearchParams needs a Suspense boundary for static prerendering. */
export function PostmortemRoute() {
  const t = useTranslations("common");
  return (
    <Suspense fallback={<p className="p-6 text-center text-muted" role="status">{t("loading")}</p>}>
      <FromQuery />
    </Suspense>
  );
}
