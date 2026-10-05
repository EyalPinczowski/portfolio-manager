"use client";
import { useEffect, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { ApiError } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import { termsHref } from "@/lib/routes";
import { usePathname, useRouter } from "@/i18n/navigation";

/** Renders children only for a signed-in user who accepted the current terms; redirects to login on 401 and to the terms page otherwise. */
export function AuthGate({ children }: { children: ReactNode }) {
  const { data, error } = useMe();
  const router = useRouter();
  const pathname = usePathname();
  const t = useTranslations("common");
  const unauth = error instanceof ApiError && error.status === 401;
  const needTerms = !!data && data.terms_accepted === false;
  useEffect(() => { if (unauth) router.replace("/login"); }, [unauth, router]);
  useEffect(() => { if (needTerms) router.replace(termsHref(pathname)); }, [needTerms, router, pathname]);
  if (data && !needTerms) return <>{children}</>;
  if (error && !unauth) return <p role="alert" className="p-6 text-center">{t("errorLoad")}</p>;
  return <p className="p-6 text-center text-muted" role="status">{t("loading")}</p>;
}
