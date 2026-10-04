import { describe, expect, it } from "vitest";
import { parseLocaleNumber } from "@/lib/number";

describe("parseLocaleNumber", () => {
  it.each([
    ["1234", 1234],
    ["1,234", 1234],
    ["1,234.56", 1234.56],
    ["1.234,5", 1234.5],
    ["1.234,56", 1234.56],
    ["12,5", 12.5],
    ["1,234,567", 1234567],
    ["1.234.567", 1234567],
    ["1 234,5", 1234.5],
    ["1.5", 1.5],
    ["0.25", 0.25],
    [".5", 0.5],
    ["₪1,234.50", 1234.5],
    ["$ 1,205.00", 1205],
    ["-3.2", -3.2],
    ["(12.5)", -12.5],
    ["+7", 7],
  ])("parses %s as %s", (input, expected) => {
    expect(parseLocaleNumber(input)).toBe(expected);
  });
  it.each(["", "  ", "abc", "1,2,3.4.5", "--1", ".", ",", "12a", "1.2.3"])("rejects %j", (input) => {
    expect(parseLocaleNumber(input)).toBeNull();
  });
});
