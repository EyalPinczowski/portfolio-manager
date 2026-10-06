import { describe, expect, it } from "vitest";
import fx from "./fixtures/ibi_cards.json";
import { detectLayout, parseScreenshotText } from "@/lib/ocr/layouts";
import { parseOcrText } from "@/lib/ocr/parse";
import { OCR_LAYOUTS } from "@/lib/config";

describe("ibi_cards layout, shared synthetic fixture", () => {
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

describe("ibi_cards layout, behaviours", () => {
  it("detects by a number next to `יחידות`, else stays on the old layouts", () => {
    expect(detectLayout("ACME 7 יחידות 41.25 -0.31% -$0.89")).toBe("ibi_cards");
    expect(detectLayout("ACME יחידות 7 41.25 -0.31% -$0.89")).toBe("ibi_cards");
    expect(detectLayout("IBB כמות 9 205.29 -0.33%")).toBe("hebrew_broker_cards");
    expect(detectLayout("שם נייר יחידות שער שווי\nטבע 1,000 6,500 65,000")).toBe("generic");
    expect(detectLayout("NASDAQ • ACME\nכמות 5")).toBe("meitav_trade");
  });

  it("keeps a leading number inside a Hebrew name, drops the units word, reads agorot", () => {
    const [r] = parseScreenshotText("תכ.תא90 50 יחידות 2,310.00 -0.40% -₪4.62").rows;
    expect(r.name).toBe("תכ.תא90");
    expect(r.name).not.toContain("יחידות");
    expect(r.quantity).toBe(50);
    expect(r.price).toBe(2310);
    expect([r.currency, r.unit]).toEqual(["ILS", "agorot"]);
  });

  it("reads fractional units and an ILS price that is not in agorot", () => {
    const [r] = parseScreenshotText("קרן דוגמה 6.47 יחידות 25.40 +1.00% +₪10.16").rows;
    expect(r.quantity).toBe(6.47);
    expect(r.unit).toBe("ILS");
  });

  it("generic lines still parse with the generic parser", () => {
    expect(parseOcrText("NVDA NVIDIA Corp 10 $120.50 $1,205.00")[0].quantity).toBe(10);
    expect(parseScreenshotText("NVDA NVIDIA Corp 10 $120.50 $1,205.00").layout).toBe("generic");
  });

  it("has a header fraction", () => {
    expect(OCR_LAYOUTS.ibi_cards.headerFraction).toBeGreaterThan(0);
  });
});
