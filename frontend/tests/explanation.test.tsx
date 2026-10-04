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

  it("wraps free text in bdi with dir=auto, including Hebrew mixed with numbers", () => {
    const he1 = "הציון 42 מתוך 100 עבור NICE.TA";
    wrap({
      version: 1, summary: he1, inputs: { "מחזור": "1,250 אלף", rsi: 58 }, rules_applied: ["כלל 3: מקסימום 12%"],
      risk_rules_applied: ["מגבלה 5%"], invalidation_risks: ["סגירה מתחת ל-50"],
      sources: [{ name: "בורסה", detail: "נתוני 2026", as_of: null }],
    });
    const root = screen.getByTestId("explanation");
    const bdis = root.querySelectorAll("bdi");
    expect(bdis.length).toBeGreaterThanOrEqual(8);
    bdis.forEach((b) => expect(b).toHaveAttribute("dir", "auto"));
    expect(screen.getByText(he1).tagName).toBe("BDI");
    expect(screen.getByText("1,250 אלף").tagName).toBe("BDI");
    expect(screen.getByText("כלל 3: מקסימום 12%").tagName).toBe("BDI");
  });

  it("never prints null or NaN for score, weight or confidence, and bars are neutral", () => {
    wrap({
      version: 1, summary: "x",
      inputs: { a: null as unknown as string, b: Number.NaN as unknown as number },
      contributions: [
        { name: "technical", score: Number.NaN, weight: Number.NaN, confidence: 0.5, raw: {} },
        { name: "patterns", score: null as unknown as number, weight: null as unknown as number, confidence: null as unknown as number, raw: {} },
        { name: "analysts", score: -40, weight: 10, confidence: 1, raw: {} },
        { name: "news", score: 40, weight: 10, confidence: 1, raw: {} },
      ],
    });
    const text = screen.getByTestId("explanation").textContent ?? "";
    expect(text).not.toMatch(/null|NaN|undefined/);
    const bars = screen.getAllByTestId("contribution-bar");
    expect(bars).toHaveLength(2);
    for (const b of bars) expect(b.className).not.toMatch(/emerald|green|red/);
  });

  it("uses no verdict wording", () => {
    wrap(full);
    expect(screen.getByTestId("explanation").textContent).not.toMatch(/\b(buy|sell|hold)\b/i);
  });
});
