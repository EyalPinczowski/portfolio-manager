import { describe, expect, it } from "vitest";
import fx from "./fixtures/meitav/meitav_trade_cards.json";
import { detectLayout, mergeScreenshots, parseScreenshotText } from "@/lib/ocr/layouts";
import { findAnchors } from "@/lib/ocr/meitav";
import { scrubIdentifiers } from "@/lib/ocr/parse";
import { namesMatch, tokensMatch } from "@/lib/ocr/hebrew";
import { flagsOf } from "@/lib/ocr/types";
import { headerFractionFor } from "@/lib/ocr/layouts";
import { OCR_LAYOUTS } from "@/lib/config";

interface ExpRow {
  name: string; symbol: string | null; tase_number: string | null; quantity: number | null; price: number | null;
  value: number | null; cost: number | null; currency: string; unit: string; flags: string[];
}
interface Case { id: string; images: Record<string, string[]>; expected: { layout: string; rows: ExpRow[] } }

const near = (a: number | null | undefined, b: number | null, rel: number) => {
  if (b === null) return expect(a ?? null).toBeNull();
  expect(a).not.toBeNull();
  expect(Math.abs((a as number) - b)).toBeLessThanOrEqual(Math.abs(b) * rel + fx.number_abs_tol);
};

describe("Meitav Trade layout, shared synthetic fixtures", () => {
  for (const c of fx.cases as Case[]) {
    for (const [variant, images] of Object.entries(c.images)) {
      it(`${c.id} / ${variant}`, () => {
        const merged = mergeScreenshots(images.map((t) => parseScreenshotText(t)));
        expect(merged.layout).toBe(c.expected.layout);
        expect(merged.rows).toHaveLength(c.expected.rows.length);
        c.expected.rows.forEach((e, i) => {
          const r = merged.rows[i];
          const where = `${c.id}/${variant} row ${i} (${e.symbol ?? e.tase_number})`;
          expect(r.index, where).toBe(i);
          expect(r.symbol, where).toBe(e.symbol);
          expect(r.tase_number, where).toBe(e.tase_number);
          expect(r.currency, where).toBe(e.currency);
          expect(r.unit, where).toBe(e.unit);
          near(r.quantity, e.quantity, 0);
          near(r.price, e.price, 1e-9);
          near(r.value, e.value, 1e-9);
          near(r.cost, e.cost, fx.cost_rel_tol);
          expect(flagsOf(merged.meta[i]).sort(), where).toEqual([...e.flags].sort());
          expect(namesMatch(r.name, e.name), `${where}: "${r.name}" vs "${e.name}"`).toBe(true);
          expect(r.flags).toEqual([]); // nothing client-only leaks into the server contract
        });
      });
    }
  }
});

