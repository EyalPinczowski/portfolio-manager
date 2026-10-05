import { describe, expect, it } from "vitest";
import { parseLine, parseOcrText, scrubIdentifiers } from "@/lib/ocr/parse";
import parity from "./fixtures/ocr-parity.json";
import { fitSize, topMaskHeight } from "@/lib/ocr/image";

describe("on-device OCR parsing (port of backend/app/importer/parse.py)", () => {
  it("Hebrew agorot table: unit, thousands separators, cost column", () => {
    const rows = parseOcrText(`שם נייר   כמות   שער (אג')   שווי   עלות
טבע   1,000   6,500   65,000   5,800
לאומי  500  3,200  16,000
בנק הפועלים 200 3,450 6,900
נייס 10 8,200 820
`);
    expect(rows.map((r) => r.name)).toEqual(["טבע", "לאומי", "בנק הפועלים", "נייס"]);
    const [teva, leumi] = rows;
    expect(teva).toMatchObject({ unit: "agorot", currency: "ILS", quantity: 1000, price: 6500, value: 65000, cost: 5800 });
    expect(leumi.cost).toBeNull();
    expect(rows.every((r) => r.unit === "agorot")).toBe(true);
    expect(rows.map((r) => r.index)).toEqual([0, 1, 2, 3]);
  });

  it("English USD rows: symbol detection, dollar signs, decimals", () => {
    const [nv, aapl] = parseOcrText(`Symbol Quantity Price Value
NVDA NVIDIA Corp 10 $120.50 $1,205.00
AAPL 5 $190.20 $951.00
`);
    expect(nv).toMatchObject({ symbol: "NVDA", currency: "USD", unit: "USD", quantity: 10, price: 120.5, value: 1205 });
    expect(aapl.symbol).toBe("AAPL");
  });

  it("finds a TASE security number and tolerates reordered (right-to-left) columns", () => {
    const [r] = parseOcrText("טבע 629014 65,000 6,500 1,000 ₪ אג'");
    expect(r.tase_number).toBe("629014");
    expect(r.value).toBe(65000);
    expect(r.unit).toBe("agorot");
    expect(new Set([r.quantity, r.price])).toEqual(new Set([1000, 6500]));
  });

  it("a shekel row on a page whose header says agorot falls back to ILS for that row", () => {
    const rows = parseOcrText("מחיר באגורות\nטבע 1,000 6,500 65,000\nאלביט 10 1,200 12,000\n");
    expect(rows.map((r) => r.unit)).toEqual(["agorot", "ILS"]);
  });

  it("shekel row with thousands separators and a cost column", () => {
    const r = parseLine("אלביט מערכות 25 ₪1,480.00 ₪37,000.00 1,210", false)!;
    expect(r).toMatchObject({ name: "אלביט מערכות", unit: "ILS", currency: "ILS", quantity: 25, price: 1480, value: 37000, cost: 1210 });
  });

  it("ignores header lines, lines with too few numbers, and lines without letters", () => {
    expect(parseOcrText("שם נייר כמות שער שווי\n12345 67890\nטבע 5\n")).toEqual([]);
  });

  it("matches the Python parser on shared fixtures (parity)", () => {
    for (const [name, c] of Object.entries(parity as Record<string, { text: string; rows: Record<string, unknown>[] }>)) {
      const got = parseOcrText(c.text).map(({ index, name: n, symbol, tase_number, quantity, price, value, cost, currency, unit }) => ({ index, name: n, symbol, tase_number, quantity, price, value, cost, currency, unit }));
      const want = c.rows.map(({ index, name: n, symbol, tase_number, quantity, price, value, cost, currency, unit }) => ({ index, name: n, symbol, tase_number, quantity, price, value, cost, currency, unit }));
      expect(got, name).toEqual(want);
    }
  });
});

describe("privacy scrubbing of OCR text", () => {
  it("drops account lines and 9+ digit runs but keeps 6-8 digit TASE numbers", () => {
    const text = "תיק השקעות - חשבון 1234567890\nטבע 629014 1,000 65 65,000\nטל 0501234567 NVDA 10 $120.50 $1,205.00\n";
    const scrubbed = scrubIdentifiers(text);
    expect(scrubbed).not.toMatch(/1234567890|0501234567/);
    expect(scrubbed).toContain("629014");
    const rows = parseOcrText(scrubbed);
    expect(rows.map((r) => r.tase_number)).toEqual(["629014", null]);
    expect(JSON.stringify(rows)).not.toMatch(/\d{9,}/);
  });
  it("drops English account lines", () => {
    expect(scrubIdentifiers("Account 99887766\nAAPL 5 $190.20 $951.00")).toBe("AAPL 5 $190.20 $951.00");
  });
});

describe("canvas geometry", () => {
  it("masks the top 12% and downsizes only oversized images", () => {
    expect(topMaskHeight(2400)).toBe(288);
    expect(topMaskHeight(0)).toBe(0);
    expect(fitSize(1080, 2400, 2600)).toEqual({ width: 1080, height: 2400 });
    expect(fitSize(1000, 5200, 2600)).toEqual({ width: 500, height: 2600 });
  });
});

describe("generic parser: cost plausibility", () => {
  it("a P&L-like leftover number is not a cost", () => {
    const r = parseLine("AAPL 10 150.00 1,500.00 2.30", false)!;
    expect(r).toMatchObject({ quantity: 10, price: 150, value: 1500, cost: null });
    expect(r.flags).not.toContain("cost_inferred");
  });
  it("a total-cost column (far above the price) is dropped", () => {
    const r = parseLine("AAPL 10 150.00 1,500.00 12,000.00", false)!;
    expect(r.cost).toBeNull();
  });
  it("a plausible per-unit cost is kept and marked cost_inferred", () => {
    const r = parseLine("AAPL 10 150.00 1,500.00 120.00", false)!;
    expect(r.cost).toBe(120);
    expect(r.flags).toContain("cost_inferred");
  });
});
