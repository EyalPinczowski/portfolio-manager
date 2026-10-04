"use client";
import { useEffect, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { ApiError } from "@/lib/api";
import { useMe } from "@/lib/hooks";
import { useRouter } from "@/i18n/navigation";

/** Renders children only for a signed-in user; redirects to login on 401. */
export function AuthGate({ children }: { children: ReactNode }) {
  const { data, error } = useMe();
  const router = useRouter();
  const t = useTranslations("common");
  const unauth = error instanceof ApiError && error.status === 401;
  useEffect(() => { if (unauth) router.replace("/login"); }, [unauth, router]);
  if (data) return <>{children}</>;
  if (error && !unauth) return <p role="alert" className="p-6 text-center">{t("errorLoad")}</p>;
  return <p className="p-6 text-center text-muted" role="status">{t("loading")}</p>;
}
