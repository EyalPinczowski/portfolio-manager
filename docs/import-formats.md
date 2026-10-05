# Broker screenshot formats

Findings from real screenshots supplied by the user on 2026-10-03. **The screenshots and the holdings in them are not stored in the repository** (personal financial data). This file describes only the *layout*; tests use synthetic fixtures with invented numbers in the same layout.

## Meitav Trade (מיטב טרייד) mobile app, "My portfolio" screen (Hebrew, right-to-left)
Three screenshots of one scrolling list. Each holding is one card. The layout of a card:

| Position in the card (RTL) | Content | Example shape |
|---|---|---|
| top right | market-closed icon, then **exchange • ticker** | `NASDAQ • CEG`, `NYSE • SPY`; TASE funds show `TLV • <7-digit security number>` |
| second line, right | company or fund name (truncated with `…` on the **left** for long names) | `Constellation Energy Corp`, `…Energy Select Sector Spdr F`; Hebrew for TASE funds |
| third line, right | **position value** with a briefcase icon, preceded by a **total P&L %** with an up/down arrow | `$3,089.88`, `₪30,670.08`, `-8.37% ↓` |
| left column, top | **price** per unit | `257.49`; TASE funds in **agorot** (`6,272` = ₪62.72) |
| left column, below | **day change %** (coloured red/green; `0%` when the market is closed) | `-0.55%` |
| section header row | a grey full-width row with a section name; the Hebrew `קרן סל` means ETF/tracker fund | separates US holdings from TASE funds |

Other things on the screen: a status bar (time, battery, a media-player notification with a song title) and the app header at the top (about the top 14 % of the image), and a bottom navigation bar that **overlaps the last visible card**.

### What this means for the parser
1. **There is no quantity column.** Quantity must be inferred: `quantity = value / price` (with the price converted from agorot to the value's currency for TASE funds). On the supplied data **every row with a value of at least $1 gave an exact whole number** (including the TASE funds once the price is read as agorot), so the inference is reliable. Rows whose value is tiny (for example warrants worth a few cents, shown rounded to cents) give a meaningless quantity; flag them `quantity_uncertain` and ask the user to enter the quantity.
2. **There is no cost column, but total P&L % is shown.** Average cost can be derived: `cost_per_unit = price / (1 + pnl%/100)`. Rounding of the shown percentage gives about ±0.01 % error. Mark it `cost_inferred` so the UI says "estimated from the broker's P&L %". Rows without a P&L % have no cost (P&L shows "—").
3. **Tickers are explicit** (exchange + ticker), and names are truncated and unreliable. Match on the ticker first; use the name only as a hint. The ticker is not in our seed list for many US holdings (small caps, ETFs, warrants). A ticker that matches `^[A-Z]{1,5}$` with the exchange `NASDAQ`/`NYSE`/`AMEX` must be accepted as a **user-scoped, unverified `Security`** and verified against the quote provider (a successful quote sets `verified`), instead of blocking the import as "unmatched".
4. **TASE funds are identified by security number** (`TLV • 1159714`), with Hebrew names. Match on `tase_number` first. If the number is not in the seed, accept it as a user-scoped holding with a manual symbol field, and show the name. Price is in **agorot**, value in ILS.
5. **Column order differs from the parser's assumptions.** The current backend parser (`app/importer/parse.py`) and its TypeScript port read these lines wrongly (probe on 2026-10-03): `NASDAQ • CEG … 257.49 -0.55% -8.37% $3,089.88` gives quantity = 257.49 (the price), price = none; `TLV • 1159714 125 MTF 6,272 …` gives quantity = 1159714 (the security number), symbol `TLV`. A dedicated layout (`meitav_trade`) is needed, detected by the `exchange • ticker` pattern and the `קרן סל` section header.
6. **Several screenshots overlap** (the last card of one image is the first card of the next). The importer sums quantities of duplicate rows today (`merge_duplicate_rows`), which would **double-count** an overlapping card. For multi-screenshot imports, identical rows (same symbol, price and value) must be **de-duplicated, not summed**; the same symbol with different values is flagged for the user.
7. **Redaction:** the existing header blur (top 12 %) is slightly too small for this app: the header spans roughly the top 14 %. Make the header fraction a per-layout setting (14 % for this layout) and keep the 6+ digit-run blur, but do **not** blur the 7-digit TASE security numbers inside a card (they are needed for matching and are not account numbers); detect them by the `TLV •` prefix.
8. **Hebrew OCR:** right-to-left lines come out of Tesseract in visual order; Hebrew names can be reversed ("125כשתא" for "תא125"). Matching must normalise by comparing both the string and its reverse for Hebrew-only tokens.

### Acceptance tests (synthetic fixtures, same layout, invented numbers)
- A card list with US tickers, a warrant row with a tiny value, a TASE fund with agorot price, and a section header: quantities inferred exactly, the warrant flagged `quantity_uncertain`, the TASE fund priced in agorot and valued in ILS.
- Costs inferred from P&L % within 0.05 %; a row without P&L % has no cost.
- Two overlapping screenshots (shared last/first card) import without double counting.
- An unseen US ticker is accepted as unverified and verified by a (mocked) quote; an unknown 7-digit TASE number is accepted with a manual symbol.
- The server and the on-device (TypeScript) parser agree on the same fixtures.

## Second Hebrew broker app, dark theme, "תיק אישי" holdings list (received 2026-10-05)
Screenshot not kept (security rule); only the layout is recorded here, with invented numbers.

- **Header card:** portfolio name with a dropdown (`תיק עדכני`), total in ₪, daily change (amount + %), total change (amount + %), and `כח קניה` (buying power, ₪). Tabs: `אחזקות` (holdings), `פילוח תיק`, `יתרות במטבע`.
- **Holdings list (`האחזקות שלי`):** one card per holding. Right side: symbol or Hebrew fund name, then `כמות N` (quantity). Left side: the **last price** and a coloured daily-change chip. There is **no per-row value, cost or currency** on this screen.
- **Mixed markets in one list:** US ETFs (`IBB`, `ICLN`, priced in USD, no currency sign) next to a TASE fund (`35 מחקה ת"א MTF`, price 424.62, almost certainly ILS/agorot-adjusted). The currency has to come from the symbol or the user, not from the screen.
- **Hebrew names contain digits:** `35 מחקה ת"א MTF` starts with a number that is part of the name (the TA-35), not a quantity.
- **Footer notice:** `נתוני ת"א בהשהייה של 15 דקות` (TASE data delayed 15 minutes), a bottom tab bar, and a sort/filter row (`מיון`, `סוג נייר ערך`).

### What the current parser does with it (probe 2026-10-05, text as OCR would give it)
1. One-line form `IBB כמות 9 205.29 -0.33%`: the price lands in `value`, `price` is empty, and the name becomes `IBB כמות` (the word `כמות` leaks into it).
2. Currency defaults to ILS for the US ETFs.
3. `35 מחקה ת"א MTF כמות 2,140 424.62 +0.34%`: quantity = 35 (the name's number), price = 2,140 (the real quantity), value = 424.62.
4. Stacked form (price, %, name, quantity on separate lines) returns no rows.

### What a dedicated layout needs (`hebrew_broker_cards`)
Detect `כמות` followed by a number; take quantity from it, the nearest decimal number as price; strip `כמות` from the name; never treat a leading number inside a Hebrew name as a quantity; leave currency to the symbol lookup (US ticker → USD, `MTF` / Hebrew fund → ILS) and ask the user to confirm it. Needs a synthetic fixture and tests before it is built, and the user's OK to start.
