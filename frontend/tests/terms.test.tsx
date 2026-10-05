import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { SWRConfig, mutate } from "swr";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");
const replace = vi.fn();
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/review",
  useRouter: () => ({ replace, push: vi.fn() }),
}));

import { api, ApiError } from "@/lib/api";
import { mockRequest, resetMockTerms } from "@/lib/mock";
import { safeNext, termsHref } from "@/lib/routes";
import { AuthGate } from "@/components/AuthGate";
import { TermsPage } from "@/components/TermsPage";

const wrap = (ui: ReactNode, locale: "en" | "he" = "en") =>
  render(
    <SWRConfig value={{ provider: () => new Map(), dedupingInterval: 0 }}>
      <NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}>{ui}</NextIntlClientProvider>
    </SWRConfig>,
  );

describe("terms: gate and page", () => {
  beforeEach(() => { resetMockTerms(); window.localStorage.setItem("pm.mock", "terms"); replace.mockClear(); });
  afterEach(() => { window.localStorage.clear(); vi.restoreAllMocks(); });

  it("mock API answers 403 terms_not_accepted until accepted, then allows", async () => {
    expect(() => mockRequest("GET", "/portfolios")).toThrow(ApiError);
    expect((await api.terms()).accepted).toBe(false);
    await expect(api.acceptTerms("1999-01-01")).rejects.toMatchObject({ status: 409 });
    const out = await api.acceptTerms((await api.terms()).version);
    expect(out.accepted).toBe(true);
    expect(mockRequest("GET", "/portfolios")).toBeTruthy();
  });

  it("AuthGate sends a user who has not accepted to the terms page, remembering where they were", async () => {
    expect((mockRequest("GET", "/auth/me") as { terms_accepted: boolean }).terms_accepted).toBe(false);
    wrap(<AuthGate><p>secret</p></AuthGate>);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/terms?next=%2Freview"));
    expect(screen.queryByText("secret")).not.toBeInTheDocument();
  });

  it.each(["en", "he"] as const)("terms page (%s): Accept is disabled until the box is ticked, then accepts and goes back", async (locale) => {
    window.history.replaceState(null, "", "/x?next=%2Freview");
    const m = locale === "en" ? en : he;
    wrap(<TermsPage />, locale);
    const box = await screen.findByRole("checkbox", { name: m.terms.checkbox });
    const btn = screen.getByRole("button", { name: m.terms.accept });
    expect(btn).toBeDisabled();
    expect(screen.getByTestId("terms-version")).toHaveTextContent("05/10/2026");
    expect(screen.getAllByRole("heading", { level: 2 })).toHaveLength(m.terms.sections.length);
    fireEvent.click(box);
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/review"));
    await mutate(() => true, undefined, { revalidate: false });
  });

  it("is read-only when logged out (no checkbox)", async () => {
    vi.spyOn(api, "me").mockRejectedValue(new ApiError(401, "Not authenticated"));
    wrap(<TermsPage />);
    expect(await screen.findByText(en.terms.readOnly)).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.getByText(en.terms.sections[2].title, { exact: false })).toBeInTheDocument();
  });

  it("only same-app paths are valid redirect targets", () => {
    expect(safeNext("/review")).toBe("/review");
    expect(safeNext("//evil.com")).toBeNull();
    expect(safeNext("https://evil.com")).toBeNull();
    expect(safeNext("/terms")).toBeNull();
    expect(termsHref("/review")).toBe("/terms?next=%2Freview");
    expect(termsHref("//x")).toBe("/terms");
  });
});
