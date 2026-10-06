# Meitav Trade synthetic fixtures

`meitav_trade_cards.json` holds **invented** data (tickers, names and numbers are made up; no real holding or
account) in the layout described in `docs/import-formats.md`. It is plain JSON so the server-side parser
(`backend/app/importer/`) can be tested against the same file as the on-device parser
(`frontend/lib/ocr/meitav.ts`, test: `frontend/tests/meitav-layout.test.ts`).

## Shape

```
{ schema_version, invented_data, cost_rel_tol, number_abs_tol,
  cases: [ { id, description,
             images: { <variant>: [ <ocr text of screenshot 1>, <screenshot 2>, ... ] },
             expected: { layout, rows: [ <row>, ... ] } } ] }
```

* `images` holds the OCR text of the same card list in several variants. Every variant of a case must give the
  **same** `expected` rows:
  * `in_order`: lines in reading order, price either on the ticker line or on its own line.
  * `shuffled_columns`: the lines of each card (price, day change, name, P&L %, value) in other orders, value and
    P&L on one line or on separate lines, `$` before or after the number, an arrow next to the P&L %.
  * `hebrew_reversed`: right-to-left visual order: `ACME • NASDAQ` instead of `NASDAQ • ACME`, Hebrew words and
    word order reversed, `%` before the number (`%0.31-` for a negative one), the currency sign after the number.
* A multi-image case lists several screenshots; the result is the merge (shared cards counted once).
* Text before the first card (status bar, song title, app header) and after the last card (bottom navigation) is
  deliberately included and must be ignored. Everything before the first `exchange • ticker` is noise.
* `expected.rows[i]`: `name`, `symbol` (null for TASE funds), `tase_number`, `quantity` (null when uncertain),
  `price` (agorot for `TLV` rows), `value` (in the row's currency), `cost` (null without a P&L %), `currency`,
  `unit` (`USD`, `ILS` or `agorot`), and `flags`.
* `flags` uses the portable names: `quantity_uncertain` (value too small to infer a quantity from), `cost_inferred`,
  `duplicate_removed` (a card in two screenshots, counted once), `conflict` (same security in two screenshots with
  different price/value; one row kept, the later screenshot's). `quantity_fractional` (value / price is not a
  whole number) is also produced by the on-device parser but no fixture case needs it.
* Compare `name` with a Hebrew-reversal-tolerant check: a Hebrew-only token matches its reverse (`namesMatch` in
  `frontend/lib/ocr/hebrew.ts`). Names are hints; the ticker or security number is the identity.
* `cost` is compared with relative tolerance `cost_rel_tol` (0.06 %): it is derived from the rounded P&L %,
  `cost = price / (1 + pnl / 100)`, in the same unit as `price`. Other numbers are exact (`number_abs_tol`).
* Rows come back in order of first appearance across the screenshots, with `index` = position.

## The data

| id | exchange • ticker | notes |
|---|---|---|
| C1 `ACME` | NASDAQ | qty 12, price 257.49, value $3,089.88, P&L -8.37 % |
| C2 `SPYX` | NYSE | an ETF whose name equals its ticker, qty 50 |
| C3 `ZZZW` | NASDAQ | warrant worth $0.03: quantity cannot be inferred |
| C6 `QQQX` | NYSE | truncated name `…Invesco Example Tracking Trust`, **no P&L %** |
| C4 | TLV • 1234567 | fund, price 6,272 agorot, value ₪30,670.08, qty 489 |
| C5 | TLV • 7654321 | fund, price 1,085 agorot, value ₪5,425.00, qty 500 |

The section header `קרן סל` sits between the US cards and the TLV cards.

## Case `section_bars_and_simple_cards` (newer screen)

Invented data. Two US cards (English name line, day chip `0%`, a `$` value without decimals), a grey bar
`קרן סל`, two TASE fund cards whose Hebrew-with-Latin name (`77רדס.XTF`, digits inside) is **above**
`TLV • <number> מספר ני"ע`, a grey bar `אחר`, a simple card with no exchange line and no P&L % (`חיסכון ירוק 41`:
a name with a glued number, value = price, quantity 1) and a currency card with only the label `• 99041 מספר ני"ע`
(`$` value, quantity not whole, so `quantity_uncertain`). Bars and the bottom navigation words are not cards; a
card never takes a name, number or figure from its neighbour.

## Not covered

Real Tesseract output (merged or split lines beyond the variants above), a left column that is separated from
its cards entirely (all prices first, then all names), a value whose `$`/`₪` was lost by OCR, and non-Meitav
layouts. The tests in `meitav-layout.test.ts` cover a few of those edge cases inline.
