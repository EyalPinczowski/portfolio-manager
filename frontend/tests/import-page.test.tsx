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
const pngOut = new Blob([new Uint8Array([137, 80, 78, 71])], { type: "image/png" });
vi.mock("@/lib/ocr/engine", () => ({
  readScreenshotOnDevice: vi.fn(async () => parsed),
  prepareForServer: vi.fn(async () => pngOut),
}));
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/import",
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
}));

import { api } from "@/lib/api";
import { prepareForServer, readScreenshotOnDevice } from "@/lib/ocr/engine";
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

    expect(readScreenshotOnDevice).toHaveBeenCalledTimes(1);
    expect(importRows).toHaveBeenCalledWith(1, parsed);
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
    vi.mocked(readScreenshotOnDevice).mockResolvedValueOnce([{ ...parsed[0], tase_number: null, name: "Monday" }]);
    renderPage();
    await pick();
    clickRead();
    expect(await screen.findByText(/Weak match: check the symbol/)).toBeInTheDocument();
    expect(screen.getByText("Did you mean:")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Use MNDY (monday.com, 58% match)" }));
    expect(screen.getByLabelText("Symbol 1")).toHaveValue("MNDY");
  });

  it("says so when nothing could be read", async () => {
    vi.mocked(readScreenshotOnDevice).mockResolvedValueOnce([]);
    renderPage();
    await pick();
    clickRead();
    expect(await screen.findByRole("alert")).toHaveTextContent(/No holdings were found/);
  });
});
