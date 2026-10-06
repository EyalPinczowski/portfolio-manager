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

### Newer layout features (Update 10, 2026-10-06; invented fixture `section_bars_and_simple_cards`)
- **Grey section bars** between groups (`קרן סל`, `אחר`) and the **bottom navigation** (`ראשי`, `התיק שלי`, `מסחר`, `ניירות במעקב`, `הוראות`) are not cards and never names; a bar or the navigation ends the card above it.
- **TASE fund cards put the name above the number**: name (Hebrew with Latin, digits inside it, e.g. `77רדס.XTF`), then `TLV • 1180422 מספר ני"ע`, then `12.80% ↑ ₪24,750.00`; price (agorot) on the left, day chip `+0.11%` or `0%`. The label `מספר ני"ע` makes the anchor "labeled": the nearest name line above it (plus the figures between, and up to two price lines above the name) belongs to its card. Digits glued to letters are never a price.
- **US cards** keep `NYSE • VNTQ` first, then the English name, `9.30% ↑ $1,683.00` and the left price with a `0%` chip.
- **Cards with no exchange line** (a plain name with a number glued to it, `₪418.3`, value = price, no P&L %; a currency card `$2,261.17` with only `מספר ני"ע • 99041`): parsed as simple cards, never merged into a neighbour. The card starts at a name line (text, no amount) once the card before has its amount; with no whole `value / price` the quantity is left empty and flagged `quantity_uncertain`. A stretch without any `$`/`₪` amount (status bar, header) gives no card.
- A figure-only line wins as the price over a number glued to a name.

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
Detect `כמות` followed by a number; take quantity from it, the nearest decimal number as price; strip `כמות` from the name; never treat a leading number inside a Hebrew name as a quantity; leave currency to the symbol lookup (US ticker → USD, `MTF` / Hebrew fund → ILS) and ask the user to confirm it. 
Status (2026-10-05): **built** as `hebrew_broker_cards` (`backend/app/importer/hebrew_cards.py`, port `frontend/lib/ocr/hebrewCards.ts`, shared fixture `frontend/tests/fixtures/hebrew_broker_cards.json`, tests on both sides). Handles the one-line and the stacked (price, chip, name, `כמות N`) forms. Value and cost stay null; every row carries `currency_changed`, so the user must confirm the currency before saving. A card with `כמות N` above the price and name is not supported.

## IBI (`ibi_cards`, "תיק ההשקעות שלי")

Status (2026-10-06): **built** as `ibi_cards` (`backend/app/importer/ibi.py`, port `frontend/lib/ocr/ibi.ts`, shared fixture `frontend/tests/fixtures/ibi_cards.json`, tests on both sides). The numbers below are invented.

- **Layout:** dark theme, one card per holding. Right side: the symbol (`ABCD`) or a Hebrew name with a small flag, then `N יחידות` (units; fractions such as `6.47` occur). Left side: the last price (`41.35`), a coloured day-change chip (`+0.80%`) and the day's profit or loss with a currency sign (`+$3.97`, `-₪4.62`). There is no total value, cost or currency column. The title carries an account number, so the header band is blurred before OCR (default fraction) and no digit run from it is kept.
- **Hebrew names contain digits:** `תכ.תא90` (a TA index tracker). The number is part of the name, never the quantity.
- **TASE prices are in agorot:** 50 units at `2,310.00` with a day amount of `-₪4.62` and a chip of `-0.40%` fits `50 x 23.10 x 0.40%`, not `50 x 2,310 x 0.40%`.
- **Detection:** a number next to `יחידות` (`10 יחידות`, or `יחידות 10` when OCR reverses it). Each such line anchors one card; the card is the few lines around it (one line, or stacked in any order).
- **Rules:** quantity = the number next to `יחידות`; price = the nearest decimal that is not a signed amount; `$` on the day amount = USD; `₪` = ILS, with unit `agorot` when the amount fits `quantity x price/100 x chip` better than `quantity x price x chip`, else `ILS`; no amount sign = guess from the name (ticker = USD, Hebrew name = ILS). Value and cost stay empty. Every row keeps `currency_changed`, so the user confirms currency and unit before saving.
- **Example (invented):** `ABCD 12 יחידות 41.35 +0.80% +$3.97` gives quantity 12, price 41.35, USD. `הראל פיננסים 3 יחידות 98,400.00 +1.20% +₪35.42` gives quantity 3, price 98,400.00, ILS in agorot.

### Full portfolio screen (Update 11)

The first screen has a summary header (`תיק אישי`, total, `שינוי יומי`, `שינוי מעלות`, `יתרות`, `פירוט מזומן ובטחונות`,
`האחזקות שלי`, `מיון`, tabs `הכל / ני"ע זרים / קרנות`) and a section bar `ניירות זרים`. Those words never start a card;
a simple card (no exchange line) is only read after the first anchored card or section bar. `2.891.30` reads as 2,891.30;
a leading `…` in a name is dropped; a card cut off at the bottom of one screen is replaced by the complete copy from the
next (same price, no P&L % on the cut copy) without a conflict.
