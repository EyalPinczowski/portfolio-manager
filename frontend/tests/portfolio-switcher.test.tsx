import { render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";
import { PortfolioSwitcher } from "@/components/PortfolioSwitcher";
import en from "@/messages/en.json";
import type { Portfolio } from "@/lib/api";

const p = (id: number): Portfolio => ({ id, name: `P${id}` }) as Portfolio;
const ui = (list: Portfolio[]) => (
  <NextIntlClientProvider locale="en" messages={en}>
    <PortfolioSwitcher portfolios={list} value={1} onChange={() => {}} />
  </NextIntlClientProvider>
);

describe("PortfolioSwitcher", () => {
  it("hides with a single portfolio", () => {
    render(ui([p(1)]));
    expect(screen.queryByRole("combobox")).toBeNull();
  });
  it("shows with two or more", () => {
    render(ui([p(1), p(2)]));
    expect(screen.getByRole("combobox")).toBeTruthy();
  });
});
