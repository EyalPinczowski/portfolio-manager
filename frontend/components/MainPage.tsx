"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { useGuideState } from "@/lib/guide";
import { useSetupGate } from "@/lib/setup";
import { loadPortfolioChoice, savePortfolioChoice, useHoldings, usePortfolios, useSummary, type PortfolioRef } from "@/lib/hooks";
import { ScreenshotNudge } from "./ScreenshotNudge";
import { AddHoldingForm } from "./AddHoldingForm";
import { AllocationDonut } from "./AllocationDonut";
import { AppShell } from "./AppShell";
import { CreatePortfolio } from "./CreatePortfolio";
import { FirstRunGuide } from "./FirstRunGuide";
import { HoldingsList } from "./HoldingsList";
import { LiveHeader } from "./LiveHeader";
import { PnlStrip } from "./PnlStrip";
import { PortfolioSummary } from "./PortfolioSummary";
import { PortfolioSwitcher } from "./PortfolioSwitcher";

function Body() {
  const t = useTranslations();
  const { data: portfolios, error: pErr } = usePortfolios();
  const [choice, setChoice] = useState<PortfolioRef>(() => loadPortfolioChoice() ?? "combined");
  const guide = useGuideState();
  const gate = useSetupGate();
  const [adding, setAdding] = useState(false);
  const valid = choice === "combined" || portfolios?.some((p) => p.id === choice);
  const ref: PortfolioRef = valid ? choice : "combined";
  const summary = useSummary(portfolios && portfolios.length > 0 ? ref : null);
  const holdings = useHoldings(portfolios ? ref : null, portfolios);

  if (pErr) return <p role="alert">{t("common.errorLoad")}</p>;
  if (!portfolios) return <p role="status" className="text-muted">{t("common.loading")}</p>;
  if (portfolios.length === 0) return guide.dismissed && gate === "open" ? <CreatePortfolio /> : <FirstRunGuide portfolios={portfolios} />;

  const emptyHoldings = holdings.data !== undefined && holdings.data.length === 0;
  const showGuide = gate === "blocked" || (!guide.dismissed && (guide.started || emptyHoldings));
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
      {summary.data && <PortfolioSummary s={summary.data} holdings={holdings.data ?? []} />}
      {summary.data ? (
        <LiveHeader compact s={summary.data} anyPriceStale={!!holdings.data?.some((h) => h.price_stale)} />
      ) : summary.error ? <p role="alert">{t("common.errorLoad")}</p> : <p role="status">{t("common.loading")}</p>}
      {summary.data && <PnlStrip s={summary.data} />}
      {summary.data && <ScreenshotNudge at={summary.data.last_screenshot_update_at ?? null} stale={summary.data.screenshot_update_stale} />}
      {holdings.data && holdings.data.length > 0 ? (
        <>
          <AllocationDonut holdings={holdings.data} />
          <HoldingsList holdings={holdings.data} />
        </>
      ) : holdings.data ? (
        <div className="card space-y-2 text-center">
          <p>{t("holdings.empty")}</p>
          <Link href="/import" className="btn-primary">{t("holdings.importCta")}</Link>
        </div>
      ) : null}
      {holdings.data && (
        <div className="text-center">
          <button type="button" className="btn-secondary" onClick={() => setAdding(true)}>{t("addHolding.open")}</button>
        </div>
      )}
      {adding && <AddHoldingForm portfolios={portfolios} portfolioId={typeof ref === "number" ? ref : null} onClose={() => setAdding(false)} />}
    </>
  );
}

export function MainPage() {
  return <AppShell><Body /></AppShell>;
}
