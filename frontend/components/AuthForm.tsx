"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { api, ApiError } from "@/lib/api";
import { Link, useRouter } from "@/i18n/navigation";
import { Disclaimer } from "./Disclaimer";
import { TurnstileWidget } from "./TurnstileWidget";
import { LogoMark, Wordmark } from "./Logo";

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
  const [challenge, setChallenge] = useState<{ siteKey: string; round: number } | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const signup = mode === "signup";

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (signup && !accepted) return;
    if (!signup && challenge && !token) { setError(t("challengeMissing")); return; }
    setBusy(true); setError(null);
    const sent = token;
    try {
      if (signup) await api.signup({ invite_code: invite.trim(), email, password, accept_disclaimer: true, locale });
      else await api.login({ email, password, ...(sent ? { turnstile_token: sent } : {}) });
      router.replace("/");
    } catch (err) {
      const ch = !signup && err instanceof ApiError ? err.challenge : null;
      if (ch) {
        // A Turnstile token is single-use: always start a fresh widget. A repeat 403 after sending one means it was bad.
        setToken(null);
        setChallenge((c) => ({ siteKey: ch.siteKey, round: (c?.round ?? 0) + 1 }));
        setError(sent ? t("challengeInvalid") : t("challengeBody"));
        return;
      }
      const s = err instanceof ApiError ? err.status : 0;
      const wait = err instanceof ApiError ? err.retryAfter : undefined;
      setError(s === 429 ? (wait ? t("tooManyWait", { seconds: wait }) : t("tooMany")) : s === 401 && !signup ? t("invalidCredentials") : t("failed"));
    } finally { setBusy(false); }
  };

  return (
    <>
      <main className="mx-auto max-w-md space-y-4 px-4 py-10">
        <div className="flex flex-col items-center text-center">
          <LogoMark className="mb-2 h-14 w-14" />
          <h1 className="text-2xl font-bold text-fg"><Wordmark name={app("name")} /></h1>
          <p className="text-sm text-muted">{app("tagline")}</p>
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
          {challenge && !signup && (
            <fieldset className="space-y-2 rounded-xl border border-line p-3 ">
              <legend className="px-1 text-sm font-semibold">{t("challengeTitle")}</legend>
              <TurnstileWidget key={challenge.round} siteKey={challenge.siteKey} onToken={setToken} />
            </fieldset>
          )}
          {error && <p role="alert" className="text-sm text-loss">{error}</p>}
          <button type="submit" className="btn-primary w-full" disabled={busy || (signup && !accepted)}>
            {busy ? t("working") : signup ? t("signup") : t("login")}
          </button>
          <p className="text-center text-sm">
            {signup ? t("haveAccount") : t("noAccount")}{" "}
            <Link href={signup ? "/login" : "/signup"} className="font-semibold text-brand-text underline">
              {signup ? t("login") : t("signup")}
            </Link>
          </p>
        </form>
      </main>
      <Disclaimer />
    </>
  );
}
