# Live data check (2026-10-04)

Earlier sessions could not reach the data sites, so several facts were marked "unverified". This session could reach most of them. Each site was called, and the repo's own parsers were run on the answers. Recorded answers: `backend/tests/fixtures/live_2026_10/` (tested in `tests/test_recorded_sources_2026_10.py`). Live checks: `pytest -m live tests/test_live.py` (5 passed, 1 skipped for lack of a CoinGecko key).

| Source | Result | What changed |
|---|---|---|
| **GemelNet** (data.gov.il, dataset `gemelnet`) | ✅ Works, no key. Resource `a30dcbea-a1d2-482c-ae29-8f781f5025fb` = "2024 to today", refreshed daily, 23,036 rows, latest month 2026-08. All 8 configured column names match exactly. `MONTHLY_YIELD` is in percent. `q`, `filters` (fund id, classification) and `sort` all work. Largest category has 259 funds in one month, under the 500-row read. | The resource id is now the **default** (no env var needed). Other files hold 1999–2022 and 2023, so a fund's series starts at 2024-01 (32 months today). |
| **Bank of Israel** `GetExchangeRates?asXml=true` | ✅ Works, no key. Elements `Key`, `CurrentExchangeRate`, `LastUpdate`, `Unit` (USD unit = 1). USD 3.06 on 2026-10-02. | The parser was already right; the docstring now says verified. |
| **Bank of Israel SDMX** (`edge.boi.org.il`) | ✅ Reachable (HTTP 200). | Not used by the code. |
| **Frankfurter** (ECB) | ✅ Works, no key. USD/ILS 3.0653 on 2026-10-02. | None. |
| **CoinGecko** `simple/price` | ✅ All 8 configured coin ids resolve. Without a key it answers; with a **wrong** key it answers 401. | The live test now skips without a real key. |
| **Yahoo** (yfinance) | ✅ `TEVA.TA`, `LUMI.TA`, `NICE.TA` priced in `ILA` (agorot); `TA35.TA`, `^TA125.TA` in ILS points; `^GSPC`, `^IXIC`, `^VIX`, `ILS=X`, `BTC-USD` all resolve. The raw chart URL gave HTTP 429 once from this IP (yfinance itself worked). | Index symbols in CLAUDE.md confirmed. |
| **Finnhub**, **FMP** | Reachable; 401 without a key, which is expected. | Needs your free keys (see `docs/reminders.md`). |
| **TASE Data Hub** | `datahubapi.tase.co.il` is the developer portal (a sign-in web app); `openapi.tase.co.il` did not answer. The product list cannot be read without an account. | Still unverified: needs your sign-up. |
| **Stooq** | Not reachable from this sandbox. | Optional third source; unchanged. |

Still open: TASE Data Hub units and terms, Finnhub/FMP behaviour with a real key, and every vendor's terms of use (not re-checked here).
