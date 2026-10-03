"use client";
import { useState } from "react";
import { parseLocaleNumber } from "@/lib/number";

/**
 * Text input for a number that accepts "1,234", "1.234,5" etc. (see lib/number.ts). Keeps what the user typed,
 * reports the parsed value, and flags text it cannot read instead of silently turning it into 0.
 */
export function NumberCell({
  value, onValue, label, className = "",
}: { value: number | null | undefined; onValue: (n: number | null, valid: boolean) => void; label: string; className?: string }) {
  const [text, setText] = useState(value === null || value === undefined ? "" : String(value));
  const invalid = text.trim() !== "" && parseLocaleNumber(text) === null;
  return (
    <input
      aria-label={label}
      aria-invalid={invalid || undefined}
      className={`input ${invalid ? "border-red-600 dark:border-red-500" : ""} ${className}`}
      dir="ltr"
      inputMode="decimal"
      value={text}
      onChange={(e) => {
        const v = e.target.value;
        setText(v);
        const n = parseLocaleNumber(v);
        onValue(n, v.trim() === "" || n !== null);
      }}
    />
  );
}
