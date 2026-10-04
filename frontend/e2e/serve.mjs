// Builds the static export with the in-browser mock API (NEXT_PUBLIC_API_MOCK=1) into e2e/.site and serves it.
// The normal `out/` (real-API export) is moved aside during the build and restored afterwards.
// Set E2E_REUSE=1 to reuse an existing e2e/.site. No backend is needed.
import { createServer } from "node:http";
import { execSync } from "node:child_process";
import { existsSync, readFileSync, renameSync, rmSync, statSync } from "node:fs";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const site = join(root, "e2e", ".site");
const out = join(root, "out");
const bak = join(root, "out.e2e-bak");

if (!(process.env.E2E_REUSE === "1" && existsSync(site))) {
  rmSync(site, { recursive: true, force: true });
  const hadOut = existsSync(out);
  if (hadOut) { rmSync(bak, { recursive: true, force: true }); renameSync(out, bak); }
  try {
    execSync("npx next build", { cwd: root, stdio: "inherit", env: { ...process.env, NEXT_PUBLIC_API_MOCK: "1" } });
    renameSync(out, site);
  } finally {
    rmSync(out, { recursive: true, force: true });
    if (hadOut) renameSync(bak, out);
  }
}

const TYPES = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".json": "application/json",
  ".svg": "image/svg+xml", ".png": "image/png", ".webmanifest": "application/manifest+json", ".txt": "text/plain",
  ".woff2": "font/woff2", ".ico": "image/x-icon", ".gz": "application/gzip",
};
const port = Number(process.env.E2E_PORT ?? 3210);
createServer((req, res) => {
  const path = decodeURIComponent((req.url ?? "/").split("?")[0]);
  let file = normalize(join(site, path));
  if (!file.startsWith(site)) { res.writeHead(403).end(); return; }
  if (existsSync(file) && statSync(file).isDirectory()) file = join(file, "index.html");
  if (!existsSync(file)) { file = join(site, "404.html"); res.statusCode = 404; }
  res.setHeader("content-type", TYPES[extname(file)] ?? "application/octet-stream");
  res.end(readFileSync(file));
}).listen(port, () => console.log(`e2e site on http://localhost:${port}`));
