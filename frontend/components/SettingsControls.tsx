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
