# Research: efficiency and precision (2026-10-05)

Intended path: `scratchpad/research-efficiency-precision-2026-10-05.md` (coordinator's change). Plan mode was active, so it is saved here, the only writable file. Copy it as is.

Scope: read CLAUDE.md, docs/index.md, product-decisions, rag-spec, the existing 2026-10-05 reviews (token-saving, free-llm-providers, pre-deploy, quote-sources), and the code in signals/, scoring/, providers/, llm/, committee/, rag/, importer/, scheduler/, backtest/. Items those reviews already cover (Gemini model shutdown, Groq TPM, UptimeRobot, Supabase backups) are not repeated.

What is already good: structured JSON outputs on all three LLMs, validate → retry once → template, chunk-citation check, a check that every number in an answer appears in the prompt, a verdict-word ban, a response cache, a market-hours gate on the quote job, batch `yf.download` for quotes, lazy `yfinance` import, qty×price≈value check on OCR rows, and Wilson intervals with train/held-out split in the backtest.

Measured locally (fast machine, `.venv`): `import app.main` takes **1.9–2.5 s and 137 MB RSS**. `pandas` loads at startup through `app.providers.base`. On a 0.1-CPU Render instance that is roughly 20–25 s of CPU before the first request.

Legend: effort S/M/L. **[USER]** = needs user decision (weights, risk logic, thresholds, paid service).

---

## Top 8 (priority order)

| # | Change | Where | Benefit | Effort |
|---|---|---|---|---|
| 1 | Persist daily bars in a `daily_bar` table and fetch incrementally (`period="5d"` append) instead of refetching 420 days after every TTL and every cold start | new table + `providers/yfinance_provider.py:get_history`, `backtest/data.py` reuse | 10–50x fewer Yahoo bytes/calls, survives Render sleep, less rate-limit risk | M |
| 2 | Cold start: `python -m compileall -q app` in `Dockerfile.slim`; merge `migrate` + `bootstrap-admin` into one CLI call; move `pandas` imports in `providers/base.py`, `api/exit_levels.py`, `importer/parse.py` chain behind `TYPE_CHECKING`/function scope | `Dockerfile.slim:22,56`, those modules | Saves an estimated 10–30 s per cold start on 0.1 CPU and about 60 MB at idle | S |
| 3 | Earnings-date awareness: Finnhub free `/calendar/earnings` (1-month window, US) + yfinance 1.0 earnings calendar; mark "earnings in N days" in `Explanation`, lower technical confidence inside the window, flag gap risk on exit levels | new `providers/earnings.py`, `signals/technical.py`, `scoring/exit_levels.py` | Stops look safer than they are across earnings gaps | M ([USER] for any confidence/stop change) |
| 4 | Volatility-normalised technical scores: express trend/momentum as z-scores or ATR multiples (e.g. (close−SMA50)/ATR14, 12-1 momentum ÷ realised vol) instead of fixed RSI/Stoch points | `signals/technical.py`, `signals/indicators.py`, config | Scores mean the same thing for TEVA.TA and NVDA; fewer false "oversold" calls in high-vol names | M [USER] |
| 5 | Backtest overfitting guards: count every config tried (`n_trials`) and report Deflated Sharpe, PBO (CSCV) and a survivorship warning (the universe is today's 89 names) | `backtest/experiment.py`, `backtest/report.py` | The gate cannot open on a lucky config | M |
| 6 | Calibrate "confidence" into a hit probability once paper calls resolve: isotonic/Platt fit of score → P(beat benchmark at horizon), with a Brier score and reliability table on the track-record page | `trackrecord.py`, `papertrading.py`, new `scoring/calibration.py` | Turns "confidence 0.7" (today it measures data coverage, `technical.py:272`) into something users can check | M (S for reporting only) |
| 7 | Analyst signal from **revisions**, not static consensus: Finnhub free `/stock/recommendation` monthly trend (Δ buy-share over 1–3 months) + yfinance `upgrades_downgrades`/EPS-trend; TASE → `confidence=0` | new `signals/analysts.py` (slot already weighted 20) | Literature: revisions predict returns better than consensus levels | M [USER] for weights |
| 8 | OCR accuracy for Hebrew screenshots: 2x upscale of small text, auto-invert dark mode, adaptive threshold, `tessdata_best` heb, a numeric pass with a digit whitelist on cropped columns; extra check rows: Σ row values ≈ displayed total, agorot/ILS ×100 detector | `providers/ocr/tesseract.py`, `importer/imageio.py`, `importer/parse.py` | Fewer wrong quantities reaching the review screen | M |

---

## A. Precision

### A1. Signals and scores
1. **Volatility-normalised indicators** (top #4). Today RSI ≥ overbought gives −40 minus the excess, Stoch gives ±35, both fixed (`technical.py` ~L125–175). Use ATR- or σ-scaled distances and map with `tanh(z/k)` to [-100, 100]. Why: vol scaling roughly doubled momentum's Sharpe and cut its max drawdown from −97% to −45% in Barroso & Santa-Clara. Source: https://www.researchgate.net/publication/256017573_Momentum_Has_Its_Moments · https://alphaarchitect.com/avoiding-momentum-crashes/ — M [USER]
2. **Regime filter as an Explanation line plus a confidence multiplier** (not a score change): benchmark above or below its 200-day SMA, VIX above or below its 50-day SMA (`^GSPC`, `^TA125.TA`, `^VIX` are already reference symbols). In a bear regime, lower confidence on bullish technical calls. Why: 200-DMA/VIX filters cut trend-following drawdowns out of sample. Source: https://alphaarchitect.com/vix-trend-following-out-of-sample/ · https://setup4alpha.substack.com/p/i-tested-20-trend-based-regime-filters — M [USER]
3. **Confidence should not be data coverage only.** Today `confidence = (0.4+0.6·depth)·coverage`. Add agreement between categories (trend vs momentum disagreeing → lower confidence) now, and the calibrated probability (top #6) later. `combine.py` already spreads weight by confidence, so this flows through without a weights change. Why: overconfident scores inflate sizing. Source: https://www.mql5.com/en/articles/21938 — S
4. **Earnings awareness** (top #3). Finnhub free earnings calendar: 1 month ahead, US only. yfinance 1.0 (2026-01-24) added an earnings calendar. TASE: none free, so show "unknown". Why: stops cannot protect against an earnings gap. Source: https://apicostcalc.com/finnhub.html · https://github.com/ranaroussi/yfinance/releases · https://tradeology.app/academy/swing-trading/overnight-gap-risk-management — M
5. **Analyst revisions** (top #7). Use dispersion as a confidence reducer (more dispersion → lower confidence). Source: https://www.federalreserve.gov/econres/feds/files/2024049pap.pdf · https://papers.ssrn.com/sol3/Delivery.cfm/SSRN_ID270036_code010523600.pdf?abstractid=270036 — M [USER]
6. **Sentiment signal is not built** (no F&G/VIX code found in `signals/`). When built: CNN F&G needs browser headers or it returns HTTP 418. Cache it daily and fall back to a VIX-percentile + breadth proxy (CLAUDE.md rule). Treat it as a market-wide regime input, not a per-stock score. Source: https://pkg.go.dev/github.com/wildsurfer/cnn-fear-and-greed-parse/v2 — M

### A2. Backtest and gate
7. **Deflated Sharpe + PBO + trial counter** (top #5). `experiment.py` already reports held-out Wilson intervals, but it does not track how many configurations were tried (the 2026-10-04 tuning tried at least 6). Why: PBO is about 0.5 on pure noise, and DSR never approved noise strategies. Source: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551 · https://github.com/esvhd/pypbo (implement in numpy; don't add the dependency) — M
8. **Survivorship bias**: `universe_seed.txt` (89 names) is today's list, so past windows exclude delisted losers. Add a caveat line to the report now, and point-in-time membership later if a free source exists. Why: it inflates held-out success. — S
9. **Purge/embargo** between train and held-out: windows that straddle the split are already dropped. Also add a gap equal to the horizon. Source: https://github.com/quantskills/skill-backtest-overfit — S

### A3. LLM accuracy
10. **Per-claim support check (cheap, no LLM)**. Today numbers must appear somewhere in the *prompt*. Tighten this so each claim's numbers and named entities must appear in *its cited chunk(s)*. Optionally require a ≤25-word verbatim `quote` field per claim and check that it is a substring of the cited chunk. On failure, drop that claim instead of the whole answer. Where: `committee/roles.py:numbers_grounded`, `committee/schemas.py`. Why: fine-grained quote attribution is the 2025 best practice, and cited-but-unsupported is the main failure. Source: https://arxiv.org/pdf/2508.15396 · https://arxiv.org/html/2605.06635v1 — S
11. **No self-consistency for committee roles.** Gains are 0.4–1.6 points at nearly linear token cost. If you want it at all, use it only for the Bear "top risk" choice, 2 samples, and only when the cache misses. Source: https://arxiv.org/html/2511.00751 — (decision: skip)
12. **Schema hygiene**: enums for categorical fields (risk type, sentiment), `maxItems` on lists, numbers as references to input keys (e.g. `"value_ref": "pe_ttm"`) instead of free numbers, so the code fills in the actual value. Why: the LLM never writes a number, which removes the whole class of number hallucinations. — S
13. **Retrieval quality on Postgres**: use `websearch_to_tsquery('simple', …)` (handles quotes and OR safely) and keep `ts_rank_cd`. `pg_search` (BM25) is not on Supabase. Source: https://github.com/orgs/supabase/discussions/18061 — S

### A4. OCR / import (Hebrew screenshots)
14. **Preprocessing** (top #8): `tesseract.py` passes only grayscale with `--psm 6`. Add: invert when the mean luminance is dark (dark-mode apps), upscale 2x when the text height is below ~20 px, adaptive threshold, and the `tessdata_best` heb model. Also run a second numeric-only pass (`tessedit_char_whitelist=0123456789.,-%₪$`) on column crops, using the layouts in `importer/layouts.py`. Why: Tesseract does best at about 300 DPI with binarisation, and numeric fields are where it fails (81% numeric-field recovery on invoices). Source: https://www.nutrient.io/blog/tesseract-python-guide/ · https://aurigait.com/blog/how-to-increase-accuracy-of-tesseract/ · https://mf-sr.com/en/blog/ocr-hebrew-2026-practitioner-guide.html — M
15. **Cross-checks in `parse.py`**: (a) Σ row values ≈ portfolio total shown on the screen; (b) TASE price 100x off (agorot vs ILS) when qty×price ≈ 100×value; (c) match by TASE security number (מספר נייר) before name; (d) flag fields that Gemini and Tesseract read differently when both ran. Why: catches errors before the user confirms. — S
16. **Gemini OCR**: keep `temperature=0` + schema. Add a per-field `confidence: low|ok` so the review screen highlights doubtful cells, and send only the cropped table region (fewer tokens, no account header). VLMs hallucinate on unclear text, so never auto-accept. Source: https://mf-sr.com/en/blog/ocr-hebrew-2026-practitioner-guide.html — S

---

## B. Efficiency

### B1. External calls
17. **`daily_bar` table + incremental fetch** (top #1). About 89–300 symbols × 420 bars ≈ 5–10 MB, well inside Supabase's 500 MB. Also fetch history in batches with `yf.download([...], period="5d", group_by="ticker", threads=False)` instead of one `Ticker.history` per symbol (`yfinance_provider.py:290`). Why: Yahoo rate-limits bulk and data-centre IPs (`YFRateLimitError`), and the in-memory cache is lost at every Render sleep. Source: https://github.com/ranaroussi/yfinance/issues/2614 · https://github.com/ranaroussi/yfinance/issues/2422 — M
18. **Pin `yfinance>=1.0`** and use `yf.config` retries; it needs `curl_cffi` (don't pass a `requests.Session`). Source: https://github.com/ranaroussi/yfinance/releases — S
19. **FX from Bank of Israel SDMX** (official, free, daily representative rate): `edge.boi.gov.il/FusionEdgeServer/sdmx/v2/data/dataflow/BOI.STATISTICS/EXR/1.0/?c[DATA_TYPE]=OF00&format=csv`. One call per day for every currency. Use it for trade-date FX, and keep `ILS=X` for intraday only. Check that `providers/fx_provider.py` "boi" uses this endpoint. Source: https://www.boi.org.il/media/tzxbuhhj/extracting-representative-exchange-rates-from-the-new-series-database.pdf — S
20. **Conditional requests**: Yahoo, Finnhub and CNN don't support ETag usefully. SEC EDGAR (`data.sec.gov`) serves static JSON, so send `If-Modified-Since` for `submissions/CIK*.json` and `companyfacts`. Add a declared User-Agent with a contact address (not the user's email), keep ≤10 req/s, and do it in the GitHub Actions job, not on Render. Source: https://www.sec.gov/edgar/searchedgar/accessing-edgar-data.htm · https://tldrfiling.com/blog/sec-edgar-api-rate-limits-best-practices — S
21. **TTL tuning**: fundamentals and profile 24 h → 7 days (they change quarterly); analyst trends 24 h; earnings calendar 12 h; F&G 1 h in market hours; skip all non-quote jobs on weekends and holidays (the gate exists for quotes only, `jobs.py:45`). — S

### B2. LLM tokens
22. **Prefix layout for implicit caching**: the Gemini 3.5 Flash family caches implicitly only above **4,096 tokens** (2.5 Flash: 2,048). Role prompts are capped at 1.5–2.5k, so implicit caching will **not** trigger. Don't pad prompts to reach it. The app's own `LlmCache` is the real saver, so make its key stable: sort chunks by id, round numbers in `PublicFacts`, and exclude timestamps. Source: https://ai.google.dev/gemini-api/docs/caching · https://memx.app/blog/gemini-caching-trap-prompts-under-1024-tokens/ — S
23. **Skip-the-call rules**: no new chunks and unchanged `PublicFacts` hash since the last run → reuse the last answer even after the cache TTL ends; and run News only when a chunk newer than the last run exists. — S
24. **Batch roles where safe**: Company Profile + News in one structured call (one schema with two sections) halves the number of requests against RPM/RPD caps (Gemini Flash-Lite free is about 15 RPM / 500–1,000 RPD in late 2026). Keep Bear and CIO separate, because they must see the earlier output. Source: https://tinkerllm.com/blog/gemini-api-free-tier-limits-rate-quotas/ — M

### B3. Memory/CPU on 512 MB / 0.1 CPU
25. **Bytecode + single boot process** (top #2): `PYTHONDONTWRITEBYTECODE=1` with no `compileall` means app modules compile from source at every cold start. The CMD runs three Python processes in a row (`migrate`, `bootstrap-admin`, `uvicorn`), so the import cost is paid three times. Source: https://github.com/MAVet710/buyer-dashboard/pull/540 (54 s startup on 0.1 CPU before fixes) · https://github.com/Md-Arif-hasnat99/Researchly/pull/26 — S
26. **pandas discipline**: use `float32` for OHLCV in caches and drop unused columns (`Adj Close`, `Dividends`, `Stock Splits`) right after the fetch. Use `threads=False` in `yf.download` (threads create a frame per thread). Measured: pandas 63 MB, yfinance +35 MB, exchange_calendars +9 MB at import. — S
27. **Universe screener out of the API process** (pre-deploy H1 confirms): run it in GitHub Actions and write `SignalCache` rows. The API only reads. — M
28. **Scheduler spacing**: quotes every 5 min, scores every 30 min, universe every 15 min. Offset them (`IntervalTrigger(start_date=…+k·60s)` or jitter) so no two heavy jobs start in the same minute on 0.1 CPU. Universe every 15 min is more than the 720-min re-score TTL needs: 60 min is enough. — S

### B4. Postgres (Supabase free)
29. Stay on the **session pooler (5432, IPv4)** with the persistent pool (3+2). Use the transaction pooler (6543) only with `NullPool` + `prepare_threshold=None`; port 6543 has been transaction-only since 2025-02-28. Free Nano: about 60 direct / 200 pooler client connections. Keep `pool_pre_ping=True` and `pool_recycle` < the Supavisor idle timeout. Source: https://supabase.com/docs/guides/troubleshooting/using-sqlalchemy-with-supabase-FUqebT · https://dev.to/papansarkar101/supabase-connection-scaling-the-essential-guide-for-fastapi-developers-348o — S

---

## C. Free data APIs: state on 2026-10-05

| Source | Status / change | Risk | Source link |
|---|---|---|---|
| yfinance | 1.0 released 2026-01-24 (stable; earnings calendar, retry config); needs `curl_cffi`; `YFRateLimitError` on bulk/DC IPs | Unofficial; terms grey (accepted by user) | https://github.com/ranaroussi/yfinance/releases |
| Finnhub free | 60/min; quote, news, recommendation trends; earnings calendar 1 month US only; 4 quarters EPS surprise; no candles | Personal/non-commercial | https://apicostcalc.com/finnhub.html · https://finnhub.io/pricing |
| FMP free | 250 calls/day, US only, 5 years of prices, 5 quarters of statements, "max limit 5" | Personal only | https://site.financialmodelingprep.com/pricing-plans |
| Alpha Vantage | 25/day, 5/min (was 500 → 100 → 25) | Too small; skip | https://www.macroption.com/alpha-vantage-api-limits/ |
| Stooq | API key required since April 2026 (captcha or email); keyless returns "Access denied" | Key must be fetched by hand; fragile | https://github.com/pydata/pandas-datareader/issues/1012 |
| TASE Data Hub | **Free (internal use):** Securities-Basic, Indices-Basic, TASE indices online, Mutual Funds-Basic. **Paid:** Securities EoD and 15-min delayed prices | No free TASE prices; indices online are free and worth a look for TA-35/TA-125 | https://content.tase.co.il/media/4imn13pz/2001_api_pricelist_2026_eng.pdf |
| Bank of Israel | SDMX v2 endpoint at edge.boi.gov.il, CSV, daily representative rates, no key | Low | https://www.boi.org.il/media/tzxbuhhj/extracting-representative-exchange-rates-from-the-new-series-database.pdf |
| CNN Fear & Greed | Unofficial `production.dataviz.cnn.io/index/fearandgreed/graphdata`; HTTP 418 without browser headers | Can break at any time; keep the proxy | https://pkg.go.dev/github.com/wildsurfer/cnn-fear-and-greed-parse/v2 |
| CoinGecko Demo | Free, 10,000 calls/month, 100/min (needs a demo key) | At 180 s TTL with several coins this can exceed 10k/month; use one `simple/price` call for all ids and a 5-min TTL | https://docs.coingecko.com/reference/api-usage |
| SEC EDGAR | Free, no key, 10 req/s, User-Agent with contact required, 403 without it | Low | https://www.sec.gov/edgar/searchedgar/accessing-edgar-data.htm |
| Gemini free | 3.x Flash-Lite about 15 RPM, 500–1,000 RPD (sources vary; check AI Studio); implicit-cache floor 4,096 tokens for 3.5+ | Limits changed several times in 2026; free prompts may be used for training (already handled by `PublicFacts`) | https://ai.google.dev/gemini-api/docs/caching · https://tinkerllm.com/blog/gemini-api-free-tier-limits-rate-quotas/ |
| Mistral free (Experiment) | About 1 RPS, about 1B tokens/month; exact limits only in the console; phone verification | "Evaluation, not production" wording | https://pricepertoken.com/endpoints/mistral/free |
| Groq free | gpt-oss-20b/120b, Qwen 3.6: 30 RPM, 1k RPD, 8k TPM, 200k TPD; Llama removed Sept 2026. **Adding a card raises limits: PAID-adjacent, don't** | TPM binds | https://klymentiev.com/blog/groq-pricing |

Paid items flagged (not recommended): TASE Securities EoD (paid), Finnhub premium, Alpha Vantage premium, Gemini explicit caching (needs billing), Groq card upgrade.

## Needs user decision (summary)
#4 vol-normalised scoring, regime confidence multiplier, earnings-window confidence/stop handling, analyst-revision weights, any change to how confidence affects sizing.
