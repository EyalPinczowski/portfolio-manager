import { afterEach, describe, expect, it } from "vitest";
import { loadTurnstile, resetTurnstileLoader, TURNSTILE_SRC } from "@/lib/turnstile";

describe("Turnstile loader", () => {
  afterEach(() => { document.head.innerHTML = ""; delete window.turnstile; resetTurnstileLoader(); });

  it("adds no script until loadTurnstile() is called, then adds exactly one (the Cloudflare origin only)", async () => {
    expect(document.querySelector("script")).toBeNull();
    const p = loadTurnstile();
    const p2 = loadTurnstile();
    const scripts = document.querySelectorAll("script");
    expect(scripts).toHaveLength(1);
    expect(scripts[0].src).toBe(TURNSTILE_SRC);
    expect(new URL(scripts[0].src).origin).toBe("https://challenges.cloudflare.com");
    window.turnstile = { render: () => "id", remove: () => {}, reset: () => {} };
    scripts[0].onload?.(new Event("load"));
    await expect(p).resolves.toBe(window.turnstile);
    await expect(p2).resolves.toBe(window.turnstile);
  });

  it("rejects and allows a retry when the script cannot load", async () => {
    const p = loadTurnstile();
    document.querySelector("script")!.onerror?.(new Event("error"));
    await expect(p).rejects.toThrow();
    loadTurnstile().catch(() => {});
    expect(document.querySelectorAll("script")).toHaveLength(1);
  });
});
