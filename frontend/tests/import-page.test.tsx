import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import type { ImportRow } from "@/lib/api";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");

const parsed: ImportRow[] = [
  { index: 0, name: "טבע", symbol: null, tase_number: "629014", quantity: 1000, price: 6500, value: 65000, cost: null, currency: "ILS", unit: "agorot", flags: [] },
];
const wrapRows = (rows: ImportRow[]) => ({ layout: "generic" as const, rows, meta: rows.map(() => ({})) });
const pngOut = new Blob([new Uint8Array([137, 80, 78, 71])], { type: "image/png" });
vi.mock("@/lib/ocr/engine", () => ({
  readScreenshotsOnDevice: vi.fn(async () => ({ layout: "generic", rows: parsed, meta: parsed.map(() => ({})) })),
  prepareForServer: vi.fn(async () => pngOut),
}));
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/import",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { api, ApiError } from "@/lib/api";
import { prepareForServer, readScreenshotsOnDevice } from "@/lib/ocr/engine";
import { ImportPage } from "@/components/ImportPage";

const renderPage = () =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale="en" messages={en}><ImportPage /></NextIntlClientProvider>
    </SWRConfig>,
  );

const pick = async () => {
  const input = (await screen.findByLabelText("Choose a screenshot")) as HTMLInputElement;
  const file = new File([new Uint8Array(10)], "shot.png", { type: "image/png" });
  fireEvent.change(input, { target: { files: [file] } });
  return input;
};

/** jsdom enforces `required` against its own (empty) file list, so submit the form directly. */
const clickRead = () => fireEvent.submit(screen.getByRole("button", { name: "Read on this device" }).closest("form")!);

