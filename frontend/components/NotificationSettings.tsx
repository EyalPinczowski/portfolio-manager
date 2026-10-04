"use client";
import { useEffect, useRef, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { api, ApiError, type LinkCode, type QuietHours, type Settings, type Weekday } from "@/lib/api";
import { formatTime } from "@/lib/format";
import { useTelegramStatus } from "@/lib/hooks";
import { SaveStatus, useSettingsSave, HHMM } from "./SettingsControls";
import { CalendarGlyph, SendGlyph } from "./SettingsIcons";
import { ConfirmSheet, FIELD, FieldRow, GroupItem, SettingsGroup, SettingsRow, ToggleRow } from "./SettingsUI";

const DAYS: Weekday[] = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"];

export function WeeklyReviewScreen({ s }: { s: Settings }) {
  const t = useTranslations("prefs.notifications");
  const { save, state, status } = useSettingsSave();
  const w = s.weekly_review;
  const [time, setTime] = useState(w.time);
  return (
    <>
      <SettingsGroup label={t("weeklyTitle")} footer={t("weeklyHelp")}>
        <ToggleRow label={t("weeklyOn")} checked={w.enabled} onChange={(enabled) => void save({ weekly_review: { enabled } })} icon={<CalendarGlyph />} tone="orange" />
        <FieldRow label={t("weeklyDay")} htmlFor="wr-day">
          <select id="wr-day" className={FIELD} value={w.day} onChange={(e) => void save({ weekly_review: { day: e.target.value as Weekday } })}>
            {DAYS.map((d) => <option key={d} value={d}>{t(`weekdays.${d}`)}</option>)}
          </select>
        </FieldRow>
        <FieldRow label={t("weeklyTime")} htmlFor="wr-time" help={t("weeklyZone", { zone: w.timezone })}>
          <input id="wr-time" type="time" dir="ltr" className={FIELD} value={time} onChange={(e) => { setTime(e.target.value); if (HHMM.test(e.target.value)) void save({ weekly_review: { time: e.target.value } }); }} />
        </FieldRow>
      </SettingsGroup>
      <SaveStatus state={state} status={status} />
    </>
  );
}

export function QuietHoursScreen({ s }: { s: Settings }) {
  const t = useTranslations("prefs.notifications");
  const { save, state, status } = useSettingsSave();
  const q = s.quiet_hours;
  const [start, setStart] = useState(q?.start ?? "");
  const [end, setEnd] = useState(q?.end ?? "");
  const valid = HHMM.test(start) && HHMM.test(end) && start !== end;
  const same = HHMM.test(start) && start === end;
  const patch = (v: QuietHours | null) => save({ quiet_hours: v });
  return (
    <>
      <SettingsGroup label={t("quietTitle")} footer={t("quietHelp")}>
        <GroupItem><p className="text-sm" role="status" data-testid="quiet-state">{q ? t("quietNow", { start: q.start, end: q.end }) : t("quietNone")}</p></GroupItem>
        <FieldRow label={t("quietFrom")} htmlFor="qh-start"><input id="qh-start" type="time" dir="ltr" className={FIELD} value={start} onChange={(e) => setStart(e.target.value)} /></FieldRow>
        <FieldRow label={t("quietTo")} htmlFor="qh-end"><input id="qh-end" type="time" dir="ltr" className={FIELD} value={end} onChange={(e) => setEnd(e.target.value)} /></FieldRow>
      </SettingsGroup>
      {same && <p className="px-4 text-sm text-warn-fg" role="status">{t("quietSame")}</p>}
      <div className="flex flex-wrap items-center gap-2 px-1">
        <button type="button" className="btn-primary" disabled={!valid || state === "saving"} onClick={() => void patch({ start, end })}>{t("quietSave")}</button>
        <button type="button" className="btn-secondary" disabled={!q || state === "saving"} onClick={async () => { if (await patch(null)) { setStart(""); setEnd(""); } }}>{t("quietClear")}</button>
        <SaveStatus state={state} status={status} />
      </div>
    </>
  );
}

function CodeBox({ code, onNew }: { code: LinkCode; onNew: () => void }) {
  const t = useTranslations("prefs.telegram");
  const locale = useLocale();
  const { data: st } = useTelegramStatus();
  const [copied, setCopied] = useState(false);
  const copy = async () => { try { await navigator.clipboard.writeText(code.command); setCopied(true); } catch { /* clipboard blocked: the text is selectable */ } };
  return (
    <div className="space-y-3" data-testid="link-code">
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

export function TelegramScreen() {
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
    try { await api.telegramUnlink(); setConfirm(false); await mutate(); } catch { setErr(t("errGeneric")); setConfirm(false); } finally { setBusy(false); }
  };

  if (error) return <p role="alert" className="card text-loss">{c("errorLoad")}</p>;
  if (!st) return <p role="status" className="text-muted">{c("loading")}</p>;
  return (
    <>
      <SettingsGroup label={t("title")} footer={t("help")}>
        <GroupItem>
          <p className={st.linked ? "chip-brand w-fit" : "chip-neutral w-fit"} role="status" data-testid="telegram-state">{st.linked ? t("connected") : t("notConnected")}</p>
          {!st.configured && !st.linked && <p className="mt-2 text-sm text-muted">{t("unavailable")}</p>}
        </GroupItem>
        {!st.linked && st.configured && !shownCode && <SettingsRow label={t("connect")} icon={<SendGlyph />} tone="blue" disabled={busy} onClick={() => void getCode()} />}
        {shownCode && <GroupItem><CodeBox code={shownCode} onNew={() => void getCode()} /></GroupItem>}
        {st.linked && <SettingsRow label={t("disconnect")} destructive onClick={() => setConfirm(true)} />}
      </SettingsGroup>
      {err && <p role="alert" className="px-4 text-sm text-loss">{err}</p>}
      {confirm && (
        <ConfirmSheet title={t("disconnect")} body={t("disconnectConfirm")} confirmLabel={t("disconnectYes")} cancelLabel={c("cancel")} busy={busy} onConfirm={() => void unlink()} onCancel={() => setConfirm(false)} />
      )}
    </>
  );
}
