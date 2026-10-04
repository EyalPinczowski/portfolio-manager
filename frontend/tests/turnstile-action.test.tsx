import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import en from "@/messages/en.json";
import { CloudflareTurnstile } from "@/components/TurnstileWidget";
import { resetTurnstileLoader } from "@/lib/turnstile";

describe("real Turnstile widget", () => {
  afterEach(() => { cleanup(); document.head.innerHTML = ""; delete window.turnstile; resetTurnstileLoader(); });

  it('renders with action "login" (the server verifies the action) and the given site key', async () => {
    const renderSpy = vi.fn(() => "widget-id");
    window.turnstile = { render: renderSpy, remove: () => {}, reset: () => {} };
    render(
      <NextIntlClientProvider locale="en" messages={en}>
        <CloudflareTurnstile siteKey="site-key-1" onToken={() => {}} />
      </NextIntlClientProvider>,
    );
    await waitFor(() => expect(renderSpy).toHaveBeenCalledTimes(1));
    const opts = (renderSpy.mock.calls[0] as unknown as [HTMLElement, { sitekey: string; action?: string }])[1];
    expect(opts.sitekey).toBe("site-key-1");
    expect(opts.action).toBe("login");
  });
});
