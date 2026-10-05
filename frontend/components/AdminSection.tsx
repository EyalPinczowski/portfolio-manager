"use client";
import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { api, type AdminUser, type Invite } from "@/lib/api";
import { formatDate, formatNumber } from "@/lib/format";
import { useAdminInvites, useAdminLlmUsage, useAdminUsers } from "@/lib/hooks";
import { parseLocaleNumber } from "@/lib/number";
import { GroupItem, SettingsGroup } from "./SettingsUI";

function Invites() {
  const t = useTranslations("prefs.admin");
  const { data, error, mutate } = useAdminInvites(true);
  const [days, setDays] = useState("");
  const [err, setErr] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const n = days.trim() === "" ? null : parseLocaleNumber(days);
  const daysOk = days.trim() === "" || (n !== null && Number.isInteger(n) && n >= 1);
  const run = async (fn: () => Promise<unknown>) => { setErr(false); try { await fn(); await mutate(); } catch { setErr(true); } };
  const copy = async (code: string) => { try { await navigator.clipboard.writeText(code); setCopied(code); } catch { /* clipboard blocked */ } };
  return (
    <>
      <SettingsGroup title={t("invites")} footer={t("invitesHelp")}>
      <GroupItem>
      <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); if (daysOk) void run(async () => { await api.adminCreateInvite(n); setDays(""); }); }}>
        <div>
          <label htmlFor="inv-days" className="label">{t("days")}</label>
          <input id="inv-days" className="input w-32" inputMode="numeric" dir="ltr" value={days} onChange={(e) => setDays(e.target.value)} aria-describedby="inv-days-h" />
          <p id="inv-days-h" className="text-caption text-muted">{t("daysHelp")}</p>
        </div>
        <button type="submit" className="btn-primary" disabled={!daysOk}>{t("create")}</button>
      </form>
      </GroupItem>
      </SettingsGroup>
      {error ? <p role="alert" className="px-4 text-loss">{t("loadError")}</p> : !data ? null : data.length === 0 ? <p className="px-4 text-sm text-muted">{t("inviteNone")}</p> : (
        <SettingsGroup label={t("invites")} role="list">
          {data.map((i: Invite) => (
            <li key={i.code} className="flex flex-wrap items-center justify-between gap-2 px-4 py-3 text-sm">
              <div>
                <p><code dir="ltr" className="font-mono font-semibold">{i.code}</code>{" "}<span className={i.status === "unused" ? "chip-brand" : "chip-neutral"}>{t(`status.${i.status}`)}</span></p>
                <p className="text-caption text-muted">{t("created", { date: formatDate(i.created_at) })} · {t("expires", { date: formatDate(i.expires_at) })}</p>
              </div>
              <div className="flex gap-2">
                <button type="button" className="btn-secondary" onClick={() => void copy(i.code)}>{copied === i.code ? "✓" : t("copyCode")}</button>
                {i.status === "unused" && <button type="button" className="btn-secondary" onClick={() => void run(() => api.adminRevokeInvite(i.code))}>{t("revoke")}</button>}
              </div>
            </li>
          ))}
        </SettingsGroup>
      )}
      {err && <p role="alert" className="px-4 text-sm text-loss">{t("error")}</p>}
    </>
  );
}

function Users() {
  const t = useTranslations("prefs.admin");
  const c = useTranslations("common");
  const { data, error, mutate } = useAdminUsers();
  const [confirm, setConfirm] = useState<number | null>(null);
  const [err, setErr] = useState(false);
  const run = async (fn: () => Promise<unknown>) => { setErr(false); try { await fn(); setConfirm(null); await mutate(); } catch { setErr(true); } };
  return (
    <>
      {error ? <p role="alert" className="px-4 text-loss">{t("loadError")}</p> : !data ? null : (
        <SettingsGroup title={t("users")} footer={t("usersHelp")} role="list">
          {data.map((u: AdminUser) => (
            <li key={u.id} className="space-y-2 px-4 py-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="min-w-0">
                  <p className="break-all font-semibold" dir="ltr">{u.email}</p>
                  <p className="text-caption text-muted">{t("joined", { date: formatDate(u.created_at) })} · {u.last_seen_at ? t("lastSeen", { date: formatDate(u.last_seen_at) }) : t("neverSeen")}</p>
                  <p className="mt-1 flex flex-wrap gap-1.5">
                    <span className={u.active ? "chip-brand" : "chip-warn"}>{u.active ? t("active") : t("disabled")}</span>
                    {u.is_admin && <span className="chip-neutral">{t("adminBadge")}</span>}
                    {u.telegram_linked && <span className="chip-neutral">{t("telegram")}</span>}
                  </p>
                </div>
                {!u.is_admin && (u.active
                  ? confirm === u.id ? null : <button type="button" className="btn-secondary" onClick={() => setConfirm(u.id)}>{t("disable")}</button>
                  : <button type="button" className="btn-secondary" onClick={() => void run(() => api.adminEnableUser(u.id))}>{t("enable")}</button>)}
              </div>
              {confirm === u.id && (
                <div className="space-y-2" role="group" aria-label={t("disable")}>
                  <p>{t("disableConfirm", { email: u.email })}</p>
                  <div className="flex gap-2">
                    <button type="button" className="btn-danger" onClick={() => void run(() => api.adminDisableUser(u.id))}>{t("disableYes")}</button>
                    <button type="button" className="btn-secondary" onClick={() => setConfirm(null)}>{c("cancel")}</button>
                  </div>
                </div>
              )}
            </li>
          ))}
        </SettingsGroup>
      )}
      {err && <p role="alert" className="px-4 text-sm text-loss">{t("error")}</p>}
    </>
  );
}

/** Today's free-tier AI use against the daily budget. Totals only: no prompts, no user data. */
function AiUsage() {
  const t = useTranslations("prefs.admin.aiUsage");
  const locale = useLocale();
  const { data, error } = useAdminLlmUsage();
  if (error) return <p role="alert" className="px-4 text-loss">{t("loadError")}</p>;
  if (!data) return null;
  const n = (v: number) => formatNumber(v, locale, 0);
  return (
    <SettingsGroup title={t("title")} footer={t("footer", { user: n(data.user_daily_budget), rpm: n(data.requests_per_minute) })} role="list">
      {data.providers.length === 0 ? <li className="px-4 py-3 text-sm text-muted">{t("none")}</li> : data.providers.map((p) => (
        <li key={p.provider} className="px-4 py-3 text-sm" data-testid="ai-usage-row">
          <p className="font-semibold" dir="ltr">{p.provider}</p>
          <p className="text-caption text-muted">{t("row", { requests: n(p.requests), budget: n(data.daily_budget), tokens: n(p.tokens), fallbacks: n(p.fallbacks) })}</p>
          {data.tokens_in_out_recorded && <p className="text-caption text-muted">{t("detail", { input: n(p.tokens_in), output: n(p.tokens_out), cached: n(p.tokens_cached), hits: n(p.cache_hits) })}</p>}
        </li>
      ))}
      {!(data.tokens_in_out_recorded && data.cache_hits_recorded) && <li className="px-4 py-3 text-caption text-muted">{t("notRecorded")}</li>}
    </SettingsGroup>
  );
}

/** Admin screen. A member gets 403 from the probe, so it shows a short "admins only" note and no data. */
export function AdminScreen() {
  const t = useTranslations("prefs.admin");
  const { data, error } = useAdminUsers();
  if (error) return <p className="card" role="alert">{t("notAllowed")}</p>;
  if (!data) return null;
  return <><AiUsage /><Invites /><Users /></>;
}
