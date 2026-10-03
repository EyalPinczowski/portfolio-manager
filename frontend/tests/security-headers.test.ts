import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { BASE_SECURITY_HEADERS, buildCsp } from "@/lib/security-headers";

const headersFile = readFileSync(path.resolve(__dirname, "../public/_headers"), "utf8");
const globalBlock = headersFile.split(/\n\s*\n/).find((b) => b.includes("\n/*\n") || b.trimStart().startsWith("/*") || /^\/\*$/m.test(b)) ?? "";
const parsed = Object.fromEntries(
  globalBlock.split("\n").filter((l) => /^\s+\S+:\s/.test(l)).map((l) => { const i = l.indexOf(":"); return [l.slice(0, i).trim().toLowerCase(), l.slice(i + 1).trim()]; }),
);

const caddyfile = readFileSync(path.resolve(__dirname, "../Caddyfile"), "utf8");

describe("security headers", () => {
  it("Caddyfile (Docker image) uses the same CSP", () => {
    expect(caddyfile).toContain(`Content-Security-Policy "${buildCsp()}"`);
  });
  it("public/_headers CSP equals the production CSP from lib/security-headers.ts", () => {
    expect(parsed["content-security-policy"]).toBe(buildCsp());
  });
  it("public/_headers carries every base security header", () => {
    for (const [k, v] of Object.entries(BASE_SECURITY_HEADERS)) expect(parsed[k.toLowerCase()], k).toBe(v);
  });
  it("CSP allows what on-device OCR needs and nothing remote", () => {
    const csp = buildCsp();
    expect(csp).toContain("worker-src 'self'");
    expect(csp).toContain("'wasm-unsafe-eval'");
    expect(csp).not.toContain("'unsafe-eval'");
    expect(csp).not.toMatch(/https?:\/\//);
    expect(csp).toContain("connect-src 'self'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("frame-ancestors 'none'");
  });
  it("dev CSP adds eval and websockets only for the dev server", () => {
    expect(buildCsp({ dev: true })).toContain("'unsafe-eval'");
    expect(buildCsp({ dev: true })).toContain("ws:");
    expect(buildCsp()).not.toContain("ws:");
  });
});
