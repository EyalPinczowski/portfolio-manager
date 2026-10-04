import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import type { ReactNode } from "react";
import en from "@/messages/en.json";
import he from "@/messages/he.json";

vi.stubEnv("NEXT_PUBLIC_API_MOCK", "1");
const replace = vi.fn();
vi.mock("@/i18n/navigation", () => ({
  Link: ({ href, children, ...rest }: { href: string; children: ReactNode }) => <a href={href} {...rest}>{children}</a>,
  usePathname: () => "/login",
  useRouter: () => ({ replace, push: vi.fn() }),
}));

import { api } from "@/lib/api";
import { MOCK_SITE_KEY, MOCK_TURNSTILE_TOKEN } from "@/lib/mock";
import { AuthForm } from "@/components/AuthForm";

const renderForm = (locale: "en" | "he" = "en") =>
  render(<NextIntlClientProvider locale={locale} messages={locale === "en" ? en : he}><AuthForm mode="login" /></NextIntlClientProvider>);

const fill = (email: string) => {
  fireEvent.change(screen.getByLabelText(en.auth.email), { target: { value: email } });
  fireEvent.change(screen.getByLabelText(en.auth.password), { target: { value: "pw" } });
};
const submit = () => fireEvent.click(screen.getByRole("button", { name: en.auth.login }));
const thirdParty = () => document.querySelector('script[src*="challenges.cloudflare.com"], iframe[src*="challenges.cloudflare.com"]');

describe("login with Turnstile", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); replace.mockClear(); });

  it("shows no widget and loads nothing third-party on the first render or a normal login", async () => {
    const login = vi.spyOn(api, "login");
    renderForm();
    expect(screen.queryByTestId("turnstile-stub")).toBeNull();
    expect(screen.queryByTestId("turnstile-real")).toBeNull();
    expect(thirdParty()).toBeNull();
    fill("demo@example.com");
    submit();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
    expect(login).toHaveBeenCalledWith({ email: "demo@example.com", password: "pw" });
    expect(screen.queryByTestId("turnstile-stub")).toBeNull();
  });

  it("shows the widget with the server's site key after a 403 turnstile_required, then retries with the token", async () => {
    const login = vi.spyOn(api, "login");
    renderForm();
    fill("challenge@example.com");
    submit();
    const widget = await screen.findByTestId("turnstile-stub");
    expect(widget).toHaveAttribute("data-sitekey", MOCK_SITE_KEY);
    expect(screen.getByRole("alert")).toHaveTextContent(en.auth.challengeBody);
    expect(login).toHaveBeenCalledTimes(1);
    expect(login.mock.calls[0][0]).not.toHaveProperty("turnstile_token");

    fireEvent.click(screen.getByRole("button", { name: en.auth.challengeLabel }));
    submit();
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
    expect(login).toHaveBeenCalledTimes(2);
    expect(login.mock.calls[1][0]).toEqual({ email: "challenge@example.com", password: "pw", turnstile_token: MOCK_TURNSTILE_TOKEN });
  });

  it("does not call the API when submitting before the challenge is solved, and says so", async () => {
    const login = vi.spyOn(api, "login");
    renderForm();
    fill("challenge@example.com");
    submit();
    await screen.findByTestId("turnstile-stub");
    submit();
    expect(await screen.findByText(en.auth.challengeMissing)).toBeInTheDocument();
    expect(login).toHaveBeenCalledTimes(1);
  });

  it("shows the invalid-token message when the server repeats the 403 after a token was sent", async () => {
    const login = vi.spyOn(api, "login");
    renderForm();
    fill("challenge@example.com");
    submit();
    await screen.findByTestId("turnstile-stub");
    // The stub's token is valid for the mock; make the server reject it.
    login.mockRejectedValueOnce(Object.assign(new (await import("@/lib/errors")).ApiError(403, "x", undefined, { detail: "x", code: "turnstile_required", site_key: "k2" })));
    fireEvent.click(screen.getByRole("button", { name: en.auth.challengeLabel }));
    submit();
    expect(await screen.findByText(en.auth.challengeInvalid)).toBeInTheDocument();
    expect(screen.getByTestId("turnstile-stub")).toHaveAttribute("data-sitekey", "k2");
    expect(replace).not.toHaveBeenCalled();
  });

  it("429 shows the wait in seconds (English and Hebrew)", async () => {
    renderForm();
    fill("ratelimit@example.com");
    submit();
    expect(await screen.findByText("Too many attempts. Try again in 30 seconds.")).toBeInTheDocument();
    expect(screen.queryByTestId("turnstile-stub")).toBeNull();
    cleanup();
    renderForm("he");
    fireEvent.change(screen.getByLabelText(he.auth.email), { target: { value: "ratelimit@example.com" } });
    fireEvent.change(screen.getByLabelText(he.auth.password), { target: { value: "pw" } });
    fireEvent.click(screen.getByRole("button", { name: he.auth.login }));
    expect(await screen.findByText(he.auth.tooManyWait.replace("{seconds}", "30"))).toBeInTheDocument();
  });
});
