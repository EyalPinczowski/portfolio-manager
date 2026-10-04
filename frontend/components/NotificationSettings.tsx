"use client";
import { useEffect, useRef, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { api, ApiError, type LinkCode, type QuietHours, type Settings, type Weekday } from "@/lib/api";
import { formatTime } from "@/lib/format";
import { useTelegramStatus } from "@/lib/hooks";
import { SaveStatus, SettingsCard, useSettingsSave, HHMM } from "./SettingsControls";

const DAYS: Weekday[] = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];

function WeeklyReview({ s }: { s: Settings }) {
  const t = useTranslations("prefs.notifications");
  const { save, state, status } = useSettingsSave();
  const w = s.weekly_review;
  const [time, setTime] = useState(w.time);
  return (
    <SettingsCard title={t("weeklyTitle")} intro={t("weeklyHelp")}>
      <label className="flex min-h-11 items-center gap-3">
        <input type="checkbox" className="h-5 w-5" checked={w.enabled} onChange={(e) => void save({ weekly_review: { enabled: e.target.checked } })} />
        <span>{t("weeklyOn")}</span>
      </label>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label htmlFor="wr-day" className="label">{t("weeklyDay")}</label>
          <select id="wr-day" className="input" value={w.day} onChange={(e) => void save({ weekly_review: { day: e.target.value as Weekday } })}>
            {DAYS.map((d) => <option key={d} value={d}>{t(`weekdays.${d}`)}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor="wr-time" className="label">{t("weeklyTime")}</label>
          <input id="wr-time" type="time" dir="ltr" className="input" value={time} onChange={(e) => { setTime(e.target.value); if (HHMM.test(e.target.value)) void save({ weekly_review: { time: e.target.value } }); }} />
        </div>
      </div>
      <p className="text-caption text-muted">{t("weeklyZone", { zone: w.timezone })}</p>
      <SaveStatus state={state} status={status} />
    </SettingsCard>
  );
}

function QuietHoursForm({ s }: { s: Settings }) {
  const t = useTranslations("prefs.notifications");
  const { save, state, status } = useSettingsSave();
  const q = s.quiet_hours;
  const [start, setStart] = useState(q?.start ?? "");
  const [end, setEnd] = useState(q?.end ?? "");
  const valid = HHMM.test(start) && HHMM.test(end) && start !== end;
  const same = HHMM.test(start) && start === end;
  const patch = (v: QuietHours | null) => save({ quiet_hours: v });
  return (
    <SettingsCard title={t("quietTitle")} intro={t("quietHelp")}>
      <p className="text-sm" role="status" data-testid="quiet-state">{q ? t("quietNow", { start: q.start, end: q.end }) : t("quietNone")}</p>
      <div className="grid grid-cols-2 gap-3">
        <div><label htmlFor="qh-start" className="label">{t("quietFrom")}</label><input id="qh-start" type="time" dir="ltr" className="input" value={start} onChange={(e) => setStart(e.target.value)} /></div>
        <div><label htmlFor="qh-end" className="label">{t("quietTo")}</label><input id="qh-end" type="time" dir="ltr" className="input" value={end} onChange={(e) => setEnd(e.target.value)} /></div>
      </div>
      {same && <p className="text-sm text-warn-fg" role="status">{t("quietSame")}</p>}
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="btn-primary" disabled={!valid || state === "saving"} onClick={() => void patch({ start, end })}>{t("quietSave")}</button>
        <button type="button" className="btn-secondary" disabled={!q || state === "saving"} onClick={async () => { if (await patch(null)) { setStart(""); setEnd(""); } }}>{t("quietClear")}</button>
        <SaveStatus state={state} status={status} />
      </div>
    </SettingsCard>
  );
}

function CodeBox({ code, onNew }: { code: LinkCode; onNew: () => void }) {
  const t = useTranslations("prefs.telegram");
  const locale = useLocale();
  const { data: st } = useTelegramStatus();
  const [copied, setCopied] = useState(false);
  const copy = async () => { try { await navigator.clipboard.writeText(code.command); setCopied(true); } catch { /* clipboard blocked: the text is selectable */ } };
  return (
    <div className="space-y-3 rounded-xl border border-line p-3" data-testid="link-code">
      <div>
        <h3 className="font-semibold">{t("codeTitle")}</h3>
        <p className="text-caption text-muted">{t("codeHelp", { minutes: code.ttl_minutes })} {t("codeUntil", { time: formatTime(code.expires_at, locale) })}</p>
      </div>
      <ol className="list-decimal space-y-2 ps-5 text-sm">
        <li>{t("step1", { bot: st?.bot_username ?? "" })}</li>
        <li>
          {t("step2")}
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <code dir="ltr" className="select-all rounded-lg bg-surface-2 px-3 py-2 font-mono" data-testid="link-command">{code.command}</code>
            <button type="button" className="btn-secondary" onClick={() => void copy()}>{copied ? t("copied") : t("copy")}</button>
          </div>
        </li>
        <li>{t("step3")}</li>
      </ol>
      <div className="flex flex-wrap gap-2">
        {code.deep_link && <a className="btn-primary" href={code.deep_link} target="_blank" rel="noopener noreferrer">{t("openBot")}</a>}
        <button type="button" className="btn-secondary" onClick={onNew}>{t("newCode")}</button>
      </div>
    </div>
  );
}

export function TelegramCard() {
  const t = useTranslations("prefs.telegram");
  const c = useTranslations("common");
  const { data: st, error, mutate } = useTelegramStatus();
  const [code, setCode] = useState<LinkCode | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const poll = useRef<ReturnType<typeof setInterval> | null>(null);

  // While a code is on screen, check every 3 s whether the bot has linked the chat.
  const shownCode = st?.linked ? null : code;
  useEffect(() => {
    if (!shownCode) return;
    poll.current = setInterval(() => void mutate(), 3000);
    return () => { if (poll.current) clearInterval(poll.current); };
  }, [shownCode, mutate]);

  const getCode = async () => {
    setBusy(true); setErr(null);
    try { setCode(await api.telegramLinkCode()); } catch (e) {
      const s = e instanceof ApiError ? e.status : 0;
      setErr(s === 503 ? t("errNotConfigured") : s === 429 ? t("errRate") : t("errGeneric"));
    } finally { setBusy(false); }
  };
  const unlink = async () => {
    setBusy(true); setErr(null);
    try { await api.telegramUnlink(); setConfirm(false); await mutate(); } catch { setErr(t("errGeneric")); } finally { setBusy(false); }
  };

  return (
    <SettingsCard title={t("title")} intro={t("help")}>
      {error ? <p role="alert" className="text-loss">{c("errorLoad")}</p> : !st ? <p role="status" className="text-muted">{c("loading")}</p> : (
        <>
          <p className={st.linked ? "chip-brand w-fit" : "chip-neutral w-fit"} role="status" data-testid="telegram-state">{st.linked ? t("connected") : t("notConnected")}</p>
          {!st.configured && !st.linked && <p className="text-sm text-muted">{t("unavailable")}</p>}
          {st.linked ? (
            confirm ? (
              <div className="space-y-2" role="group" aria-label={t("disconnect")}>
                <p className="text-sm">{t("disconnectConfirm")}</p>
                <div className="flex gap-2">
                  <button type="button" className="btn-danger" disabled={busy} onClick={() => void unlink()}>{t("disconnectYes")}</button>
                  <button type="button" className="btn-secondary" onClick={() => setConfirm(false)}>{c("cancel")}</button>
                </div>
              </div>
            ) : <button type="button" className="btn-secondary w-fit" onClick={() => setConfirm(true)}>{t("disconnect")}</button>
          ) : st.configured && !shownCode ? (
            <button type="button" className="btn-primary w-fit" disabled={busy} onClick={() => void getCode()}>{t("connect")}</button>
          ) : null}
          {shownCode && <CodeBox code={shownCode} onNew={() => void getCode()} />}
          {err && <p role="alert" className="text-sm text-loss">{err}</p>}
        </>
      )}
    </SettingsCard>
  );
}

export function NotificationsScreen({ s }: { s: Settings }) {
  const t = useTranslations("prefs.notifications");
  const { save, state, status } = useSettingsSave();
  return (
    <>
      <SettingsCard title={t("priceAlerts")} intro={t("priceAlertsHelp")}>
        <label className="flex min-h-11 items-center gap-3">
          <input type="checkbox" className="h-5 w-5" checked={s.price_alerts_enabled} onChange={(e) => void save({ price_alerts_enabled: e.target.checked })} />
          <span>{t("priceAlerts")}</span>
        </label>
        <SaveStatus state={state} status={status} />
      </SettingsCard>
      <WeeklyReview s={s} />
      <QuietHoursForm s={s} />
      <TelegramCard />
    </>
  );
}
