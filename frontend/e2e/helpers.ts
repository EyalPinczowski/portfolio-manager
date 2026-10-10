import { expect, type Page, type TestInfo } from "@playwright/test";
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import en from "../messages/en.json";
import he from "../messages/he.json";

export type Locale = "he" | "en";
export const LOCALES: Locale[] = ["he", "en"];
const MSGS = { en, he } as Record<Locale, unknown>;

/** The app's own message for a dotted key, so tests follow the translation files instead of copying strings. */
export function msg(locale: Locale, key: string, vars: Record<string, string | number> = {}): string {
  let v: unknown = MSGS[locale];
  for (const part of key.split(".")) v = (v as Record<string, unknown> | undefined)?.[part];
  if (typeof v !== "string") throw new Error(`missing message ${locale}:${key}`);
  return v.replace(/\{(\w+)\}/g, (_, k: string) => String(vars[k] ?? `{${k}}`));
}

/** Collects console errors, page errors and failed same-origin requests; call `.expectNone()` at the end of a test. */
export function watchErrors(page: Page) {
  const errors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(`console: ${m.text()}`); });
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  // Prefetches cancelled by a navigation (ERR_ABORTED) are normal, not errors.
  page.on("requestfailed", (r) => { if (r.url().startsWith("http://localhost") && !/ERR_ABORTED/.test(r.failure()?.errorText ?? "")) errors.push(`requestfailed: ${r.url()}`); });
  page.on("response", (r) => { if (r.url().startsWith("http://localhost") && r.status() >= 400) errors.push(`http ${r.status()}: ${r.url()}`); });
  return { expectNone: () => expect(errors).toEqual([]) };
}

export async function noHorizontalScroll(page: Page) {
  const w = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
  expect(w.scroll, "page is wider than the viewport").toBeLessThanOrEqual(w.inner);
}

export async function expectDir(page: Page, locale: Locale) {
  await expect(page.locator("html")).toHaveAttribute("dir", locale === "he" ? "rtl" : "ltr");
  await expect(page.locator("html")).toHaveAttribute("lang", locale);
}

// Sentences that say what the app is NOT (disclaimers) are allowed to contain these words.
const NEGATED = /\bnot\b|\bnever\b|\bno\b|neither|אינ|לא |ללא|אין /i;
const FORBIDDEN = /\b(buy|sell|recommend\w*)\b|קנה|קנייה|קניה|מכור|מכירה|המלצ/i;
/** No buy/sell/recommend wording outside of disclaimers ("this is not a recommendation"). */
export async function noAdviceWording(page: Page) {
  // The analysts' own rating categories (Strong buy ... Strong sell) are the analysts' words, shown as reported inside the analyst card.
  await page.addStyleTag({ content: '[data-testid="analyst-view"] { display: none !important; }' });
  const text = await page.locator("body").innerText();
  await page.evaluate(() => document.querySelectorAll("style").forEach((s) => { if (s.textContent?.includes('data-testid="analyst-view"') && s.textContent.includes("display: none")) s.remove(); }));
  const bad = text.split(/\n|(?<=[.!?])\s+/).filter((l) => FORBIDDEN.test(l) && !NEGATED.test(l));
  expect(bad, "buy/sell/recommend wording on screen").toEqual([]);
}

/** Standard per-screen checks: direction, no horizontal scroll (mobile), no advice wording. */
export async function checkScreen(page: Page, locale: Locale) {
  await expectDir(page, locale);
  const vw = page.viewportSize()?.width ?? 0;
  if (vw <= 430) await noHorizontalScroll(page);
  await noAdviceWording(page);
}

const ROOT = join(__dirname, "screenshots");
/** Saves a screenshot. `curated` ones go to e2e/screenshots/ (committed, mobile only); the rest to screenshots/all/ (ignored). */
export async function shot(page: Page, info: TestInfo, name: string, curated = false) {
  const dir = curated ? ROOT : join(ROOT, "all", info.project.name);
  mkdirSync(dir, { recursive: true });
  const scheme = info.project.use.colorScheme === "dark" ? "-dark" : "";
  if (curated && info.project.name !== "mobile") return;
  // Curated shots are viewport-sized (what a phone shows); the rest are full page.
  await page.screenshot({ path: join(dir, `${name}${scheme}.png`), fullPage: !curated });
}

/** Opens a page in a locale and waits until the signed-in mock shell has rendered. */
export async function open(page: Page, locale: Locale, path: string) {
  await page.goto(`/${locale}${path}`);
  await expect(page.locator("main, [role=main], h1").first()).toBeVisible();
}
