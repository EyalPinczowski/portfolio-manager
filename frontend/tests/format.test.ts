import { describe, expect, it } from "vitest";
import { formatDate, formatMoney, formatPct, pnlSign } from "@/lib/format";

describe("formatting", () => {
  it("formats ILS and USD for he-IL and en-US", () => {
    expect(formatMoney(1234.5, "ILS", "en")).toBe("₪1,234.50");
    expect(formatMoney(1234.5, "USD", "en")).toBe("$1,234.50");
    const he = formatMoney(1234.5, "ILS", "he");
    expect(he).toContain("₪");
    expect(he).toContain("1,234.50");
    expect(formatMoney(1234.5, "USD", "he")).toContain("$");
  });
  it("always prints an explicit sign for signed values", () => {
    expect(formatMoney(50, "USD", "en", { signed: true })).toBe("+$50.00");
    expect(formatMoney(-50, "USD", "en", { signed: true })).toBe("-$50.00");
    expect(formatPct(1.5, "en", { signed: true })).toBe("+1.50%");
    expect(formatPct(-1.5, "en", { signed: true })).toBe("-1.50%");
    expect(pnlSign(3)).toBe("+");
    expect(pnlSign(-3)).toBe("-");
    expect(pnlSign(0)).toBe("");
  });
  it("formats dates as dd/mm/yyyy", () => {
    expect(formatDate("2026-07-06")).toBe("06/07/2026");
    expect(formatDate("2026-07-06T09:00:00Z")).toBe("06/07/2026");
  });
});