describe("import page", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows the retention promise and reads on the device by default; only parsed rows reach the API", async () => {
    const createUrl = vi.spyOn(URL, "createObjectURL");
    const importRows = vi.spyOn(api, "importRows");
    const upload = vi.spyOn(api, "createImport");
    renderPage();
    expect(await screen.findByText("We keep only the stock list. The screenshot is deleted right away.")).toBeInTheDocument();
    await pick();
    clickRead();
    await screen.findByRole("region", { name: "Review the rows" });

    expect(readScreenshotsOnDevice).toHaveBeenCalledTimes(1);
    expect(importRows).toHaveBeenCalledWith(1, parsed, "partial");
    expect(upload).not.toHaveBeenCalled(); // the image never goes to the server
    expect(createUrl).not.toHaveBeenCalled(); // no blob URL for the screenshot
    expect(screen.getByText(/This draft is deleted automatically on/)).toBeInTheDocument();
    expect(screen.getByText("TASE no. 629014")).toBeInTheDocument();
  });

  it("forgets the chosen file once reading starts (cancel returns to an empty picker)", async () => {
    renderPage();
    await pick();
    expect(screen.getByRole("button", { name: "Read on this device" })).toBeEnabled();
    clickRead();
    await screen.findByRole("region", { name: "Review the rows" });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByRole("button", { name: "Read on this device" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Use server reading" })).toBeDisabled();
  });

  it("declining the consent notice uploads nothing", async () => {
    const upload = vi.spyOn(api, "createImport");
    renderPage();
    await pick();
    fireEvent.click(screen.getByRole("button", { name: "Use server reading" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "No thanks" }));
    expect(upload).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("server reading asks for consent, then uploads a raw PNG body", async () => {
    const upload = vi.spyOn(api, "createImport");
    renderPage();
    await pick();
    fireEvent.click(screen.getByRole("button", { name: "Use server reading" }));
    const dialog = await screen.findByRole("dialog", { name: "Use server reading?" });
    expect(upload).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "I agree, upload" }));
    await screen.findByRole("region", { name: "Review the rows" });
    expect(prepareForServer).toHaveBeenCalled();
    expect(upload).toHaveBeenCalledWith(1, pngOut);
  });

  it("number cells accept 1.234,5 and 1,234; unreadable numbers block confirming", async () => {
    const patch = vi.spyOn(api, "patchImport");
    renderPage();
    await pick();
    clickRead();
    const qty = await screen.findByLabelText("Quantity 1");
    fireEvent.change(qty, { target: { value: "1.234,5" } });
    const price = screen.getByLabelText("Price 1");
    fireEvent.change(price, { target: { value: "abc" } });
    expect(price).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("button", { name: "Confirm import" })).toBeDisabled();
    fireEvent.change(price, { target: { value: "6,500" } });
    const confirm = screen.getByRole("button", { name: "Confirm import" });
    expect(confirm).toBeEnabled();
    fireEvent.click(confirm);
    await waitFor(() => expect(patch).toHaveBeenCalled());
    const rows = patch.mock.calls[0][1].rows!;
    expect(rows[0].quantity).toBe(1234.5);
    expect(rows[0].price).toBe(6500);
  });

  it("shows weak-match candidates and lets the user pick one", async () => {
    vi.mocked(readScreenshotsOnDevice).mockResolvedValueOnce(wrapRows([{ ...parsed[0], tase_number: null, name: "Monday" }]));
    renderPage();
    await pick();
    clickRead();
    await screen.findByRole("region", { name: "Review the rows" });
    expect(await screen.findByText(/Weak match: check the symbol/)).toBeInTheDocument();
    expect(screen.getByText("Did you mean:")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Use MNDY (monday.com, 58% match)" }));
    expect(screen.getByLabelText("Symbol 1")).toHaveValue("MNDY");
  });

  it("blocks confirming while a row has no symbol; picking a candidate re-syncs the draft and unblocks it", async () => {
    vi.mocked(readScreenshotsOnDevice).mockResolvedValueOnce(wrapRows([{ ...parsed[0], tase_number: null, name: "Monday" }]));
    const patch = vi.spyOn(api, "patchImport");
    renderPage();
    await pick();
    clickRead();
    await screen.findByRole("region", { name: "Review the rows" });
    expect(screen.getByRole("button", { name: "Confirm import" })).toBeDisabled();
    expect(screen.getByText(/Pick a symbol for every highlighted row/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Use MNDY (monday.com, 58% match)" }));
    await waitFor(() => expect(patch).toHaveBeenCalledTimes(1));
    expect(patch.mock.calls[0][1]).toEqual({ rows: [expect.objectContaining({ symbol: "MNDY" })] }); // no stale proposed_changes
    await waitFor(() => expect(screen.getByRole("button", { name: "Confirm import" })).toBeEnabled());
  });

  it("lets the user remove a row", async () => {
    renderPage();
    await pick();
    clickRead();
    await screen.findByRole("region", { name: "Review the rows" });
    fireEvent.click(screen.getByRole("button", { name: "Remove row 1" }));
    await waitFor(() => expect(screen.queryByLabelText("Name 1")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Confirm import" })).toBeDisabled();
  });

  it("server reading: 503 (no OCR engine on the server) points back at on-device reading", async () => {
    vi.spyOn(api, "createImport").mockRejectedValueOnce(new ApiError(503, "Tesseract is not installed"));
    renderPage();
    await pick();
    fireEvent.click(screen.getByRole("button", { name: "Use server reading" }));
    const agree = screen.queryByRole("button", { name: "I agree, upload" }); // absent if an earlier test consented
    if (agree) fireEvent.click(agree);
    expect(await screen.findByRole("alert")).toHaveTextContent(/server cannot read screenshots right now/i);
  });

  it("confirm 422 (unmatched row on the server) shows a specific message, not the generic one", async () => {
    vi.spyOn(api, "confirmImport").mockRejectedValueOnce(new ApiError(422, "Row 1 is not matched"));
    renderPage();
    await pick();
    clickRead();
    await screen.findByRole("region", { name: "Review the rows" });
    fireEvent.click(screen.getByRole("button", { name: "Confirm import" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(/not matched to a security/);
  });

  it("partial scope (the default) leaves holdings that are not in the screenshots alone: no 'not in these screenshots' group", async () => {
    renderPage();
    await pick();
    clickRead();
    await screen.findByRole("region", { name: "Review the rows" });
    expect(screen.queryByRole("group", { name: /Not in this screenshot/ })).toBeNull();
    expect(screen.getByRole("checkbox", { name: "These screenshots show my whole portfolio" })).not.toBeChecked();
  });

  it("full scope: holdings that are not in the screenshots default to keep; sell and withdrawal are the user's choice", async () => {
    const importRows = vi.spyOn(api, "importRows");
    const patch = vi.spyOn(api, "patchImport");
    renderPage();
    await pick();
    fireEvent.click(await screen.findByRole("checkbox", { name: /These screenshots show my whole portfolio/ }));
    clickRead();
    const group = await screen.findByRole("group", { name: /Not in th(is|ese) screenshots?/ });
    expect(importRows).toHaveBeenCalledWith(1, parsed, "full");
    const picker = within(group).getAllByRole("combobox")[0];
    expect(within(picker).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual(["keep", "sell", "withdrawal"]);
    expect((picker as HTMLSelectElement).value).toBe("keep");
    expect(patch).not.toHaveBeenCalled();
    fireEvent.change(picker, { target: { value: "withdrawal" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm import" }));
    await waitFor(() => expect(patch).toHaveBeenCalled());
    const sent = patch.mock.calls[0][1].proposed_changes!;
    expect(sent.some((c) => c.row_index < 0 && c.type === "withdrawal")).toBe(true);
  });

  it("says so when nothing could be read", async () => {
    vi.mocked(readScreenshotsOnDevice).mockResolvedValueOnce(wrapRows([]));
    renderPage();
    await pick();
    clickRead();
    expect(await screen.findByRole("alert")).toHaveTextContent(/No holdings were found/);
  });

  it("sends ILS (not USD) for a row parsed as USD with unit ILS", async () => {
    vi.mocked(readScreenshotsOnDevice).mockResolvedValueOnce(wrapRows([{ ...parsed[0], currency: "USD", unit: "ILS", price: 65, value: 65000 }]));
    renderPage();
    await pick();
    clickRead();
    await screen.findByRole("region", { name: "Review the rows" }); // the mock server answers 422 for a currency/unit mismatch, so this proves the row was fixed
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows a per-row message (not a generic error) when the server answers 422", async () => {
    vi.spyOn(api, "importRows").mockRejectedValueOnce(new ApiError(422, "Validation error", undefined, { detail: [
      { type: "value_error", loc: ["body", "rows", 0, "quantity"], msg: "Input should be less than or equal to 1000000000000" },
    ] }));
    renderPage();
    await pick();
    clickRead();
    expect(await screen.findByText("The server rejected these rows. Fix them and try again.")).toBeInTheDocument();
    expect(screen.getByText(/Row 1 \(טבע\): quantity Input should be less than/)).toBeInTheDocument();
    expect(screen.queryByText("Could not read the screenshot. Try again.")).toBeNull();
  });

  it("Meitav rows: a required quantity blocks confirm until entered; inferred cost and duplicates get notes", async () => {
    const row = (i: number, symbol: string, over: Partial<ImportRow> = {}): ImportRow => ({
      index: i, name: symbol, symbol, tase_number: null, quantity: 10, price: 10, value: 100, cost: 9, currency: "USD", unit: "USD", matched_name: null, flags: [], ...over,
    });
    vi.mocked(readScreenshotsOnDevice).mockResolvedValueOnce({
      layout: "meitav_trade",
      rows: [row(0, "ACME"), row(1, "ZZZW", { quantity: null, price: 0.0107, value: 0.03 }), row(2, "QQQX", { cost: null })],
      meta: [{ cost_inferred: true, pnl_pct: -8.37 }, { quantity_uncertain: true, cost_inferred: true, pnl_pct: -91.2 }, { duplicate_removed: true }],
    });
    renderPage();
    await pick();
    clickRead();
    await screen.findByRole("region", { name: "Review the rows" });
    expect(screen.getAllByTestId("note-cost-inferred")[0]).toHaveTextContent("Cost was estimated from the broker's P&L % (-8.37%)");
    expect(screen.getByTestId("note-duplicate")).toHaveTextContent("counted once");
    const qty = screen.getByLabelText("Quantity 2");
    expect(qty).toHaveAttribute("aria-required", "true");
    expect(screen.getByText(/The quantity cannot be read from the screenshot/)).toBeInTheDocument();
    const confirm = screen.getByRole("button", { name: "Confirm import" });
    expect(confirm).toBeDisabled();
    fireEvent.change(qty, { target: { value: "3" } });
    expect(screen.getByLabelText("Quantity 2")).not.toHaveAttribute("aria-required");
    expect(screen.getByRole("button", { name: "Confirm import" })).toBeEnabled();
    expect(screen.getByLabelText("Quantity 1")).not.toHaveAttribute("aria-required");
  });

  it("groups the review: new holdings and changed quantities, with the trade/deposit/withdrawal picker, and totals", async () => {
    renderPage();
    await pick();
    clickRead();
    // The mock portfolio holds TEVA.TA (600), the screenshot has TEVA.TA 1000 (changed) and an unknown symbol (new).
    expect(await screen.findByRole("group", { name: "Quantity changed" })).toBeInTheDocument();
    expect(screen.getByText(/A deposit or withdrawal is money moved in or out, not profit or loss/)).toBeInTheDocument();
    const picker = screen.getByLabelText("Change type 1") as HTMLSelectElement;
    expect(within(picker).getAllByRole("option").map((o) => o.getAttribute("value"))).toEqual(["buy", "sell", "deposit", "withdrawal"]);
    const totals = await screen.findByTestId("update-totals");
    expect(totals).toHaveTextContent("Value now");
    expect(totals).toHaveTextContent("Value after this update");
  });

  it("the whole-portfolio checkbox in the review switches the scope on the server and shows the 'not in these screenshots' list", async () => {
    const patch = vi.spyOn(api, "patchImport");
    renderPage();
    await pick();
    clickRead();
    await screen.findByRole("region", { name: "Review the rows" });
    expect(screen.queryByRole("group", { name: /Not in th(is|ese) screenshots?/ })).toBeNull();
    fireEvent.click(screen.getByRole("checkbox", { name: "These screenshots show my whole portfolio" }));
    await screen.findByRole("group", { name: /Not in th(is|ese) screenshots?/ });
    expect(patch.mock.calls[0][1].scope).toBe("full");
    fireEvent.click(screen.getByRole("checkbox", { name: "These screenshots show my whole portfolio" }));
    await waitFor(() => expect(screen.queryByRole("group", { name: /Not in th(is|ese) screenshots?/ })).toBeNull());
    expect(patch.mock.calls[1][1].scope).toBe("partial");
  });

  it("server reading with the whole-portfolio box ticked switches the new draft to full scope", async () => {
    const patch = vi.spyOn(api, "patchImport");
    renderPage();
    await pick();
    fireEvent.click(screen.getByRole("checkbox", { name: /These screenshots show my whole portfolio/ }));
    fireEvent.click(screen.getByRole("button", { name: "Use server reading" }));
    const agree = screen.queryByRole("button", { name: "I agree, upload" }); // the mock remembers consent from earlier tests
    if (agree) fireEvent.click(agree);
    await screen.findByRole("region", { name: "Review the rows" });
    expect(patch).toHaveBeenCalledWith(expect.any(Number), { scope: "full" });
  });

  describe("server flags", () => {
    const base = (i: number, symbol: string, over: Partial<ImportRow> = {}): ImportRow => ({
      index: i, name: symbol, symbol, tase_number: null, quantity: 10, price: 10, value: 100, cost: 9, currency: "USD", unit: "USD", matched_name: null, flags: [], ...over,
    });
    const load = async (rows: ImportRow[]) => {
      vi.mocked(readScreenshotsOnDevice).mockResolvedValueOnce(wrapRows(rows));
      renderPage();
      await pick();
      clickRead();
      await screen.findByRole("region", { name: "Review the rows" });
    };
    const confirmBtn = () => screen.getByRole("button", { name: "Confirm import" });

    it("quantity_uncertain (server flag only, no client meta) blocks confirm until a quantity is typed", async () => {
      await load([base(0, "ACME"), base(1, "ZZZW", { quantity: null, flags: ["quantity_uncertain", "missing_fields"] })]);
      expect(screen.getByTestId("note-quantity-uncertain")).toBeInTheDocument();
      expect(screen.getByLabelText("Quantity 2")).toHaveAttribute("aria-required", "true");
      expect(confirmBtn()).toBeDisabled();
      fireEvent.change(screen.getByLabelText("Quantity 2"), { target: { value: "3" } });
      expect(screen.queryByTestId("note-quantity-uncertain")).toBeNull();
      expect(confirmBtn()).toBeEnabled();
    });

    it("conflict blocks confirm until the user ticks that the numbers were checked; shows what the other copy said", async () => {
      await load([base(0, "ACME", { flags: ["conflict"], conflict: { price: 9.5, value: 95, quantity: null } })]);
      expect(screen.getByTestId("note-conflict")).toHaveTextContent("value 95, price 9.5");
      expect(confirmBtn()).toBeDisabled();
      expect(screen.getByText(/tick "I checked these numbers"/)).toBeInTheDocument();
      fireEvent.click(screen.getByRole("checkbox", { name: /I checked these numbers 1/ }));
      expect(confirmBtn()).toBeEnabled();
      fireEvent.click(screen.getByRole("checkbox", { name: /I checked these numbers 1/ }));
      expect(confirmBtn()).toBeDisabled();
    });

    it("quantity_fractional, cost_inferred and duplicate_removed explain themselves and do not block", async () => {
      await load([
        base(0, "ACME", { flags: ["quantity_fractional"] }),
        base(1, "BETA", { flags: ["cost_inferred"] }),
        base(2, "GAMA", { flags: ["duplicate_removed"] }),
      ]);
      expect(screen.getByTestId("note-quantity-fractional")).toHaveTextContent("not a whole number");
      expect(screen.getByTestId("note-cost-inferred")).toHaveTextContent("approximate");
      expect(screen.getByTestId("note-duplicate")).toHaveTextContent("counted once, not added up");
      expect(confirmBtn()).toBeEnabled();
    });
  });
});
