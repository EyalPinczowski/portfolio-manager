/**
 * Cloudflare Turnstile loader. The script is requested ONLY when `loadTurnstile()` is called, which happens when
 * the server answers POST /auth/login with 403 `turnstile_required`. Nothing here runs on the first render.
 */
import { TURNSTILE_ORIGIN } from "./security-headers";

export interface TurnstileApi {
  render(el: HTMLElement, opts: {
    sitekey: string;
    callback: (token: string) => void;
    "expired-callback"?: () => void;
    "error-callback"?: () => void;
    theme?: "auto" | "light" | "dark";
    language?: string;
  }): string;
  remove(id: string): void;
  reset(id: string): void;
}
declare global { interface Window { turnstile?: TurnstileApi } }

export const TURNSTILE_SRC = `${TURNSTILE_ORIGIN}/turnstile/v0/api.js?render=explicit`;
let pending: Promise<TurnstileApi> | null = null;

export function loadTurnstile(): Promise<TurnstileApi> {
  if (typeof window === "undefined") return Promise.reject(new Error("no window"));
  if (window.turnstile) return Promise.resolve(window.turnstile);
  if (pending) return pending;
  pending = new Promise<TurnstileApi>((resolve, reject) => {
    const s = document.createElement("script");
    s.src = TURNSTILE_SRC;
    s.async = true;
    s.referrerPolicy = "strict-origin";
    s.onload = () => (window.turnstile ? resolve(window.turnstile) : reject(new Error("turnstile missing")));
    s.onerror = () => { pending = null; s.remove(); reject(new Error("turnstile load failed")); };
    document.head.appendChild(s);
  });
  return pending;
}

/** Test helper. */
export function resetTurnstileLoader(): void { pending = null; }
