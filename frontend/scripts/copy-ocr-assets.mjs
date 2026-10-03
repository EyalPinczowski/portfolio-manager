// Copies the tesseract.js worker, WASM core and Hebrew/English language data from node_modules into
// public/tesseract/ so on-device OCR never contacts a third-party host (CSP: connect-src 'self').
// Run by `postinstall` (lenient) and by `npm run build` (--strict).
import { cpSync, existsSync, mkdirSync, rmSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const nm = join(root, "node_modules");
const out = join(root, "public", "tesseract");
const strict = process.argv.includes("--strict");

// tesseract.js picks one of these at runtime (lstmOnly build; plain/SIMD/relaxed-SIMD by CPU feature).
const CORE = ["tesseract-core-lstm.wasm.js", "tesseract-core-simd-lstm.wasm.js", "tesseract-core-relaxedsimd-lstm.wasm.js"];
const LANGS = [
  ["eng", join(nm, "@tesseract.js-data", "eng", "4.0.0_best_int", "eng.traineddata.gz")],
  ["heb", join(nm, "@tesseract.js-data", "heb", "4.0.0_best_int", "heb.traineddata.gz")],
];
const jobs = [
  [join(nm, "tesseract.js", "dist", "worker.min.js"), join(out, "worker.min.js")],
  ...CORE.map((f) => [join(nm, "tesseract.js-core", f), join(out, "core", f)]),
  ...LANGS.map(([l, p]) => [p, join(out, "lang", `${l}.traineddata.gz`)]),
];

const missing = jobs.filter(([src]) => !existsSync(src)).map(([src]) => src);
if (missing.length > 0) {
  const msg = `copy-ocr-assets: missing ${missing.length} source file(s); run \`npm ci\` with dev dependencies.\n  ${missing.join("\n  ")}`;
  if (strict) { console.error(msg); process.exit(1); }
  console.warn(msg);
  process.exit(0);
}
rmSync(out, { recursive: true, force: true });
for (const [src, dst] of jobs) {
  mkdirSync(dirname(dst), { recursive: true });
  cpSync(src, dst);
}
const mb = jobs.reduce((a, [, d]) => a + statSync(d).size, 0) / 1e6;
console.log(`copy-ocr-assets: ${jobs.length} files, ${mb.toFixed(1)} MB -> public/tesseract/`);
