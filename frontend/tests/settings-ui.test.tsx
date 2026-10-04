import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";

vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
}));

import { CheckList, ConfirmSheet, FieldRow, MultiCheckList, SettingsGroup, SettingsRow, Sheet, ToggleRow } from "@/components/SettingsUI";

afterEach(cleanup);

describe("SettingsGroup and SettingsRow", () => {
  it("renders a titled group with a footer and rows; a link row shows its value and a mirrored chevron", () => {
    render(
      <SettingsGroup title="General" footer="Saved at once.">
        <SettingsRow label="Language" value="English" href="/settings?section=language" icon={<i />} tone="blue" />
        <SettingsRow label="Reset" destructive onClick={() => {}} />
      </SettingsGroup>,
    );
    expect(screen.getByRole("heading", { level: 2, name: "General" })).toBeInTheDocument();
    expect(screen.getByText("Saved at once.")).toBeInTheDocument();
    const link = screen.getByRole("link", { name: /Language/ });
    expect(link).toHaveAttribute("href", "/settings?section=language");
    expect(link).toHaveTextContent("English");
    expect(link.className).toContain("min-h-12"); // 48px touch target
    expect(link.querySelector("svg")?.getAttribute("class")).toContain("rtl:-scale-x-100");
    const reset = screen.getByRole("button", { name: "Reset" });
    expect(reset.querySelector("span")?.className).toContain("text-loss");
    expect(reset.querySelector("svg")).toBeNull(); // an action row has no chevron
  });

  it("an action row calls onClick; a disabled one does not", () => {
    const fn = vi.fn();
    render(<SettingsGroup label="x"><SettingsRow label="Go" onClick={fn} /><SettingsRow label="Off" onClick={fn} disabled /></SettingsGroup>);
    fireEvent.click(screen.getByRole("button", { name: "Go" }));
    fireEvent.click(screen.getByRole("button", { name: "Off" }));
    expect(fn).toHaveBeenCalledTimes(1);
  });
});

describe("ToggleRow", () => {
  it("is a switch that reports the new value and mirrors its knob in RTL", () => {
    const fn = vi.fn();
    render(<SettingsGroup label="x"><ToggleRow label="Price alerts" checked={false} onChange={fn} /></SettingsGroup>);
    const sw = screen.getByRole("switch", { name: "Price alerts" });
    expect(sw).toHaveAttribute("aria-checked", "false");
    fireEvent.click(sw);
    expect(fn).toHaveBeenCalledWith(true);
    cleanup();
    render(<SettingsGroup label="x"><ToggleRow label="Price alerts" checked onChange={fn} /></SettingsGroup>);
    const on = screen.getByRole("switch");
    expect(on).toHaveAttribute("aria-checked", "true");
    expect(on.querySelector("span span")?.className).toMatch(/translate-x-5.*rtl:-translate-x-5/);
  });
});

describe("CheckList", () => {
  it("checks only the current choice and nothing when the value is null", () => {
    const pick = vi.fn();
    const opts = [{ value: "a", label: "Alpha" }, { value: "b", label: "Beta", hint: "second" }];
    render(<CheckList label="Pick" value="b" options={opts} onPick={pick} footer="Note" />);
    expect(screen.getByRole("radiogroup", { name: "Pick" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Beta/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: "Alpha" })).toHaveAttribute("aria-checked", "false");
    fireEvent.click(screen.getByRole("radio", { name: "Alpha" }));
    expect(pick).toHaveBeenCalledWith("a");
    cleanup();
    render(<CheckList label="Pick" value={null} options={opts} onPick={pick} />);
    expect(screen.getAllByRole("radio").every((r) => r.getAttribute("aria-checked") === "false")).toBe(true);
  });

  it("MultiCheckList toggles several rows", () => {
    const tog = vi.fn();
    render(<MultiCheckList label="Markets" values={["US"]} options={[{ value: "US", label: "US" }, { value: "TASE", label: "TASE" }]} onToggle={tog} />);
    expect(screen.getByRole("checkbox", { name: "US" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("checkbox", { name: "TASE" }));
    expect(tog).toHaveBeenCalledWith("TASE");
  });
});

describe("FieldRow", () => {
  it("labels its control", () => {
    render(<SettingsGroup label="x"><FieldRow label="Time" htmlFor="t" help="Zone"><input id="t" type="time" /></FieldRow></SettingsGroup>);
    expect(screen.getByLabelText("Time")).toHaveAttribute("type", "time");
    expect(screen.getByText("Zone")).toBeInTheDocument();
  });
});

describe("Sheet and ConfirmSheet", () => {
  it("closes on Escape and on a tap outside, but not on a tap inside", () => {
    const close = vi.fn();
    render(<Sheet title="Sure?" onClose={close}><p>body</p></Sheet>);
    const dlg = screen.getByRole("dialog", { name: "Sure?" });
    expect(dlg).toHaveAttribute("aria-modal", "true");
    fireEvent.click(dlg);
    expect(close).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("sheet-backdrop"));
    expect(close).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(close).toHaveBeenCalledTimes(2);
  });

  it("a confirm sheet has a red confirm button and a cancel; confirm can be disabled", () => {
    const ok = vi.fn(); const no = vi.fn();
    render(<ConfirmSheet title="Delete?" body="Gone for good." confirmLabel="Delete" cancelLabel="Cancel" onConfirm={ok} onCancel={no} confirmDisabled />);
    const d = within(screen.getByRole("dialog"));
    expect(d.getByText("Gone for good.")).toBeInTheDocument();
    expect(d.getByRole("button", { name: "Delete" })).toBeDisabled();
    expect(d.getByRole("button", { name: "Delete" }).className).toContain("btn-danger");
    fireEvent.click(d.getByRole("button", { name: "Cancel" }));
    expect(no).toHaveBeenCalled();
    expect(ok).not.toHaveBeenCalled();
  });

  it("the slide animations are only declared under no-preference reduced motion", async () => {
    const { readFileSync } = await import("node:fs");
    const css = readFileSync("app/globals.css", "utf8");
    const block = css.slice(css.indexOf("prefers-reduced-motion: no-preference"));
    expect(block).toContain(".settings-slide { animation");
    expect(css.slice(0, css.indexOf("prefers-reduced-motion: no-preference"))).not.toMatch(/\.settings-slide\s*\{\s*animation/);
  });
});
