import { defineConfig, devices } from "@playwright/test";

// Real-backend pass: the normal static export + the FastAPI app (fixed offline quotes) behind one origin.
// `npm run e2e:real`. The mock pass (`npm run e2e`) stays the default.
const PORT = Number(process.env.E2E_REAL_PORT ?? 3220);
const { defaultBrowserType: _ignored, ...iphone } = devices["iPhone 13"];
void _ignored;

export default defineConfig({
  testDir: "./e2e/real",
  testMatch: /.*\.spec\.ts/,
  outputDir: "./e2e/.results-real",
  timeout: 180_000, // on-device OCR loads ~15 MB of reading data the first time
  expect: { timeout: 15_000 },
  workers: 1, // one shared database: the steps build on each other
  reporter: [["list"]],
  webServer: {
    command: "node e2e/serve-real.mjs",
    url: `http://localhost:${PORT}/api/health`,
    timeout: 400_000,
    reuseExistingServer: false,
  },
  use: { baseURL: `http://localhost:${PORT}`, browserName: "chromium", locale: "en-US", ...iphone },
});
