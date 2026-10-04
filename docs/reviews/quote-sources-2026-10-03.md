# Free fallback price sources (research, 2026-10-03)

Research agent, read-only. Many vendor pages were blocked in the sandbox (finnhub.io, frankfurter.dev, openapi.tase.co.il), so most facts come from secondary sources and are marked "unverified". **Check each vendor's own page before relying on it.**

## Recommendation
| Market | Chain | Notes |
|---|---|---|
| US stocks / ETFs (live quote) | yfinance → **Finnhub `/quote`** → last cached close | Finnhub free: 60 calls/min, real-time US quotes, key without a card. No candles on the free tier (403). |
| US history (ATR, moving averages) | yfinance → **FMP free EOD** (250 calls/day, ~5 years) or Stooq CSV → stale cache | Daily bars change only after the close: cache 24 h. |
| Crypto | yfinance → **CoinGecko demo key** | 30/min, 10,000/month, attribution required; batch all coins in one call. |
| USD/ILS | yfinance `ILS=X` → **Bank of Israel representative rate** or **Frankfurter (ECB)** → last cached | Daily rates, not live; show the date. BoI is the official rate Israeli brokers use. |
| TASE stocks / funds | yfinance `.TA` (agorot ÷ 100) → **TASE Data Hub "End of Day Securities Trading Data"** (free, "internal use", signup + key; unverified) → last stored close, labelled with its date | **No good free live or delayed fallback exists.** Twelve Data's TASE coverage is Pro only (paid). |
| Indices | S&P 500 / VIX: yfinance → last close with its date (Stooq now needs a key). TA-35 / TA-125: yfinance → TASE Data Hub if it covers indices (unverified) → last close | |

Not recommended: Alpha Vantage (25 requests/day, and its terms define other users' access as commercial), Tiingo (free tier is "internal, personal use only"), Polygon/Massive (5/min), scraping investing.com, Globes, Bizportal or TASE web pages (their terms forbid it; investing.com says data contracts prohibit an API).

## Source-by-source (condensed)
| Source | Covers | Free limits | Terms note | Units / history |
|---|---|---|---|---|
| Finnhub | US stocks/ETFs; no TASE | 60/min, key, no card | "Personal, non-commercial"; ToS not fetched | `/quote` gives c, d, dp, h, l, o, pc, t in USD; `/stock/candle` 403 on free keys |
| CoinGecko demo | Crypto in many fiat currencies incl. ILS and USD | 30/min, 10k/month | Attribution required on the free plan | Daily history on the demo plan (about 365 days, unverified) |
| Stooq | US (`.us`), world indices, FX, crypto; TASE unknown | Since ~2026-04-01 an apikey is required (captcha page or email); unpublished quota; over-quota gives error text with HTTP 200 | Terms not found; described as personal/research use; automated downloads were disabled in 2020 | Daily OHLCV CSV; treat a non-CSV answer as a failure |
| Alpha Vantage | US, FX, crypto | 25/day, 5/min | Defines commercial use as letting others access the information | Too small for 30 symbols |
| Twelve Data | ~15 US exchanges, FX, crypto; **TASE Pro-only** | 8 credits/min, 800/day | Not verified | |
| Financial Modeling Prep | US only, EOD, ~5 years | 250/day | Not verified | Good history backup |
| Tiingo | US, crypto; no TASE | 50/hour, 1,000/day | **"Internal, personal use only"** | 30+ years daily |
| Polygon (now Massive.com) | US | 5/min | Not verified | |
| Frankfurter / ECB | FX incl. ILS | no key | Open data; cache/attribution rules unverified | `https://api.frankfurter.dev/v1/latest?base=USD&symbols=ILS` |
| Bank of Israel | USD/ILS and other representative rates | public | Official public data; reuse terms unverified | `https://www.boi.org.il/PublicApi/GetExchangeRates?asXml=true`; SDMX at `https://edge.boi.org.il/FusionEdgeServer/sdmx/v2/data/dataflow/BOI.STATISTICS` |
| TASE Data Hub | TASE EOD prices, volume, market cap | developer-portal signup + key; EOD product listed free for **internal use** | Redistribution licensed separately; whether an invite-only multi-user app counts as internal is unclear | Closes probably in agorot: confirm in the API guide and normalise at the provider layer |

Data-center IPs: Yahoo rate-limits Render and similar hosts (a project saw 8,030 of 9,186 fetches rejected with 429 on 2026-09-30). No reports were found of Finnhub, CoinGecko, Frankfurter or BoI blocking data-center IPs; test from the real host.

## Chain and TTLs (about 30 symbols, 5 users, refresh every 5 min in market hours)
Fetch each symbol once on the server, shared across users (about 30 calls per 5 minutes: far inside Finnhub's 60/min).
- US quotes: 5 min in market hours, 6 h otherwise. US history: 24 h. Crypto: 2–5 min (one batched call). USD/ILS: 1 h live, 24 h for the daily fallback (labelled as a daily reference rate with its date). TASE fallback: cached 24 h. Indices: 5 min live, 24 h fallback.
- Every price carries `source`, `as_of`, `is_stale`; the UI shows "Delayed" or "Last close, <date>" whenever a fallback is used.
- A circuit breaker per provider: after a 429 or 403, back off, then move to the next source.
- Never mix agorot and ILS; convert in the provider and test with fixtures. Limits are config values, not code constants. Keep fallbacks behind the existing provider interface, key-gated by env vars.

## Terms and risks
1. **Multi-user terms are a grey area.** Most free tiers are "personal, non-commercial" or "internal". An invite-only app with logged-in family members arguably serves data to people other than the key holder. Mitigations: keep data behind login, never expose a public data API, never export or redistribute it, show attribution (footer: "Data: Yahoo Finance, Finnhub, CoinGecko, ECB / Bank of Israel, TASE"), and ask a vendor in writing for certainty. Finnhub is the most workable; Tiingo the strictest. Yahoo has the same issue, so a second source reduces availability risk, not legal risk.
2. Stooq is fragile (captcha key, unpublished quota, HTTP 200 errors, history of disabling automated access): optional third source only.
3. Finnhub free has no candles: history cannot come from it.
4. TASE: no free live feed; Data Hub EOD is "internal use", needs signup, units and index coverage unverified. Honest fallback: last close with its date.
5. Rebrands and limit changes happen (Alpha Vantage 500 → 100 → 25/day, Polygon → Massive, Stooq added a key in 2026).

## Could not verify
> **Update 2026-10-04:** Frankfurter, Bank of Israel (XML element names), the CoinGecko coin ids and the Yahoo symbols were verified live; see `live-data-check-2026-10-04.md`. TASE Data Hub, Stooq and the vendor terms are still unverified.

Finnhub ToS, the Frankfurter site and terms, the TASE Data Hub portal and API guide (so TASE units, index coverage, and "internal use" for a multi-user app), whether Alpha Vantage returns TASE quotes, Stooq's terms, quota, TASE coverage and cloud-IP blocking, full terms of FMP/Tiingo/Polygon/Twelve Data, whether any key-based API blocks data-center IPs, CoinGecko's exact cache window and history depth, and Yahoo's current block status.
