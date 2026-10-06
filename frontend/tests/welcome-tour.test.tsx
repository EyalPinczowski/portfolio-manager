import { beforeEach, describe, expect, it } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";
import { WelcomeTour } from "@/components/WelcomeTour";
import { TOUR_KEY, tourActions } from "@/lib/tour";

const wrap = (locale: "en" | "he", ui: ReactNode) =>
  render(<NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>{ui}</NextIntlClientProvider>);

beforeEach(() => { tourActions.reset(); window.localStorage.clear(); });

describe("WelcomeTour", () => {
  it("shows on first entrance", () => {
    wrap("en", <WelcomeTour />);
    expect(screen.getByRole("dialog")).toBeTruthy();
    expect(screen.getByText("Welcome to Holdwise")).toBeTruthy();
    expect(screen.getByText("1 of 6")).toBeTruthy();
  });

  it("does not show when already seen", () => {
    tourActions.markSeen();
    wrap("en", <WelcomeTour />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("skip marks it seen and closes", () => {
    wrap("en", <WelcomeTour />);
    fireEvent.click(screen.getByRole("button", { name: "Skip" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(window.localStorage.getItem(TOUR_KEY)).toBe("1");
  });

  it("Escape skips", () => {
    wrap("en", <WelcomeTour />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(window.localStorage.getItem(TOUR_KEY)).toBe("1");
  });

  it("next and back move between slides; the last slide finishes", () => {
    wrap("en", <WelcomeTour />);
    expect(screen.queryByRole("button", { name: "Back" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("2 of 6")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByText("1 of 6")).toBeTruthy();
    for (let k = 0; k < 5; k++) fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("6 of 6")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Next" })).toBeNull();
    expect(screen.getByRole("button", { name: "Skip" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Let's start" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(window.localStorage.getItem(TOUR_KEY)).toBe("1");
  });

  it("can be reopened after being seen (Settings row)", () => {
    tourActions.markSeen();
    wrap("en", <WelcomeTour />);
    expect(screen.queryByRole("dialog")).toBeNull();
    act(() => tourActions.reopen());
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("renders in Hebrew", () => {
    wrap("he", <WelcomeTour />);
    expect(screen.getByText("ברוכים הבאים ל-Holdwise")).toBeTruthy();
    expect(screen.getByRole("button", { name: "דלג" })).toBeTruthy();
    expect(screen.getByRole("button", { name: /הבא/ })).toBeTruthy();
  });
});
