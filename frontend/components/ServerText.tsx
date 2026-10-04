"use client";
import { useTranslations } from "next-intl";
import { isKnownTextCode, sourceKey, type TextCodeIn } from "@/lib/server-text";

/** Translates a server `TextCode` (code + params); without a known code it shows the English text in a <bdi>. */
export function useServerText() {
  const t = useTranslations("serverText");
  const ex = useTranslations("exit");
  const st = useTranslations("settings");
  const src = (s: string) => {
    const k = sourceKey(s);
    return k ? ex(`sources.${k.key}` as "sources.atr", { n: k.n ?? "" }) : s.replaceAll("_", " ");
  };
  return (tc: TextCodeIn | null | undefined): string | null => {
    if (!isKnownTextCode(tc)) return null;
    const p: Record<string, string | number> = { ...(tc.params ?? {}) };
    if (typeof p.source === "string") p.source = src(p.source);
    if (typeof p.profile === "string") p.profile = st.has(`presets.${p.profile}`) ? st(`presets.${p.profile}`) : p.profile.replaceAll("_", " ");
    if (tc.code.startsWith("exposure_")) {
      const name = String(p.name ?? "");
      p.label = p.dimension === "position" ? t("labelPosition") : p.dimension === "sector" ? t("labelSector", { name }) : t("labelCountry", { name });
    }
    return t(tc.code as "stop_atr", p);
  };
}

export function ServerText({ code, text, className }: { code?: TextCodeIn | null; text: string; className?: string }) {
  const tr = useServerText()(code);
  return <span className={className} dir="auto">{tr ?? <bdi dir="auto">{text}</bdi>}</span>;
}
