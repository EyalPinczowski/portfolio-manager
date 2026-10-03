import { describe, expect, it } from "vitest";
import en from "@/messages/en.json";
import he from "@/messages/he.json";

function keys(o: unknown, prefix = ""): string[] {
  if (o === null || typeof o !== "object") return [prefix];
  return Object.entries(o as Record<string, unknown>).flatMap(([k, v]) => keys(v, prefix ? `${prefix}.${k}` : k));
}

describe("i18n messages", () => {
  it("he and en have identical keys", () => {
    expect(keys(he).sort()).toEqual(keys(en).sort());
  });
  it("has no empty strings", () => {
    for (const m of [en, he]) {
      const flat = (o: unknown): string[] => (typeof o === "string" ? [o] : Object.values(o as object).flatMap(flat));
      expect(flat(m).every((s) => s.trim().length > 0)).toBe(true);
    }
  });
  it("labels the since-start tile with the start date placeholder", () => {
    expect(en.header.sinceStart).toBe("Since you started using the app ({date})");
    expect(he.header.sinceStart).toBe("מאז שהתחלת להשתמש באפליקציה ({date})");
    expect(en.pnl.sinceStartChart).toContain("{date}");
    expect(he.pnl.sinceStartChart).toContain("{date}");
  });
});
