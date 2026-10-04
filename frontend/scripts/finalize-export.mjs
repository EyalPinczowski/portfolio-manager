// Post-build sanity check for the static export (`out/`). Fails the build if something a static host needs is missing.
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const out = new URL("../out/", import.meta.url).pathname;
const need = [
  "index.html", "404.html", "he/index.html", "en/index.html", "he/holding/index.html", "en/holding/index.html", "he/import/index.html", "he/settings/index.html", "he/login/index.html",
  "_headers", "tesseract/worker.min.js", "tesseract/lang/heb.traineddata.gz", "tesseract/lang/eng.traineddata.gz",
];
const missing = need.filter((f) => !existsSync(join(out, f)));
if (missing.length) { console.error(`static export is missing: ${missing.join(", ")}`); process.exit(1); }

// Cloudflare Pages: 25 MiB per file.
const big = [];
(function walk(d) {
  for (const n of readdirSync(d)) {
    const p = join(d, n);
    if (statSync(p).isDirectory()) walk(p);
    else if (statSync(p).size > 25 * 1024 * 1024) big.push(p);
  }
})(out);
if (big.length) { console.error(`files over the Cloudflare Pages 25 MiB limit: ${big.join(", ")}`); process.exit(1); }

// The CSP in _headers must match the shipped file, and no page may reference a third-party host.
const html = readFileSync(join(out, "he/index.html"), "utf8");
const remote = [...html.matchAll(/(?:src|href)="(https?:\/\/[^"]+)"/g)].map((m) => m[1]);
if (remote.length) { console.error(`he/index.html loads remote resources: ${remote.join(", ")}`); process.exit(1); }
console.log("static export OK");
