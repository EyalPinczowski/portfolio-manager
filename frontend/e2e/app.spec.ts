import { expect, test, type Page } from "@playwright/test";
import { checkScreen, expectDir, LOCALES, msg, open, shot, watchErrors, type Locale } from "./helpers";

const heading = (page: Page, text: string) => page.getByRole("heading", { name: text, exact: true }).first();

for (const locale of LOCALES) {
  test.describe(`flows (${locale})`, () => {
    const m = (k: string, v?: Record<string, string | number>) => msg(locale, k, v);

    test("login (mock) lands on the main page", async ({ page }, info) => {
      const errs = watchErrors(page);
      await open(page, locale, "/login/");
      await expect(heading(page, m("auth.loginTitle"))).toBeVisible();
      await checkScreen(page, locale);
      await page.getByLabel(m("auth.email")).fill("demo@example.com");
      await page.getByLabel(m("auth.password")).fill("a-long-demo-password");
      await shot(page, info, `login-${locale}`);
      await page.getByRole("button", { name: m("auth.login"), exact: true }).click();
      await expect(page.getByText(m("holdings.title"), { exact: true })).toBeVisible();
      errs.expectNone();
    });

    test("main page", async ({ page }, info) => {
      const errs = watchErrors(page);
      await open(page, locale, "/");
      await expect(page.getByText(m("holdings.title"), { exact: true })).toBeVisible();
      await expect(page.getByText(m("pnl.week"), { exact: true })).toBeVisible();
      await checkScreen(page, locale);
      await shot(page, info, `main-${locale}`, locale === "he");
      errs.expectNone();
    });

    test("holding page: exit levels with the scale-out plan", async ({ page }, info) => {
      const errs = watchErrors(page);
      await open(page, locale, "/");
      await page.locator('a[href*="holding"]').first().click();
      await expect(page).toHaveURL(/holding\/?\?id=/);
      const plan = page.getByTestId("scale-plan");
      await expect(plan).toBeVisible();
      await expect(page.getByTestId("plan-planFirst")).toBeVisible();
      await expect(page.getByTestId("plan-planSecond")).toBeVisible();
      await expect(page.getByTestId("plan-planTrail")).toBeVisible();
      await plan.scrollIntoViewIfNeeded();
      await checkScreen(page, locale);
      await shot(page, info, `exit-levels-${locale}`, locale === "he");
      errs.expectNone();
    });

    test("review my portfolio", async ({ page }, info) => {
      const errs = watchErrors(page);
      await open(page, locale, "/review/");
      await expect(heading(page, m("review.title"))).toBeVisible();
      await expect(page.getByText(m("review.totalsTitle"), { exact: true })).toBeVisible();
      await checkScreen(page, locale);
      await shot(page, info, `review-${locale}`, locale === "en");
      errs.expectNone();
    });

    test("analyze: search, fit section first, watchlist star, recent searches", async ({ page }, info) => {
      const errs = watchErrors(page);
      await open(page, locale, "/analyze/");
      await expect(heading(page, m("analyze.title"))).toBeVisible();
      await expect(page.getByText(m("analyze.recentTitle"), { exact: true })).toBeVisible();
      await expect(page.getByText(m("analyze.watchTitle"), { exact: true })).toBeVisible();
      await shot(page, info, `analyze-home-${locale}`);

      await page.getByLabel(m("analyze.searchLabel")).fill("nvd");
      // Suggestions appear while typing; open the matching one (submitting would analyze the typed text itself).
      await page.getByRole("list", { name: m("analyze.searchGo") }).getByRole("link", { name: /NVDA/ }).click();
      await expect(page).toHaveURL(/symbol=NVDA/);
      await expect(page.getByText(/NVDA/).first()).toBeVisible();

      // The "does it fit my portfolio" section comes before the signal breakdown.
      const fit = page.getByRole("heading", { name: m("analyze.fitTitle") }).first();
      const signals = page.getByRole("heading", { name: m("analyze.signalsTitle") }).first();
      await expect(fit).toBeVisible();
      await expect(signals).toBeVisible();
      const [fy, sy] = [(await fit.boundingBox())!.y, (await signals.boundingBox())!.y];
      expect(fy, "fit section must be above the signals").toBeLessThan(sy);

      // Watchlist star toggles.
      const add = page.getByRole("button", { name: m("analyze.addWatch", { symbol: "NVDA" }) });
      const inWatch = page.getByRole("button", { name: m("analyze.inWatch", { symbol: "NVDA" }) });
      await expect(add.or(inWatch)).toBeVisible();
      const before = await inWatch.count();
      await add.or(inWatch).click();
      await expect(before ? add : inWatch).toBeVisible();

      await checkScreen(page, locale);
      await shot(page, info, `analyze-result-${locale}`, locale === "he");

      // Recent searches (the mock does not record new ones, the real server does): show, open and remove one.
      await open(page, locale, "/analyze/");
      const recent = page.getByRole("region", { name: m("analyze.recentTitle") });
      await expect(recent.getByRole("link", { name: m("analyze.open", { symbol: "AMD" }) })).toBeVisible();
      await recent.getByRole("button", { name: m("analyze.removeSearch", { symbol: "AMD" }) }).click();
      await expect(recent.getByRole("link", { name: m("analyze.open", { symbol: "AMD" }) })).toHaveCount(0);
      errs.expectNone();
    });

    test("suggest new stocks: no defaults, results, skipped list", async ({ page }, info) => {
      const errs = watchErrors(page);
      await open(page, locale, "/suggest/");
      await expect(heading(page, m("suggest.title"))).toBeVisible();
      // No defaults: nothing is selected and submitting is blocked.
      await expect(page.getByRole("radio", { checked: true })).toHaveCount(0);
      await expect(page.getByLabel(m("suggest.amount"))).toHaveValue("");
      await expect(page.getByText(m("suggest.fillAll"))).toBeVisible();
      await expect(page.getByRole("button", { name: m("suggest.submit") })).toBeDisabled();
      await checkScreen(page, locale);

      await page.getByLabel(m("suggest.amount")).fill("20000");
      await page.getByRole("radio", { name: "₪ ILS" }).click();
      await page.getByRole("radio", { name: m("holding.horizons.3m"), exact: true }).click();
      await page.getByLabel(m("suggest.risk")).selectOption({ index: 3 });
      for (const grp of ["markets", "assetTypes"]) {
        const box = page.getByRole("group", { name: m(`suggest.${grp}`) }).getByRole("checkbox").first();
        await box.check();
      }
      await page.getByRole("button", { name: m("suggest.submit") }).click();
      await expect(page.getByText(/#1/).first()).toBeVisible();
      await expect(page.getByText(m("suggest.skippedTitle", { n: "" }).split("(")[0].trim(), { exact: false }).first()).toBeVisible();
      await checkScreen(page, locale);
      await shot(page, info, `suggest-results-${locale}`, locale === "en");
      errs.expectNone();
    });

    test("post-mortem: set an expectation", async ({ page }, info) => {
      const errs = watchErrors(page);
      await open(page, locale, "/postmortem/");
      await expect(heading(page, m("postmortem.title"))).toBeVisible();
      await expect(page.getByText(m("postmortem.notSet"), { exact: true }).first()).toBeVisible();
      await page.getByLabel(m("postmortem.expPct")).fill("8");
      await page.getByLabel(m("postmortem.expMonths")).fill("12");
      await page.getByRole("button", { name: m("postmortem.expSave"), exact: true }).click();
      await expect(page.getByText(/8/).first()).toBeVisible();
      await checkScreen(page, locale);
      await shot(page, info, `postmortem-${locale}`, locale === "he");
      errs.expectNone();
    });

    test("settings: hub, drill-down, toggle, confirm sheet", async ({ page }, info) => {
      const errs = watchErrors(page);
      await open(page, locale, "/settings/");
      await expect(heading(page, m("settings.title"))).toBeVisible();
      await checkScreen(page, locale);
      await shot(page, info, `settings-hub-${locale}`, true);

      // Toggle on the hub.
      const sw = page.getByRole("switch", { name: m("prefs.sections.priceAlerts") });
      const was = await sw.getAttribute("aria-checked");
      await sw.click();
      await expect(sw).not.toHaveAttribute("aria-checked", was!);
      await sw.click();

      // Drill-down: appearance.
      await page.getByRole("link", { name: new RegExp(m("prefs.sections.appearance")) }).first().click();
      await expect(page).toHaveURL(/section=appearance/);
      await checkScreen(page, locale);
      await shot(page, info, `settings-appearance-${locale}`);

      // Confirm sheet: account -> sign out.
      await open(page, locale, "/settings/?section=account");
      await page.locator("main").getByRole("button", { name: m("nav.logout") }).click();
      const dlg = page.getByRole("dialog", { name: m("prefs.account.logoutTitle") });
      await expect(dlg).toBeVisible();
      await shot(page, info, `settings-confirm-${locale}`);
      await dlg.getByRole("button", { name: m("common.cancel") }).click();
      await expect(dlg).toHaveCount(0);
      errs.expectNone();
    });

    test("settings: privacy and AI page, system about, main currency reaches the home page", async ({ page }, info) => {
      const errs = watchErrors(page);
      await open(page, locale, "/settings/?section=privacy");
      await expect(heading(page, m("prefs.sections.privacy"))).toBeVisible();
      await expect(page.getByText(m("prefs.privacy.aiLead"))).toBeVisible();
      await expect(page.getByText(m("prefs.privacy.shotsLead"))).toBeVisible();
      await checkScreen(page, locale);
      await shot(page, info, `settings-privacy-${locale}`);
      await open(page, locale, "/settings/?section=status");
      await expect(page.getByTestId("data-sources")).toBeVisible();
      await expect(page.getByTestId("web-version")).toBeVisible();
      await expectDir(page, locale); // the launch-gate heading on this page names "suggestions" by design, so no wording check

      // Client-side navigation keeps the mock state: pick the dollar, then the home page shows it large.
      await open(page, locale, "/settings/?section=currency");
      await page.getByRole("radio", { name: m("prefs.appearance.currencies.USD") }).click();
      await expect(page.getByRole("radio", { name: m("prefs.appearance.currencies.USD") })).toHaveAttribute("aria-checked", "true");
      await page.locator("nav[aria-label]:visible").getByRole("link", { name: m("nav.home") }).first().click();
      await expect(page.getByTestId("total-main")).toContainText("$");
      await expect(page.getByTestId("total-other")).toContainText("₪");
      errs.expectNone();
    });

    test("track record", async ({ page }, info) => {
      const errs = watchErrors(page);
      await open(page, locale, "/track-record/");
      await expect(heading(page, m("trackRecord.title"))).toBeVisible();
      await expect(page.getByText(m("trackRecord.tableTitle"), { exact: true })).toBeVisible();
      await checkScreen(page, locale);
      await shot(page, info, `track-record-${locale}`);
      errs.expectNone();
    });

    test("x-ray rules: currency shows 'No limit set'", async ({ page }, info) => {
      const errs = watchErrors(page);
      await open(page, locale, "/xray/");
      await expect(page.getByText(m("xray.noLimit"), { exact: false }).first()).toBeVisible();
      await checkScreen(page, locale);
      await shot(page, info, `xray-${locale}`, locale === "he");
      errs.expectNone();
    });

    test("dividends", async ({ page }, info) => {
      const errs = watchErrors(page);
      await open(page, locale, "/dividends/");
      await expect(heading(page, m("dividends.title"))).toBeVisible();
      await expect(page.getByText(m("dividends.incomeTitle"), { exact: true })).toBeVisible();
      await checkScreen(page, locale);
      await shot(page, info, `dividends-${locale}`);
      errs.expectNone();
    });

    test("ask my portfolio: answer with tools used, holding-period prompt, delete", async ({ page }, info) => {
      const errs = watchErrors(page);
      await open(page, locale, "/ask/");
      await expect(heading(page, m("ask.title"))).toBeVisible();
      await checkScreen(page, locale);
      await page.getByLabel(m("ask.askLabel")).fill("What are my exit levels?");
      await page.getByRole("button", { name: m("ask.send"), exact: true }).click();
      await expect(page.getByTestId("tools-used")).toBeVisible();
      await expect(page.getByTestId("tools-used")).toContainText(m("ask.tool.get_exit_levels"));
      const prompt = page.getByTestId("needs-horizon");
      await expect(prompt).toBeVisible();
      await expect(prompt.getByTestId("needs-horizon-link")).toHaveAttribute("href", /holding\/?\?id=\d+/);
      await expect(page.getByTestId("conversation")).toHaveCount(1);
      await checkScreen(page, locale);
      await shot(page, info, `ask-${locale}`, locale === "en");
      await page.getByRole("button", { name: m("ask.deleteConversation", { title: "What are my exit levels?" }) }).click();
      const dlg = page.getByRole("dialog", { name: m("ask.deleteTitle") });
      await expect(dlg).toBeVisible();
      await shot(page, info, `ask-delete-${locale}`);
      await dlg.getByRole("button", { name: m("ask.deleteYes") }).click();
      await expect(page.getByTestId("no-conversations")).toBeVisible();
      errs.expectNone();
    });

    test("analyze: investment committee report", async ({ page }, info) => {
      const errs = watchErrors(page);
      await open(page, locale, "/analyze/?symbol=NVDA");
      const section = page.getByTestId("committee");
      await expect(section).toBeVisible();
      await section.getByRole("button", { name: m("committee.run"), exact: true }).click();
      await expect(page.getByTestId("committee-report")).toBeVisible();
      await expect(page.getByTestId("committee-risk")).toHaveCount(3);
      await expect(page.getByTestId("committee-gate")).toBeVisible();
      await section.scrollIntoViewIfNeeded();
      await checkScreen(page, locale);
      await shot(page, info, `committee-${locale}`);
      errs.expectNone();
    });

    test("add a fund flow", async ({ page }, info) => {
      const errs = watchErrors(page);
      await open(page, locale, "/");
      await page.getByRole("button", { name: m("addHolding.open") }).click();
      await page.getByRole("button", { name: m("addHolding.fundOpen"), exact: true }).click();
      const dlg = page.getByRole("dialog", { name: m("addHolding.fundTitle") });
      await expect(dlg).toBeVisible();
      await dlg.getByLabel(m("funds.searchLabel")).fill("1001");
      await dlg.getByRole("button", { name: m("funds.search"), exact: true }).click();
      await expect(dlg.getByTestId("fund-results")).toBeVisible();
      await shot(page, info, `fund-search-${locale}`);
      await dlg.getByTestId("fund-results").getByRole("button").first().click();
      await expect(dlg.getByText(m("funds.noPrice").slice(0, 20), { exact: false }).first()).toBeVisible();
      await expect(dlg.getByRole("button", { name: m("funds.addThis") })).toBeVisible();
      await shot(page, info, `fund-detail-${locale}`);
      errs.expectNone();
    });

    test("terms gate: an account that has not accepted is sent to the terms page and back", async ({ page }, info) => {
      const errs = watchErrors(page);
      await page.addInitScript(() => window.localStorage.setItem("pm.mock", "terms"));
      await page.goto(`/${locale}/`);
      await expect(heading(page, m("terms.title"))).toBeVisible();
      await expect(page).toHaveURL(/terms/);
      await expectDir(page, locale);
      const accept = page.getByRole("button", { name: m("terms.accept"), exact: true });
      await expect(accept).toBeDisabled();
      await shot(page, info, `terms-${locale}`);
      await page.getByRole("checkbox", { name: m("terms.checkbox") }).check();
      await expect(accept).toBeEnabled();
      await accept.click();
      await expect(page.getByText(m("holdings.title"), { exact: true })).toBeVisible();
      errs.expectNone();
    });
  });
}

test("dark mode renders the main page and settings hub", async ({ browser }, info) => {
  const ctx = await browser.newContext({ ...info.project.use, colorScheme: "dark" });
  const page = await ctx.newPage();
  const errs = watchErrors(page);
  for (const [path, name] of [["/", "main"], ["/settings/", "settings"]] as const) {
    await open(page, "he" as Locale, path);
    await page.waitForTimeout(300);
    await shot(page, { ...info, project: { ...info.project, use: { ...info.project.use, colorScheme: "dark" } } } as typeof info, `${name}-he`);
  }
  errs.expectNone();
  await ctx.close();
});
