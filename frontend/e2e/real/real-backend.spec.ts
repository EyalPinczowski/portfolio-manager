// One user journey against the real API (backend/scripts/e2e_server.py): Turnstile after failed logins,
// first portfolio, on-device screenshot import, and the null-price paths. AAPL has a fixed quote and
// history; NVDA has neither, so it must show "no data" and "no levels" instead of a made-up 0.
import { expect, test, type Page } from "@playwright/test";
import { checkScreen, msg } from "../helpers";

const m = (k: string, v?: Record<string, string | number>) => msg("en", k, v);
const EMAIL = process.env.E2E_ADMIN_EMAIL ?? "e2e@example.com";
const PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "e2e-long-password";
const TOKEN = "e2e-turnstile-ok"; // the only token the siteverify stub in serve-real.mjs accepts

// Stand-in for Cloudflare's script: renders one button that hands back the token, like a solved challenge.
const FAKE_TURNSTILE = `window.turnstile = {
  render(el, o) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = "e2e check"; b.onclick = () => o.callback(${JSON.stringify(TOKEN)});
    el.appendChild(b); return "w1";
  },
  remove() {}, reset() {},
};`;

// Invented Meitav Trade card list (layout of frontend/tests/fixtures/meitav) with real seed symbols.
const CARDS = [
  "12:41", "85%", "תיק ההשקעות שלי", "",
  "NASDAQ • AAPL 231.10", "Apple Inc 0.40%", "+5.00% ↑ @ $2,311.00",
  "NASDAQ • NVDA 142.30", "NVIDIA Corp 2.40%", "+10.00% ↑ @ $1,423.00",
  "בית    תיק    שוק    עוד",
];

/** Renders the card text as a phone screenshot (PNG bytes, viewport-sized like a real one) in a separate page. */
async function screenshotOfCards(page: Page): Promise<Buffer> {
  const p = await page.context().newPage();
  await p.setViewportSize({ width: 390, height: 844 });
  const lines = CARDS.map((l) => `<div>${l || "&nbsp;"}</div>`).join("");
  // Phone width (without the viewport tag a mobile page lays out 980 px wide and the text is tiny). The status
  // bar and app header sit in the top part that the reader blanks, like on a real screenshot.
  await p.setContent(
    `<meta name="viewport" content="width=device-width,initial-scale=1">` +
      `<body style="margin:0;padding:24px;background:#fff;color:#000;font:20px/1.7 Arial,sans-serif">${lines}</body>`,
  );
  const png = await p.screenshot();
  await p.close();
  return png;
}

/** Collects failed same-origin requests except the ones this journey causes on purpose. */
function watch(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("response", (r) => {
    const u = r.url();
    if (!u.startsWith("http://localhost") || r.status() < 400) return;
    if (u.endsWith("/api/auth/login") && [401, 403].includes(r.status())) return; // wrong password, challenge
    if (u.endsWith("/api/auth/me") && r.status() === 401) return; // not signed in yet
    errors.push(`http ${r.status()}: ${u}`);
  });
  return { expectNone: () => expect(errors).toEqual([]) };
}

