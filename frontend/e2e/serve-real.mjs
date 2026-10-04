// Real-backend e2e server: builds the normal static export (no mock API) into e2e/.site-real, starts the API
// (backend/scripts/e2e_server.py: throwaway SQLite, fixed offline quotes) and serves both on one origin, the
// way Caddy does in production (/api/* is proxied, so the session cookie is first-party).
// It also answers the Turnstile siteverify call the API makes (TURNSTILE_VERIFY_URL points here): only the
// token E2E_TURNSTILE_TOKEN passes. Set E2E_REUSE=1 to reuse an existing e2e/.site-real.
import { createServer, request } from "node:http";
import { execSync, spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, renameSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const backend = join(root, "..", "backend");
const site = join(root, "e2e", ".site-real");
const out = join(root, "out");
const bak = join(root, "out.e2e-bak");
const port = Number(process.env.E2E_REAL_PORT ?? 3220);
const apiPort = Number(process.env.E2E_API_PORT ?? 8765);
export const TURNSTILE_TOKEN = "e2e-turnstile-ok";

if (!(process.env.E2E_REUSE === "1" && existsSync(site))) {
  rmSync(site, { recursive: true, force: true });
  const hadOut = existsSync(out);
  if (hadOut) { rmSync(bak, { recursive: true, force: true }); renameSync(out, bak); }
  try {
    const env = { ...process.env };
    delete env.NEXT_PUBLIC_API_MOCK;
    execSync("npm run build", { cwd: root, stdio: "inherit", env });
    renameSync(out, site);
  } finally {
    rmSync(out, { recursive: true, force: true });
    if (hadOut) renameSync(bak, out);
  }
}

const dbDir = mkdtempSync(join(tmpdir(), "pm-e2e-"));
const api = spawn("uv", ["run", "python", "scripts/e2e_server.py", "--port", String(apiPort)], {
  cwd: backend,
  stdio: "inherit",
  env: {
    ...process.env,
    DATABASE_URL: `sqlite:///${join(dbDir, "e2e.db")}`,
    ENV: "dev",
    COOKIE_SECURE: "false",
    SECRET_KEY: "e2e-secret-key-not-for-production-0123456789",
    MODEL_PROBE_ENABLED: "false",
    SCHEDULER_IN_PROCESS: "false",
    TURNSTILE_ENABLED: "true",
    TURNSTILE_SITE_KEY: "e2e-site-key",
    TURNSTILE_SECRET_KEY: "e2e-secret",
    TURNSTILE_VERIFY_URL: `http://127.0.0.1:${port}/__turnstile/siteverify`,
    TURNSTILE_ALLOWED_HOSTNAMES: '["localhost"]',
    E2E_ADMIN_EMAIL: process.env.E2E_ADMIN_EMAIL ?? "e2e@example.com",
    E2E_ADMIN_PASSWORD: process.env.E2E_ADMIN_PASSWORD ?? "e2e-long-password",
  },
});
const stop = () => { api.kill("SIGTERM"); rmSync(dbDir, { recursive: true, force: true }); };
process.on("exit", stop);
for (const sig of ["SIGINT", "SIGTERM"]) process.on(sig, () => { stop(); process.exit(0); });
api.on("exit", (code) => { if (code) { console.error(`e2e API exited with ${code}`); process.exit(1); } });

const TYPES = {
  ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".json": "application/json",
  ".svg": "image/svg+xml", ".png": "image/png", ".webmanifest": "application/manifest+json", ".txt": "text/plain",
  ".woff2": "font/woff2", ".ico": "image/x-icon", ".gz": "application/gzip", ".wasm": "application/wasm",
};

function proxy(req, res) {
  const up = request(
    { host: "127.0.0.1", port: apiPort, path: req.url, method: req.method, headers: { ...req.headers, host: `127.0.0.1:${apiPort}` } },
    (r) => { res.writeHead(r.statusCode ?? 502, r.headers); r.pipe(res); },
  );
  up.on("error", () => { if (!res.headersSent) res.writeHead(502); res.end(); });
  req.pipe(up);
}

function siteverify(req, res) {
  let body = "";
  req.on("data", (c) => { body += c; });
  req.on("end", () => {
    const token = new URLSearchParams(body).get("response") ?? (() => { try { return JSON.parse(body).response; } catch { return null; } })();
    const ok = token === TURNSTILE_TOKEN;
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify(ok ? { success: true, hostname: "localhost", action: "login" } : { success: false, "error-codes": ["invalid-input-response"] }));
  });
}

createServer((req, res) => {
  const path = decodeURIComponent((req.url ?? "/").split("?")[0]);
  if (path.startsWith("/api/")) return proxy(req, res);
  if (path === "/__turnstile/siteverify" && req.method === "POST") return siteverify(req, res);
  let file = normalize(join(site, path));
  if (!file.startsWith(site)) { res.writeHead(403).end(); return; }
  if (existsSync(file) && statSync(file).isDirectory()) file = join(file, "index.html");
  if (!existsSync(file)) { file = join(site, "404.html"); res.statusCode = 404; }
  res.setHeader("content-type", TYPES[extname(file)] ?? "application/octet-stream");
  res.end(readFileSync(file));
}).listen(port, () => console.log(`e2e real site on http://localhost:${port}`));
