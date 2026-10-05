import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const sw = readFileSync(join(__dirname, "../public/sw.js"), "utf8");

describe("public/sw.js", () => {
  it("only caches successful responses", () => {
    expect(sw).toMatch(/if \(res\.ok\)[\s\S]*c\.put\(req, copy\)/);
  });
  it("has a per-build cache name placeholder (replaced by scripts/finalize-export.mjs) and prunes old caches", () => {
    expect(sw).toContain('"pm-shell-__BUILD__"');
    expect(sw).toMatch(/k !== CACHE[\s\S]*caches\.delete/);
  });
});