test("real backend: challenge, first portfolio, import, null-price paths", async ({ page }) => {
  const errs = watch(page);
  await page.route("https://challenges.cloudflare.com/**", (r) =>
    r.fulfill({ status: 200, contentType: "text/javascript", body: FAKE_TURNSTILE }),
  );
  const login = page.getByRole("button", { name: m("auth.login"), exact: true });

  // 1. Three wrong passwords, then the right one: the server asks for the challenge.
  await page.goto("/en/login/");
  await page.getByLabel(m("auth.email")).fill(EMAIL);
  for (let i = 0; i < 3; i++) {
    await page.getByLabel(m("auth.password")).fill(`wrong-password-${i}`);
    await login.click();
    await expect(page.getByRole("alert").first()).toBeVisible();
  }
  await page.getByLabel(m("auth.password")).fill(PASSWORD);
  await login.click();
  await expect(page.getByText(m("auth.challengeTitle"))).toBeVisible();
  await page.getByRole("button", { name: "e2e check" }).click();
  await login.click();

  // 1b. Terms: the server refuses everything else until the current version is accepted.
  await expect(page.getByRole("heading", { name: m("terms.title") })).toBeVisible();
  const accept = page.getByRole("button", { name: m("terms.accept") });
  await expect(accept).toBeDisabled();
  await page.getByLabel(m("terms.checkbox")).check();
  await accept.click();

  // 1c. The welcome tour opens once after the terms; it can always be skipped.
  const tour = page.getByRole("dialog", { name: m("tour.title") });
  await expect(tour).toBeVisible();
  await tour.getByRole("button", { name: m("tour.skip") }).click();
  await expect(tour).toBeHidden();

  // 2. First portfolio.
  await expect(page.getByText(m("createPortfolio.title"))).toBeVisible();
  await checkScreen(page, "en");
  await page.getByLabel(m("createPortfolio.name")).fill("E2E");
  await page.getByRole("button", { name: m("createPortfolio.create") }).click();

  // 3. Import a screenshot, read on this device, confirm.
  await page.goto("/en/import/");
  await page.locator("#imp-file").setInputFiles({ name: "cards.png", mimeType: "image/png", buffer: await screenshotOfCards(page) });
  await page.getByRole("button", { name: m("import.readOnDevice") }).click();
  const review = page.getByRole("region", { name: m("import.reviewTitle") });
  await expect(review).toBeVisible({ timeout: 150_000 });
  await expect(review.getByLabel(`${m("import.col.symbol")} 1`)).toHaveValue("AAPL");
  await expect(review.getByLabel(`${m("import.col.symbol")} 2`)).toHaveValue("NVDA");
  await checkScreen(page, "en"); // the wide review table scrolls inside its box, never the page
  // 3b. Add a stock that is not in the screenshot, by hand: search the (seeded, so offline) ticker, pick it, enter a quantity.
  await review.getByRole("button", { name: m("import.addRow") }).click();
  const added = review.getByLabel(`${m("import.col.symbol")} 3`);
  await expect(added).toBeVisible();
  await added.fill("MSFT");
  await page.getByRole("option", { name: /MSFT/ }).first().click();
  await expect(added).toHaveValue("MSFT");
  await review.getByLabel(`${m("import.col.quantity")} 3`).fill("2");
  await expect(page.getByRole("button", { name: m("import.confirm") })).toBeEnabled();
  await checkScreen(page, "en");
  await page.getByRole("button", { name: m("import.confirm") }).click();
  await expect(page.getByText(m("import.done"))).toBeVisible();

  // 4. Home: AAPL is priced; NVDA has no quote and says so (no zero, no crash).
  await page.goto("/en/");
  await expect(page.getByText(m("holdings.title"), { exact: true })).toBeVisible();
  const card = (sym: string) => page.locator("article, li, section").filter({ hasText: sym }).last();
  await expect(card("AAPL")).toContainText("2,311.00"); // 10 x 231.10: the row shows the position value
  await expect(card("NVDA")).toContainText(m("holdings.noData"));
  await expect(card("NVDA")).not.toContainText("$0.00");
  await expect(card("MSFT")).toBeVisible(); // the hand-added row was confirmed with the others
  await checkScreen(page, "en");

  // 4b. The setup is required before other pages open: a risk level, then a holding period for each
  // holding (suggested with a reason, never pre-selected; the user taps to use it).
  await page.getByRole("radio", { name: new RegExp(`^${m("settings.presets.balanced")}(?!-)`) }).check();
  await page.getByRole("button", { name: m("guide.riskSave") }).click();
  await expect(page.getByText(m("guide.suggest.reason.stockMid")).first()).toBeVisible();
  await page.getByRole("button", { name: m("guide.suggest.useAll") }).click();
  await expect(page.getByText(m("guide.suggest.reason.stockMid"))).toHaveCount(0);

  // 5. NVDA holding page: after choosing a holding period, exit levels explain why there are none.
  await card("NVDA").getByRole("link").first().click();
  await page.getByRole("radio", { name: m("holding.horizons.3m") }).first().click();
  await expect(page.getByText(m("exit.noLevelsTitle"))).toBeVisible();
  await checkScreen(page, "en");
  errs.expectNone();
});
