import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.E2E_PORT ?? 3210);
// Chromium for both runs: the sandbox ships only Chromium (PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers).
const { defaultBrowserType: _ignored, ...iphone } = devices["iPhone 13"];
void _ignored;

export default defineConfig({
  testDir: "./e2e",
  testMatch: /.*\.spec\.ts/,
  testIgnore: /e2e[\\/]real[\\/]/, // the real-backend pass has its own config (playwright.real.config.ts)
  outputDir: "./e2e/.results",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  workers: 2,
  reporter: [["list"]],
  webServer: {
    command: "node e2e/serve.mjs",
    url: `http://localhost:${PORT}/he/`,
    timeout: 300_000,
    reuseExistingServer: true,
  },
  use: { baseURL: `http://localhost:${PORT}`, browserName: "chromium", locale: "en-US" },
  projects: [
    { name: "mobile", use: { ...iphone } },
    { name: "desktop", use: { viewport: { width: 1280, height: 800 } } },
    // E2E_DARK=1: the whole suite again on the phone in dark mode (shots get a "-dark" suffix).
    ...(process.env.E2E_DARK === "1"
      ? [{ name: "mobile-dark", use: { ...iphone, colorScheme: "dark" as const } }]
      : []),
  ],
});
