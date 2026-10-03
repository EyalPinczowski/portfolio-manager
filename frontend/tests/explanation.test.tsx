import "@testing-library/jest-dom/vitest";
import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import en from "@/messages/en.json";
import he from "@/messages/he.json";
import type { Explanation } from "@/lib/api";
import { ExplanationView } from "@/components/ExplanationView";

const wrap = (e: Explanation, locale: "en" | "he" = "en", props: object = {}) =>
  render(<NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}><ExplanationView e={e} {...props} /></NextIntlClientProvider>);

const full: Explanation = {
  version: 1,
  summary: "Combined score 42 from two signals.",
  inputs: { "RSI(14)": 58 },
  rules_applied: ["Weighted combination"],
  as_of: "2026-10-03T08:55:00Z",
  contributions: [
    { name: "technical", score: 60, weight: 67, confidence: 0.8, raw: { rsi: 58 } },
    { name: "patterns", score: -25, weight: 33, confidence: 0.5, raw: {} },
    { name: "analysts", score: 0, weight: 0, confidence: 0, raw: {} },
  ],
  annotations: [
    { kind: "support", label: "Support", price: 28.5, as_of: "2026-09-30T00:00:00Z" },
    { kind: "resistance", label: "Resistance", price: 33, as_of: null },
    { kind: "moving_average", label: "SMA 50", price: 30.1, as_of: "2026-10-03T00:00:00Z" },
    { kind: "pattern", label: "Golden cross", price: null, as_of: "2026-10-01T00:00:00Z" },
  ],
  risk_rules_applied: ["Max position 12%"],
  invalidation_risks: ["Close below SMA 50"],
  sources: [{ name: "Yahoo Finance", as_of: "2026-10-03T08:55:00Z", detail: "Daily bars" }],
};

describe("ExplanationView", () => {
  it("renders every section of a full typed explanation", () => {
    wrap(full, "en", { currency: "USD" });
    expect(screen.getByText("Combined score 42 from two signals.")).toBeInTheDocument();
    const table = screen.getByRole("table");
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(4); // header + 3
    expect(within(rows[1]).getByText("Technical")).toBeInTheDocument();
    expect(within(rows[1]).getByText("+60")).toBeInTheDocument();
    expect(within(rows[1]).getByText("67%")).toBeInTheDocument();
    expect(within(rows[1]).getByText("80%")).toBeInTheDocument();
    expect(within(rows[2]).getByText("-25")).toBeInTheDocument();
    expect(screen.getAllByTestId("contribution-bar")).toHaveLength(2); // none for the confidence-0 signal
    expect(within(rows[3]).getByText("—")).toBeInTheDocument();
    for (const k of ["Support", "Resistance", "Moving average", "Pattern"]) expect(screen.getAllByText(k).length).toBeGreaterThan(0);
    expect(screen.getByText(/\$28\.50/)).toBeInTheDocument();
    expect(screen.getByText("Max position 12%")).toBeInTheDocument();
    expect(screen.getByText("Close below SMA 50")).toBeInTheDocument();
    expect(screen.getByText(/Yahoo Finance/)).toBeInTheDocument();
    expect(screen.getAllByText(/Data as of 03\/10\/2026/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/^(strong )?(buy|sell|hold)$/i)).toBeNull();
  });

  it("handles an old cached explanation with every optional field missing", () => {
    wrap({ version: 1, summary: "Old card." });
    expect(screen.getByText("Old card.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    for (const h of ["Chart levels and patterns", "Risk rules applied", "What could make this wrong", "Sources", "Inputs", "Rules applied"]) {
      expect(screen.queryByText(h)).toBeNull();
    }
  });

  it("handles an empty summary and explicitly empty arrays", () => {
    wrap({ version: 1, summary: "", inputs: {}, rules_applied: [], contributions: [], annotations: [], risk_rules_applied: [], invalidation_risks: [], sources: [] });
    expect(screen.getByTestId("explanation")).toBeInTheDocument();
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("is translated to Hebrew", () => {
    wrap(full, "he");
    expect(screen.getByText(he.holding.whyContributions)).toBeInTheDocument();
    expect(screen.getByText(he.holding.whyInvalidation)).toBeInTheDocument();
    expect(screen.getAllByText(he.holding.annotationKinds.support).length).toBeGreaterThan(0);
  });
});
