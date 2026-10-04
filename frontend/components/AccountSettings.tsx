"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import useSWR from "swr";
import { api, ApiError } from "@/lib/api";
import { formatDate, formatTime } from "@/lib/format";
import { useMe } from "@/lib/hooks";
import { settingsHref } from "@/lib/routes";
import { useRouter } from "@/i18n/navigation";
import { DeviceGlyph, DownloadGlyph, LogoutGlyph, TrashGlyph } from "./SettingsIcons";
import { ConfirmSheet, SettingsGroup, SettingsRow } from "./SettingsUI";

type PasswordAction = "export" | "delete";

/** Asks for the account password (export/delete need it; a wrong one gives 403). Shown as a bottom sheet. */
function PasswordSheet({ action, onDone, onCancel }: { action: PasswordAction; onDone: () => void; onCancel: () => void }) {
  const t = useTranslations("settings");
  const c = useTranslations("common");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const isDelete = action === "delete";
  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      if (action === "export") {
        const data = await api.exportData(password);
        const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
        try {
          const a = document.createElement("a");
          a.href = url; a.download = "portfolio-manager-export.json"; a.click();
        } finally { URL.revokeObjectURL(url); }
      } else {
        await api.deleteAccount(password);
      }
      onDone();
    } catch (error) {
      const s = error instanceof ApiError ? error.status : 0;
      setErr(s === 403 ? t("wrongPassword") : s === 429 ? t("tooMany") : c("errorLoad"));
    } finally { setBusy(false); setPassword(""); }
  };
  return (
    <ConfirmSheet
      title={isDelete ? t("deleteTitle") : t("exportTitle")} body={isDelete ? t("deleteBody") : t("exportBody")}
      confirmLabel={isDelete ? t("deleteYes") : t("exportYes")} cancelLabel={c("cancel")} busy={busy} confirmDisabled={password === ""}
      onConfirm={() => void submit()} onCancel={onCancel}
    >
      <form onSubmit={(e) => { e.preventDefault(); if (password !== "") void submit(); }}>
        <label htmlFor="confirm-password" className="label">{t("confirmPassword")}</label>
        <input id="confirm-password" className="input" type="password" required autoComplete="current-password" dir="ltr" value={password} onChange={(e) => setPassword(e.target.value)} />
      </form>
      {err && <p role="alert" className="text-sm text-loss">{err}</p>}
    </ConfirmSheet>
  );
}

/** Account page: sessions and data export up top; sign out and delete (red) at the bottom, each behind a confirm sheet. */
export function AccountScreen() {
  const t = useTranslations("prefs");
  const n = useTranslations("nav");
  const c = useTranslations("common");
  const p = useTranslations("settings");
  const router = useRouter();
  const { data: me } = useMe();
  const [prompt, setPrompt] = useState<PasswordAction | "logout" | null>(null);
  const [busy, setBusy] = useState(false);
  const logout = async () => { setBusy(true); try { await api.logout(); } finally { setBusy(false); router.replace("/login"); } };
  return (
    <>
      {me && <p className="px-4 text-sm text-muted">{t("account.signedIn")}: <span dir="ltr" className="font-medium text-fg">{me.email}</span></p>}
      <SettingsGroup label={t("account.dataGroup")} footer={t("account.footerSecurity")}>
        <SettingsRow label={t("account.sessionsRow")} icon={<DeviceGlyph />} tone="blue" href={settingsHref("sessions")} />
        <SettingsRow label={t("account.exportRow")} icon={<DownloadGlyph />} tone="green" chevron onClick={() => setPrompt("export")} />
      </SettingsGroup>
      <SettingsGroup label={t("account.dangerGroup")} footer={p("deleteWarn")}>
        <SettingsRow label={n("logout")} icon={<LogoutGlyph />} tone="gray" destructive onClick={() => setPrompt("logout")} />
        <SettingsRow label={p("deleteAccount")} icon={<TrashGlyph />} tone="red" destructive onClick={() => setPrompt("delete")} />
      </SettingsGroup>
      {prompt === "logout" && (
        <ConfirmSheet title={t("account.logoutTitle")} body={t("account.logoutBody")} confirmLabel={t("account.logoutYes")} cancelLabel={c("cancel")} busy={busy} onConfirm={() => void logout()} onCancel={() => setPrompt(null)} />
      )}
      {(prompt === "export" || prompt === "delete") && (
        <PasswordSheet
          action={prompt} onCancel={() => setPrompt(null)}
          onDone={() => { const was = prompt; setPrompt(null); if (was === "delete") router.replace("/login"); }}
        />
      )}
    </>
  );
}

export function SessionsScreen() {
  const t = useTranslations("settings");
  const locale = useLocale();
  const { data, error, mutate: refresh } = useSWR("sessions", () => api.sessions());
  const [fail, setFail] = useState(false);
  const run = async (fn: () => Promise<unknown>) => {
    setFail(false);
    try { await fn(); await refresh(); } catch { setFail(true); }
  };
  const when = (iso: string) => `${formatDate(iso)} ${formatTime(iso, locale)}`;
  const others = (data ?? []).filter((x) => !x.current);
  return (
    <>
      {error && <p role="alert" className="px-4 text-sm">{t("sessionsError")}</p>}
      {data && (
        <SettingsGroup label={t("sessions")} footer={t("sessionsDesc")} role="list">
          {data.map((x) => (
            <li key={x.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 text-sm">
              <div>
                <p className="font-semibold">{x.current ? t("sessionCurrent") : t("sessionOther")}</p>
                <p className="text-xs text-muted">{t("sessionSignedIn", { time: when(x.created_at) })} · {t("sessionLastSeen", { time: when(x.last_seen_at) })}</p>
              </div>
              {!x.current && <button type="button" className="btn-secondary" onClick={() => run(() => api.revokeSession(x.id))}>{t("sessionRevoke")}</button>}
            </li>
          ))}
        </SettingsGroup>
      )}
      {others.length > 0 && (
        <SettingsGroup label={t("sessionRevokeAll")}>
          <SettingsRow label={t("sessionRevokeAll")} destructive onClick={() => void run(() => api.revokeOtherSessions())} />
        </SettingsGroup>
      )}
      {fail && <p role="alert" className="px-4 text-sm text-loss">{t("sessionsError")}</p>}
    </>
  );
}
