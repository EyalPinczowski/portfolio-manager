"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";
import { api, ApiError } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useMe, useTerms } from "@/lib/hooks";
import { safeNext } from "@/lib/routes";
import { Link, useRouter } from "@/i18n/navigation";
import { mutate } from "swr";
import { Disclaimer } from "./Disclaimer";
import { LogoMark, Wordmark } from "./Logo";

interface Section { title: string; body: string }

/**
 * The terms / disclaimer. Readable by anyone (no data from the gated API is needed); a signed-in user who has not
 * accepted the current version sees the required checkbox. After accepting, they go back to where they came from.
 */
export function TermsPage() {
  const t = useTranslations("terms");
  const app = useTranslations("app");
  const router = useRouter();
  const me = useMe();
  const terms = useTerms(!!me.data);
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const signedIn = !!me.data;
  const accepted = terms.data?.accepted === true;
  const sections = t.raw("sections") as Section[];
  const version = terms.data?.version;

  async function accept() {
    if (!agree || !terms.data) return;
    setBusy(true);
    setErr(null);
    try {
      await api.acceptTerms(terms.data.version);
      await Promise.all([mutate("me"), mutate("terms")]);
      const next = safeNext(new URLSearchParams(window.location.search).get("next"));
      router.replace(next ?? "/");
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 409 ? t("stale") : t("error"));
      setBusy(false);
    }
  }

  return (
    <div className="min-h-dvh">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-3xl items-center gap-2 px-4 py-2">
          <Link href="/" className="flex min-h-11 items-center gap-2 font-bold text-fg"><LogoMark className="h-7 w-7" /><Wordmark name={app("name")} /></Link>
        </div>
      </header>
      <main id="main" className="mx-auto max-w-3xl space-y-4 px-4 py-4">
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-muted">{t("intro")}</p>
        {version && <p className="text-caption text-muted" data-testid="terms-version">{t("versionLine", { version, date: formatDate(version) })}</p>}
        <ol className="space-y-3">
          {sections.map((s, i) => (
            <li key={s.title} className="card">
              <h2 className="text-heading">{i + 1}. {s.title}</h2>
              <p className="mt-1">{s.body}</p>
            </li>
          ))}
        </ol>
        {signedIn && !accepted && terms.data && (
          <form className="card space-y-3" onSubmit={(e) => { e.preventDefault(); void accept(); }}>
            <label className="flex items-start gap-3">
              <input type="checkbox" required checked={agree} onChange={(e) => setAgree(e.target.checked)} className="mt-1 h-5 w-5" />
              <span className="font-medium">{t("checkbox")}</span>
            </label>
            {err && <p role="alert" className="text-loss">{err}</p>}
            <button type="submit" className="btn-primary w-full" disabled={!agree || busy}>{busy ? t("accepting") : t("accept")}</button>
          </form>
        )}
        {signedIn && accepted && (
          <div className="card space-y-2">
            {terms.data?.accepted_at && <p role="status">{t("acceptedOn", { date: formatDate(terms.data.accepted_at) })}</p>}
            <Link href="/" className="btn-primary inline-flex">{t("backHome")}</Link>
          </div>
        )}
        {!signedIn && !me.isLoading && (
          <div className="card space-y-2">
            <p>{t("readOnly")}</p>
            <Link href="/login" className="btn-primary inline-flex">{t("signIn")}</Link>
          </div>
        )}
      </main>
      <Disclaimer />
    </div>
  );
}
