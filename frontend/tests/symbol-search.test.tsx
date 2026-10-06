import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";
import type { ImportRow, Portfolio } from "@/lib/api";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");

const parsed: ImportRow[] = [
  { index: 0, name: "Reddit Inc O", symbol: null, tase_number: null, quantity: 10, price: 100, value: 1000, cost: null, currency: "USD", unit: "USD", flags: [] },
];
vi.mock("@/lib/ocr/engine", () => ({
  readScreenshotsOnDevice: vi.fn(async () => ({ layout: "generic", rows: parsed, meta: parsed.map(() => ({})) })),
  prepareForServer: vi.fn(async () => new Blob([new Uint8Array([137])], { type: "image/png" })),
}));
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/import",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { api } from "@/lib/api";
import { ImportPage } from "@/components/ImportPage";
import { AddHoldingForm } from "@/components/AddHoldingForm";

const wrap = (ui: ReactNode, locale: "en" | "he" = "en") =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>{ui}</NextIntlClientProvider>
    </SWRConfig>,
  );

const openReview = async () => {
  const input = (await screen.findByLabelText("Choose a screenshot")) as HTMLInputElement;
  fireEvent.change(input, { target: { files: [new File([new Uint8Array(10)], "s.png", { type: "image/png" })] } });
  fireEvent.submit(screen.getByRole("button", { name: "Read on this device" }).closest("form")!);
  await screen.findByRole("region", { name: "Review the rows" });
};
const typeInto = async (el: HTMLElement, value: string) => {
  fireEvent.focus(el);
  fireEvent.change(el, { target: { value } });
  await act(async () => { await new Promise((r) => setTimeout(r, 400)); }); // the debounce
};

