"use client";
import { useCallback, useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { api, ApiError, type Settings, type SettingsPatch } from "@/lib/api";
import { useSettings } from "@/lib/hooks";

export type SaveState = "idle" | "saving" | "saved" | "error";

/** Saves a partial settings patch right away (no big Save button). Returns the new settings, or null on failure. */
export function useSettingsSave() {
  const { mutate } = useSettings();
  const [state, setState] = useState<SaveState>("idle");
  const [status, setStatus] = useState<number | null>(null);
  const save = useCallback(async (patch: SettingsPatch): Promise<Settings | null> => {
    setState("saving"); setStatus(null);
    try {
      const next = await api.patchSettings(patch);
      await mutate(next, { revalidate: false });
      setState("saved");
      return next;
    } catch (e) {
      setStatus(e instanceof ApiError ? e.status : 0);
      setState("error");
      return null;
    }
  }, [mutate]);
  return { save, state, status };
}

export function SaveStatus({ state, status }: { state: SaveState; status?: number | null }) {
  const t = useTranslations("prefs");
  if (state === "saving") return <span role="status" className="text-sm text-muted">{t("saving")}</span>;
  if (state === "saved") return <span role="status" className="text-sm text-gain">✓ {t("saved")}</span>;
  if (state === "error") return <span role="alert" className="text-sm text-loss">{status === 422 ? t("invalid") : t("saveError")}</span>;
  return null;
}

/** One labelled choice among a few (radio group). `value` null = nothing chosen yet (no defaults). */
export function Choice<T extends string>({ label, help, value, options, onPick, disabled }: {
  label: string; help?: string; value: T | null; options: { value: T; label: string }[]; onPick: (v: T) => void; disabled?: boolean;
}) {
  return (
    <div className="space-y-1">
      <p className="label" id={`lbl-${label}`}>{label}</p>
      {help && <p className="text-caption text-muted">{help}</p>}
      <div role="radiogroup" aria-labelledby={`lbl-${label}`} className="flex flex-wrap gap-2">
        {options.map((o) => (
          <button key={o.value} type="button" role="radio" aria-checked={value === o.value} disabled={disabled} onClick={() => onPick(o.value)} className={value === o.value ? "btn-primary" : "btn-secondary"}>
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export function SettingsCard({ title, intro, children }: { title: string; intro?: string; children: ReactNode }) {
  return (
    <section className="card space-y-4" aria-label={title}>
      <div>
        <h2 className="text-heading">{title}</h2>
        {intro && <p className="text-sm text-muted">{intro}</p>}
      </div>
      {children}
    </section>
  );
}

/** Loads settings once for a screen and shows the loading / error states. */
export function WithSettings({ children }: { children: (s: Settings) => ReactNode }) {
  const t = useTranslations("prefs");
  const c = useTranslations("common");
  const { data, error, mutate } = useSettings();
  if (error) return <div role="alert" className="card space-y-2"><p>{t("loadError")}</p><button type="button" className="btn-secondary" onClick={() => void mutate()}>{c("retry")}</button></div>;
  if (!data) return <p role="status" className="text-muted">{c("loading")}</p>;
  return <>{children(data)}</>;
}

export const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;
