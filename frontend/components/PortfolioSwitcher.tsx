"use client";
import { useTranslations } from "next-intl";
import type { Portfolio } from "@/lib/api";
import type { PortfolioRef } from "@/lib/hooks";

export function PortfolioSwitcher({
  portfolios, value, onChange, allowCombined = true,
}: { portfolios: Portfolio[]; value: PortfolioRef; onChange: (v: PortfolioRef) => void; allowCombined?: boolean }) {
  const t = useTranslations("switcher");
  if (portfolios.length < 2) return null;
  return (
    <div>
      <label htmlFor="portfolio-switcher" className="label">{t("label")}</label>
      <select
        id="portfolio-switcher"
        className="input"
        value={String(value)}
        onChange={(e) => onChange(e.target.value === "combined" ? "combined" : Number(e.target.value))}
      >
        {allowCombined && <option value="combined">{t("combined")}</option>}
        {portfolios.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select>
    </div>
  );
}