describe("symbol search dropdown", () => {
  afterEach(() => vi.restoreAllMocks());

  it("does not search below two characters; shows known and new results with a combobox role", async () => {
    const search = vi.spyOn(api, "searchSecurities");
    wrap(<AddHoldingForm portfolios={[{ id: 1, name: "Main" }] as Portfolio[]} onClose={vi.fn()} />);
    const box = screen.getByLabelText("Symbol");
    await typeInto(box, "r");
    expect(search).not.toHaveBeenCalled();
    await typeInto(box, "red");
    expect(search).toHaveBeenCalledWith("red", true);
    const opt = await screen.findByRole("option", { name: /Reddit, Inc\./ });
    expect(opt).toHaveTextContent("RDDT");
    expect(opt).toHaveTextContent("new");
    expect(box).toHaveAttribute("aria-expanded", "true");
  });

  it("keyboard: arrows move, Enter picks, Escape closes", async () => {
    wrap(<AddHoldingForm portfolios={[{ id: 1, name: "Main" }] as Portfolio[]} onClose={vi.fn()} />);
    const box = screen.getByLabelText("Symbol");
    await typeInto(box, "gitlab");
    await screen.findByRole("option", { name: /GitLab/ });
    fireEvent.keyDown(box, { key: "Escape" });
    expect(box).toHaveAttribute("aria-expanded", "false");
    fireEvent.keyDown(box, { key: "ArrowDown" });
    expect(box).toHaveAttribute("aria-expanded", "true");
    fireEvent.keyDown(box, { key: "Enter" });
    expect(box).toHaveValue("GTLB");
    expect(screen.queryByRole("option", { name: /GitLab/ })).not.toBeInTheDocument();
  });

  it("AddHoldingForm: a picked result fills the symbol and is what gets sent", async () => {
    const add = vi.spyOn(api, "addHolding").mockResolvedValue({} as never);
    const onClose = vi.fn();
    wrap(<AddHoldingForm portfolios={[{ id: 1, name: "Main" }] as Portfolio[]} onClose={onClose} />);
    await typeInto(screen.getByLabelText("Symbol"), "reddit");
    fireEvent.click(await screen.findByRole("option", { name: /Reddit, Inc\./ }));
    expect(screen.getByLabelText("Symbol")).toHaveValue("RDDT");
    fireEvent.change(screen.getByLabelText(/^Quantity/), { target: { value: "3" } });
    fireEvent.click(screen.getByRole("button", { name: "Add holding" }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(add).toHaveBeenCalledWith(1, expect.objectContaining({ symbol: "RDDT", quantity: 3 }));
  });

  it("shows the Hebrew name on a Hebrew page", async () => {
    wrap(<AddHoldingForm portfolios={[{ id: 1, name: "Main" }] as Portfolio[]} onClose={vi.fn()} />, "he");
    await typeInto(screen.getByLabelText("סימול"), "nvd");
    expect(await screen.findByRole("option", { name: /אנבידיה/ })).toBeInTheDocument();
  });
});

describe("import review: search and manual rows", () => {
  afterEach(() => vi.restoreAllMocks());

  it("picking a result in the symbol box re-syncs the draft and keeps the typed numbers", async () => {
    const patch = vi.spyOn(api, "patchImport");
    wrap(<ImportPage />);
    await openReview();
    fireEvent.change(screen.getByLabelText("Quantity 1"), { target: { value: "25" } });
    await typeInto(screen.getByLabelText("Symbol 1"), "reddit");
    fireEvent.click(await screen.findByRole("option", { name: /Reddit, Inc\./ }));
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch.mock.calls[0][1].rows?.[0]).toEqual(expect.objectContaining({ symbol: "RDDT", quantity: 25 }));
    await waitFor(() => expect(screen.getByLabelText("Symbol 1")).toHaveValue("RDDT"));
    expect(screen.getByLabelText("Quantity 1")).toHaveValue("25");
  });

  it("'+ Add a stock' appends an empty row with the next free index through the server", async () => {
    const patch = vi.spyOn(api, "patchImport");
    wrap(<ImportPage />);
    await openReview();
    fireEvent.click(screen.getByRole("button", { name: "+ Add a stock" }));
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    const sent = patch.mock.calls[0][1].rows!;
    expect(sent.map((r) => r.index)).toEqual([0, 1]);
    expect(sent[1]).toEqual(expect.objectContaining({ name: "", symbol: null, quantity: null, unit: "USD", currency: "USD" }));
    expect(await screen.findByLabelText("Symbol 2")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm import" })).toBeDisabled(); // it still needs a symbol and a quantity
  });

  it("a hand-added row can be searched, filled in, and removed with the cross", async () => {
    wrap(<ImportPage />);
    await openReview();
    fireEvent.click(screen.getByRole("button", { name: "+ Add a stock" }));
    await typeInto(await screen.findByLabelText("Symbol 2"), "gitlab");
    fireEvent.click(await screen.findByRole("option", { name: /GitLab/ }));
    await waitFor(() => expect(screen.getByLabelText("Symbol 2")).toHaveValue("GTLB"));
    fireEvent.change(screen.getByLabelText("Quantity 2"), { target: { value: "4" } });
    fireEvent.click(screen.getByRole("button", { name: "Remove row 2" }));
    await waitFor(() => expect(screen.queryByLabelText("Symbol 2")).not.toBeInTheDocument());
    expect(screen.getByLabelText("Symbol 1")).toBeInTheDocument();
  });

  it("a TASE pick on a hand-added row switches it to shekels", async () => {
    const patch = vi.spyOn(api, "patchImport");
    wrap(<ImportPage />);
    await openReview();
    fireEvent.click(screen.getByRole("button", { name: "+ Add a stock" }));
    await typeInto(await screen.findByLabelText("Symbol 2"), "zed");
    fireEvent.click(await screen.findByRole("option", { name: /Zed Q/ }));
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(2));
    expect(patch.mock.calls[1][1].rows?.[1]).toEqual(expect.objectContaining({ symbol: "ZZQ.TA", unit: "ILS", currency: "ILS" }));
  });
});
