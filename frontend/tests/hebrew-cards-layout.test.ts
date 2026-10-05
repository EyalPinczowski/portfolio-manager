import { describe, expect, it } from "vitest";
import fx from "./fixtures/hebrew_broker_cards.json";
import { detectLayout, parseScreenshotText } from "@/lib/ocr/layouts";
import { parseOcrText } from "@/lib/ocr/parse";
import { OCR_LAYOUTS } from "@/lib/config";

describe("hebrew_broker_cards layout, shared synthetic fixture", () => {
  for (const c of fx.cases) {
    for (const [variant, text] of Object.entries(c.images)) {
      it(`${c.id} / ${variant}`, () => {
        const parsed = parseScreenshotText(text);
        expect(parsed.layout).toBe(c.expected.layout);
        expect(parsed.rows).toHaveLength(c.expected.rows.length);
        c.expected.rows.forEach((e, i) => {
          const r = parsed.rows[i];
          expect(r.index).toBe(i);
          expect(r.name).toBe(e.name);
          expect(r.symbol).toBe(e.symbol);
          expect(r.quantity).toBe(e.quantity);
          expect(r.price).toBe(e.price);
          expect(r.value).toBeNull();
          expect(r.cost).toBeNull();
          expect(r.currency).toBe(e.currency);
          expect(r.unit).toBe(e.unit);
          expect(r.flags).toEqual(e.flags);
        });
      });
    }
  }
});

describe("hebrew_broker_cards layout, behaviours", () => {
  it("detects by `כמות` + number, else stays on the old layouts", () => {
    expect(detectLayout("IBB כמות 9 205.29 -0.33%")).toBe("hebrew_broker_cards");
    expect(detectLayout("שם נייר כמות שער שווי\nטבע 1,000 6,500 65,000")).toBe("generic");
    expect(detectLayout("NASDAQ • ACME\nכמות 5")).toBe("meitav_trade");
  });

  it("keeps a leading number inside a Hebrew name and strips `כמות` from the name", () => {
    const [r] = parseScreenshotText('35 מחקה ת"א MTF כמות 2,140 424.62 +0.34%').rows;
    expect(r.name).toBe('35 מחקה ת"א MTF');
    expect(r.name).not.toContain("כמות");
    expect(r.quantity).toBe(2140);
    expect(r.price).toBe(424.62);
  });

  it("generic lines still parse with the generic parser", () => {
    const rows = parseOcrText("NVDA NVIDIA Corp 10 $120.50 $1,205.00");
    expect(rows[0].quantity).toBe(10);
    expect(parseScreenshotText("NVDA NVIDIA Corp 10 $120.50 $1,205.00").layout).toBe("generic");
  });

  it("has a header fraction", () => {
    expect(OCR_LAYOUTS.hebrew_broker_cards.headerFraction).toBeGreaterThan(0);
  });
});
