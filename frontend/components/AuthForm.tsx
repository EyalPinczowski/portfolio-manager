"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { api, ApiError } from "@/lib/api";
import { Link, useRouter } from "@/i18n/navigation";
import { Disclaimer } from "./Disclaimer";

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const t = useTranslations("auth");
  const app = useTranslations("app");
  const locale = useLocale();
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [invite, setInvite] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const signup = mode === "signup";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (signup && !accepted) return;
    setBusy(true); setError(null);
    try {
      if (signup) await api.signup({ invite_code: invite.trim(), email, password, accept_disclaimer: true, locale });
      else await api.login({ email, password });
      router.replace("/");
    } catch (err) {
      const s = err instanceof ApiError ? err.status : 0;
      const wait = err instanceof ApiError ? err.retryAfter : undefined;
      setError(s === 429 ? (wait ? t("tooManyWait", { seconds: wait }) : t("tooMany")) : s === 401 && !signup ? t("invalidCredentials") : t("failed"));
    } finally { setBusy(false); }
  };

  return (
    <>
      <main className="mx-auto max-w-md space-y-4 px-4 py-10">
        <div className="text-center">
          <h1 className="text-2xl font-bold text-blue-800 dark:text-blue-300">{app("name")}</h1>
          <p className="text-sm text-slate-600 dark:text-slate-400">{app("tagline")}</p>
        </div>
        <form onSubmit={submit} className="card space-y-4">
          <h2 className="text-xl font-bold">{signup ? t("signupTitle") : t("loginTitle")}</h2>
          {signup && (
            <div>
              <label htmlFor="invite" className="label">{t("inviteCode")}</label>
              <input id="invite" className="input" required value={invite} onChange={(e) => setInvite(e.target.value)} autoComplete="off" dir="ltr" />
            </div>
          )}
          <div>
            <label htmlFor="email" className="label">{t("email")}</label>
            <input id="email" className="input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" dir="ltr" />
          </div>
          <div>
            <label htmlFor="password" className="label">{t("password")}</label>
            <input id="password" className="input" type="password" required minLength={signup ? 10 : 1} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete={signup ? "new-password" : "current-password"} dir="ltr" />
          </div>
          {signup && (
            <label className="flex items-start gap-2 text-sm">
              <input type="checkbox" required checked={accepted} onChange={(e) => setAccepted(e.target.checked)} className="mt-1 h-5 w-5" />
              <span>{t("acceptDisclaimer")}</span>
            </label>
          )}
          {error && <p role="alert" className="text-sm text-red-700 dark:text-red-400">{error}</p>}
          <button type="submit" className="btn-primary w-full" disabled={busy || (signup && !accepted)}>
            {busy ? t("working") : signup ? t("signup") : t("login")}
          </button>
          <p className="text-center text-sm">
            {signup ? t("haveAccount") : t("noAccount")}{" "}
            <Link href={signup ? "/login" : "/signup"} className="font-semibold text-blue-800 underline dark:text-blue-300">
              {signup ? t("login") : t("signup")}
            </Link>
          </p>
        </form>
      </main>
      <Disclaimer />
    </>
  );
}
