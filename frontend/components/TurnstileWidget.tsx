"use client";
import { useEffect, useRef, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { isMock } from "@/lib/api";
import { MOCK_TURNSTILE_TOKEN } from "@/lib/mock";
import { loadTurnstile } from "@/lib/turnstile";

export interface TurnstileProps {
  siteKey: string;
  onToken: (token: string | null) => void;
}

/** Offline stand-in used in mock mode and tests: no network, one click yields a token. */
export function StubTurnstile({ siteKey, onToken }: TurnstileProps) {
  const t = useTranslations("auth");
  return (
    <div data-testid="turnstile-stub" data-sitekey={siteKey}>
      <button type="button" className="btn-secondary" onClick={() => onToken(MOCK_TURNSTILE_TOKEN)}>{t("challengeLabel")}</button>
    </div>
  );
}

function CloudflareTurnstile({ siteKey, onToken }: TurnstileProps) {
  const t = useTranslations("auth");
  const locale = useLocale();
  const host = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  const cb = useRef(onToken);
  useEffect(() => { cb.current = onToken; });
  useEffect(() => {
    let id: string | null = null;
    let gone = false;
    loadTurnstile().then((api) => {
      if (gone || !host.current) return;
      id = api.render(host.current, {
        sitekey: siteKey,
        theme: "auto",
        language: locale === "he" ? "he" : "en",
        callback: (tok) => cb.current(tok),
        "expired-callback": () => cb.current(null),
        "error-callback": () => { cb.current(null); setFailed(true); },
      });
    }).catch(() => { if (!gone) setFailed(true); });
    return () => { gone = true; if (id && window.turnstile) window.turnstile.remove(id); };
  }, [siteKey, locale]);
  return (
    <div>
      <div ref={host} data-testid="turnstile-real" aria-label={t("challengeLabel")} />
      {failed && <p role="alert" className="text-sm text-red-700 dark:text-red-400">{t("challengeLoadError")}</p>}
    </div>
  );
}

/** The challenge widget: the stub in mock mode, Cloudflare's script otherwise (loaded only now). */
export function TurnstileWidget(props: TurnstileProps) {
  return isMock() ? <StubTurnstile {...props} /> : <CloudflareTurnstile {...props} />;
}
