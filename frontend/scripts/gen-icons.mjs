// Rasterises the Holdwise mark (public/icons/icon.svg, icon-maskable.svg) into the PWA PNGs with Playwright's
// Chromium. Run after changing either SVG: node scripts/gen-icons.mjs
import { chromium } from "@playwright/test";
import { readFileSync } from "node:fs";

const jobs = [
  ["public/icons/icon.svg", "public/icons/icon-192.png", 192, true],
  ["public/icons/icon.svg", "public/icons/icon-512.png", 512, true],
  ["public/icons/icon-maskable.svg", "public/icons/icon-maskable-512.png", 512, false], // full bleed, art in safe zone
];
const browser = await chromium.launch();
for (const [src, out, size, transparent] of jobs) {
  const page = await browser.newPage({ viewport: { width: size, height: size } });
  const svg = readFileSync(src, "utf8").replace("<svg ", `<svg width="${size}" height="${size}" `);
  await page.setContent(`<body style="margin:0;background:transparent">${svg}</body>`);
  await page.screenshot({ path: out, omitBackground: transparent, clip: { x: 0, y: 0, width: size, height: size } });
  await page.close();
}
await browser.close();
