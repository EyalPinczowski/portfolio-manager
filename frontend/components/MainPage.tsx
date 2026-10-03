"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { useGuideState } from "@/lib/guide";
import { loadPortfolioChoice, savePortfolioChoice, useHoldings, usePortfolios, useSummary, type PortfolioRef } from "@/lib/hooks";
import { AppShell } from "./AppShell";
import { CreatePortfolio } from "./CreatePortfolio";
import { FirstRunGuide } from "./FirstRunGuide";
import { HoldingsList } from "./HoldingsList";
import { LiveHeader } from "./LiveHeader";
import { PnlStrip } from "./PnlStrip";
import { PortfolioSwitcher } from "./PortfolioSwitcher";

function Body() {
  const t = useTranslations();
  const { data: portfolios, error: pErr } = usePortfolios();
  const [choice, setChoice] = useState<PortfolioRef>(() => loadPortfolioChoice() ?? "combined");
  const guide = useGuideState();
  const valid = choice === "combined" || portfolios?.some((p) => p.id === choice);
  const ref: PortfolioRef = valid ? choice : "combined";
  const summary = useSummary(portfolios && portfolios.length > 0 ? ref : null);
  const holdings = useHoldings(portfolios ? ref : null, portfolios);

  if (pErr) return <p role="alert">{t("common.errorLoad")}</p>;
  if (!portfolios) return <p role="status" className="text-muted">{t("common.loading")}</p>;
  if (portfolios.length === 0) return guide.dismissed ? <CreatePortfolio /> : <FirstRunGuide portfolios={portfolios} />;

  const emptyHoldings = holdings.data !== undefined && holdings.data.length === 0;
  const showGuide = !guide.dismissed && (guide.started || emptyHoldings);
  return (
    <>
      {showGuide && <FirstRunGuide portfolios={portfolios} />}
      {portfolios.length > 1 && (
        <PortfolioSwitcher
          portfolios={portfolios}
          value={ref}
          onChange={(v) => { setChoice(v); savePortfolioChoice(v); }}
        />
      )}
      {summary.data ? (
        <LiveHeader s={summary.data} anyPriceStale={!!holdings.data?.some((h) => h.price_stale)} />
      ) : summary.error ? <p role="alert">{t("common.errorLoad")}</p> : <p role="status">{t("common.loading")}</p>}
      {summary.data && <PnlStrip s={summary.data} />}
      {holdings.data && holdings.data.length > 0 ? (
        <HoldingsList holdings={holdings.data} />
      ) : holdings.data ? (
        <div className="card space-y-2 text-center">
          <p>{t("holdings.empty")}</p>
          <Link href="/import" className="btn-primary">{t("holdings.importCta")}</Link>
        </div>
      ) : null}
    </>
  );
}

export function MainPage() {
  return <AppShell><Body /></AppShell>;
}