describe("Meitav Trade layout, behaviours", () => {
  const card = (lines: string[]) => lines.join("\n");

  it("detects the layout by the exchange bullet ticker pattern, else stays generic", () => {
    expect(detectLayout("NASDAQ • ACME\nAcme\n$100.00")).toBe("meitav_trade");
    expect(detectLayout("ACME • NASDAQ\n$100.00")).toBe("meitav_trade");
    expect(detectLayout("NYSE ABC\nNASDAQ XYZ")).toBe("meitav_trade");
    expect(detectLayout("NVDA NVIDIA Corp 10 $120.50 $1,205.00")).toBe("generic");
    expect(detectLayout("קרן סל\nטבע 1,000 6,500 65,000")).toBe("generic");
    const generic = parseScreenshotText("NVDA NVIDIA Corp 10 $120.50 $1,205.00");
    expect(generic.layout).toBe("generic");
    expect(generic.rows[0]).toMatchObject({ symbol: "NVDA", quantity: 10 });
  });

  it("matches anchors for US tickers with dots and TLV numbers, and never for lowercase words", () => {
    expect(findAnchors("NYSE • BRK.B")[0]).toMatchObject({ exchange: "NYSE", ticker: "BRK.B" });
    expect(findAnchors("TLV • 1159714")[0]).toMatchObject({ exchange: "TLV", ticker: "1159714" });
    expect(findAnchors("TLV • ABC")).toHaveLength(0);
    expect(findAnchors("NASDAQ • 1234567")).toHaveLength(0);
    expect(findAnchors("NASDAQ • Constellation")).toHaveLength(0);
  });

  it("never takes the 7-digit TASE number as a quantity or a price", () => {
    const { rows } = parseScreenshotText(card(["TLV • 1159714 6,272", "מחקה דמה", "-0.31%", "₪30,670.08"]));
    expect(rows[0]).toMatchObject({ tase_number: "1159714", symbol: null, price: 6272, value: 30670.08, quantity: 489, unit: "agorot", currency: "ILS" });
    expect(rows[0].name).not.toMatch(/\d{5,}/);
  });

  it("infers exact quantities from value / price, also with a price in agorot", () => {
    const { rows, meta } = parseScreenshotText(card(["NASDAQ • ACME", "257.49", "Acme Corp", "-0.55%", "-8.37% ↓ $3,089.88"]));
    expect(rows[0]).toMatchObject({ quantity: 12, price: 257.49, value: 3089.88, unit: "USD" });
    expect(meta[0].cost_inferred).toBe(true);
    expect(rows[0].cost).toBeCloseTo(281, 0);
  });

  it("marks a row worth cents as quantity_uncertain and leaves the quantity empty", () => {
    const { rows, meta } = parseScreenshotText(card(["NASDAQ • ZZZW 0.0107", "Warrant", "-3.10%", "-91.20% ↓ $0.03"]));
    expect(rows[0].quantity).toBeNull();
    expect(meta[0].quantity_uncertain).toBe(true);
    expect(rows[0].value).toBe(0.03);
  });

  it("no P&L % means no cost; a lone day change is never read as P&L", () => {
    const { rows, meta } = parseScreenshotText(card(["NYSE • QQQX", "98.10", "Invesco Trust", "0.12%", "$1,962.00"]));
    expect(rows[0].cost).toBeNull();
    expect(meta[0].cost_inferred).toBeUndefined();
    expect(rows[0].quantity).toBe(20);
  });

  it("leaves cost empty when the P&L % cannot be told from the day change", () => {
    const { rows } = parseScreenshotText(card(["NYSE • QQQX", "98.10 $1,962.00", "1.5%", "2.5%", "Invesco Trust"]));
    expect(rows[0].cost).toBeNull();
    expect(rows[0].quantity).toBe(20);
  });

  it("drops the leading ellipsis and the section header from names, keeps the ticker as symbol", () => {
    const { rows } = parseScreenshotText(card(["NYSE • XLEX 50.00", "…Energy Select Sector Spdr F", "-0.10%", "+5.00% ↑ $500.00", "קרן סל", "TLV • 1111111 100", "קרן", "-0.1%", "₪10.00"]));
    expect(rows[0].name).toBe("Energy Select Sector Spdr F");
    expect(rows[0].symbol).toBe("XLEX");
    expect(rows[1].tase_number).toBe("1111111");
  });

  it("handles a card missing its value without inventing numbers", () => {
    const { rows, meta } = parseScreenshotText(card(["NASDAQ • ACME", "Acme Corp", "257.49", "-0.55%"]));
    expect(rows[0].value).toBeNull();
    expect(rows[0].quantity).toBeNull();
    expect(meta[0].quantity_uncertain).toBe(true);
  });

  it.each(["–", "—", "‐", "‑", "−"])("a %s glued to the P&L percent is a minus (cost = price / (1 - 8.37%))", (dash) => {
    const { rows, meta } = parseScreenshotText(card(["NASDAQ • ACME", "257.49", "Acme Corp", "-0.55%", `${dash}8.37% ↓ $3,089.88`]));
    expect(meta[0].pnl_pct).toBe(-8.37);
    expect(rows[0].cost).toBeCloseTo(257.49 / (1 - 0.0837), 3);
  });

  it("flags a fractional quantity instead of rounding it silently", () => {
    const { rows, meta } = parseScreenshotText(card(["NASDAQ • ACME 100.00", "Acme Corp", "-0.5%", "-1.0% ↓ $250.00"]));
    expect(rows[0].quantity).toBe(2.5);
    expect(meta[0].quantity_fractional).toBe(true);
  });

  it("sums nothing when two screenshots share a card, and flags different values", () => {
    const a = parseScreenshotText("NYSE • AAAA 10.00\nAaaa\n0%\n$100.00");
    const same = parseScreenshotText("NYSE • AAAA 10.00\nAaaa\n0%\n$100.00");
    const m = mergeScreenshots([a, same]);
    expect(m.rows).toHaveLength(1);
    expect(m.rows[0].quantity).toBe(10);
    expect(flagsOf(m.meta[0])).toEqual(["duplicate_removed"]);
    const diff = parseScreenshotText("NYSE • AAAA 12.00\nAaaa\n0%\n$120.00");
    const c = mergeScreenshots([a, diff]);
    expect(c.rows).toHaveLength(1);
    expect(flagsOf(c.meta[0])).toContain("conflict");
    expect(c.meta[0].conflict).toEqual({ price: 10, value: 100 });
  });

  it("uses a 14 % header for this layout and 12 % otherwise", () => {
    expect(OCR_LAYOUTS.meitav_trade.headerFraction).toBe(0.14);
    expect(headerFractionFor("meitav_trade")).toBe(0.14);
    expect(headerFractionFor("auto")).toBe(0.12);
  });

  it("does not blur the 7-digit security number on TLV lines but still drops account digit runs", () => {
    const out = scrubIdentifiers("TLV • 1159714 6,272\n1159714 • TLV\nחשבון 123456789\n12345678901 NYSE • ABC");
    expect(out).toContain("TLV • 1159714");
    expect(out).toContain("1159714 • TLV");
    expect(out).not.toContain("חשבון");
    expect(out).not.toContain("12345678901");
  });

  it("compares Hebrew-only tokens with their reverse", () => {
    expect(tokensMatch("תא", "אמ")).toBe(false);
    expect(tokensMatch("מדד", "דדמ")).toBe(true);
    expect(tokensMatch("תא125", "125אמ")).toBe(false);
    expect(tokensMatch("ACME", "EMCA")).toBe(false);
    expect(namesMatch("מחקה מדד 125", "125 דדמ הקחמ")).toBe(true);
    expect(namesMatch("Acme Corp", "Acme Corp")).toBe(true);
    expect(namesMatch("", "")).toBe(false);
  });

  describe("cards never borrow from a neighbour (section bars, simple cards, number labels)", () => {
    const text = card([
      "קרן סל", "4,125 77רדס.XTF", "+0.11%", 'TLV • 1180422 מספר ני"ע', "12.80% ↑ ₪24,750.00",
      "אחר", "418.3", "חיסכון ירוק 41", "₪418.3", "ראשי",
    ]);

    it("keeps a TASE number with the name above it and ignores bars and navigation", () => {
      const { rows } = parseScreenshotText(text);
      expect(rows).toHaveLength(2);
      expect(rows[0]).toMatchObject({ tase_number: "1180422", symbol: null, price: 4125, value: 24750, quantity: 600 });
      expect(namesMatch(rows[0].name, "77רדס.XTF")).toBe(true);
      expect(rows[1]).toMatchObject({ symbol: null, tase_number: null, price: 418.3, value: 418.3, quantity: 1, cost: null, name: "חיסכון ירוק 41" });
    });

    it("flags a simple card whose quantity cannot be inferred", () => {
      const lines = text.split("\n").slice(0, 5);
      const { rows, meta } = parseScreenshotText(card([...lines, "אחר", "חיסכון ירוק 41", "281.3", "₪2,261.17"]));
      expect(rows).toHaveLength(2);
      expect(rows[0].tase_number).toBe("1180422");
      expect(rows[1].quantity).toBeNull();
      expect(flagsOf(meta[1])).toContain("quantity_uncertain");
    });

    it("makes no card from the status bar and header", () => {
      const { rows } = parseScreenshotText(card(["11:41", "מיטב:טרייד", "NYSE • VNTQ", "84.15", "$1,683.00"]));
      expect(rows).toHaveLength(1);
      expect(rows[0].symbol).toBe("VNTQ");
    });
  });
});
