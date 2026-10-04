import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import en from "@/messages/en.json";
import { Heatmap } from "@/components/Heatmap";

afterEach(cleanup);

const items = [
  { symbol: "LUMI.TA", sector: "Financials", weight_pct: 19.5, day_change_pct: -0.6 },
  { symbol: "AAPL", sector: "Technology", weight_pct: 7.7, day_change_pct: -0.3 },
];

function tiles() {
  render(
    <NextIntlClientProvider locale="en" messages={en}>
      <Heatmap items={items} />
    </NextIntlClientProvider>,
  );
  return screen.getAllByRole("listitem");
}

describe("Heatmap tiles", () => {
  it("keeps three single-line rows in every tile so nothing overlaps", () => {
    for (const li of tiles()) {
      const rows = Array.from(li.children);
      expect(rows).toHaveLength(3);
      for (const row of rows) expect(row).toHaveClass("truncate");
    }
  });

  it("a wide tile shows the sector; a one-column tile moves it to the tooltip and splits the numbers", () => {
    const [wide, narrow] = tiles();
    expect(within(wide).getByText("Financials")).toBeInTheDocument();
    expect(wide).toHaveTextContent(/19\.5%/);
    expect(within(narrow).queryByText("Technology")).toBeNull();
    expect(narrow).toHaveAttribute("title", "AAPL · Technology");
    expect(within(narrow).getByText("7.7%")).toBeInTheDocument();
  });
});
