import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig } from "swr";
import en from "@/messages/en.json";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");

import { CreatePortfolio } from "@/components/CreatePortfolio";

describe("CreatePortfolio (first run, account has no portfolio)", () => {
  it("creates a portfolio with name and currency and reports its id", async () => {
    const onCreated = vi.fn();
    render(
      <SWRConfig value={{ provider: () => new Map() }}>
        <NextIntlClientProvider locale="en" messages={en}>
          <CreatePortfolio onCreated={onCreated} />
        </NextIntlClientProvider>
      </SWRConfig>,
    );
    const btn = screen.getByRole("button", { name: "Create portfolio" });
    expect(btn).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Portfolio name"), { target: { value: "Main" } });
    fireEvent.change(screen.getByLabelText("Base currency"), { target: { value: "USD" } });
    fireEvent.click(btn);
    await waitFor(() => expect(onCreated).toHaveBeenCalledTimes(1));
  });
});
