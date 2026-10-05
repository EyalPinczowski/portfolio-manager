import { describe, expect, it } from "vitest";
import { detectTotal, flagTotalMismatch, parseOcrText } from "@/lib/ocr/parse";
import { lumaHistogram, otsu, shouldInvert, shouldUpscale } from "@/lib/ocr/preprocess";

const px = (v: number, n: number) => Array.from({ length: n }, () => [v, v, v, 255]).flat();

describe("ocr preprocess", () => {
  it("inverts dark screens only", () => {
    expect(shouldInvert(lumaHistogram(px(10, 50)))).toBe(true);
    expect(shouldInvert(lumaHistogram(px(240, 50)))).toBe(false);
  });
  it("upscales narrow images only", () => {
    expect(shouldUpscale(400, 800)).toBe(true);
    expect(shouldUpscale(1200, 800)).toBe(false);
  });
  it("otsu separates two tones", () => {
    const { threshold, separation } = otsu(lumaHistogram([...px(10, 50), ...px(240, 50)]));
    expect(threshold).toBeGreaterThanOrEqual(10);
    expect(threshold).toBeLessThan(240);
    expect(separation).toBeGreaterThan(0.9);
  });
});

describe("total cross-check", () => {
  it("detects the displayed total", () => {
    expect(detectTotal('סה"כ שווי תיק 1,000\nx')).toBe(1000);
    expect(detectTotal("Teva 10 20 200")).toBeNull();
  });
  it("flags rows that do not add up, never edits them", () => {
    const text = "Alpha Corp 10 20 200\nBeta Corp 10 30 300\nTotal 400";
    const rows = parseOcrText(text);
    expect(rows).toHaveLength(2);
    expect(rows.every((r) => r.flags.includes("total_mismatch"))).toBe(true);
    const ok = parseOcrText("Alpha Corp 10 20 200\nBeta Corp 10 30 300\nTotal 1,000");
    expect(ok.some((r) => r.flags.includes("total_mismatch"))).toBe(false);
    flagTotalMismatch([], 5);
  });
});
