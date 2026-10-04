# Phase 1.5 re-review: verification of the hardening (2026-10-03)

Reviewer: Opus review agent, read-only. It read the Phase 1.5 diff (`2a6e102..HEAD`, 148 files) and wrote its own probes (FastAPI TestClient, real uvicorn with raw sockets, a modified memprobe, an Alembic batch-migration test).
Baseline re-run by the reviewer: backend 460 passed / 12 skipped, ruff and mypy clean; frontend 118 tests passed. Not run: frontend build, Postgres, Docker, anything live.
Labels: **verified** = reproduced by a probe; **by reading** = found in the code; **web** = from a cited source, not tested here.
Status: **findings accepted; fixes are Phase 2.0 (see the end).**

## Fix verification
| Earlier problem | Status | Evidence |
|---|---|---|
| Single-ticker `get_quotes` never returns a quote | **FIXED** | `frame_for_symbol` handles both MultiIndex orders and flat frames (probed). |
| Unknown currency → ×100 / ×360 | **FIXED** (caveat) | `resolve_currency`, `store_quotes` and `to_ils` guard it. Caveat (by reading): Yahoo's currency cache is in memory only, so after every Render restart each TASE symbol needs a successful `fast_info` call before it gets a quote. Under a Yahoo 429 TASE prices stay stale for hours (safe but slow). |
| TWR loses flows on days with no snapshot | **PARTIAL** | Gap days are fixed. **Two new flow holes (verified):** (1) a holding added with no quote and no cost records **no transaction**, so when the quote arrives the since-start shows **+60,000 ILS / +1,666 %**; (2) `_record_flow` uses `sec.currency` while the price may be the cost-fallback price in `cost_currency`: AAPL with an ILS cost of 700 became a 7,000 **USD** flow = 25,200 ILS, a phantom loss of 18,200 ILS. |
| `h.pnl` null crash in the UI | **FIXED** | `HoldingsList.tsx:15` renders `NoData`; mock includes a null-pnl holding. |
| Fuzzy matcher superstring auto-accept | **PARTIAL** | "Apple Hospitality REIT" no longer becomes AAPL. **But the exact-name auto-accept indexes every `Security`, including other users' unverified tickers (verified):** user A adds `PALANTIR`; user B imports "Palantir" and it is silently matched to A's junk row; B's import of "Apple" lists A's `APPLE` as a candidate (leaks A's watchlist). "אלפבית" maps only to GOOGL, so a GOOG holder gets GOOGL. |
| Week starts Monday | **FIXED** | |
| TASE holidays empty | **FIXED** | `exchange_calendars` 4.13.2 XTAE: Fri 2026-10-09 is a session with a 13:50 close; Sun 10-04 and 10-11 are closed (verified). |
| Stale close; no backoff | **FIXED** (minor gap) | Single-ticker retries always take the first 5 missing symbols in sorted order, so junk alert symbols take those slots every cycle. |
| Match silently overwrites the row currency | **PARTIAL** | The `currency_changed` flag blocks confirm, **but it compares `row.currency` while `confirm_draft` stores `cost_currency` from `row.unit` (verified):** currency `USD` with unit `ILS` gets no flag and is stored as an ILS cost. |
| Screenshot price written as a global quote | **FIXED** | |
| FX staleness; silent 3.6 | **FIXED** | |
| No launch-gate mechanism or test | **PARTIAL** | The gate exists, but **the contract test is easy to bypass (verified):** fields named `rating`, `status: Literal['buy','sell']`, `outlook`, `target_price`, `bullish`, `trim_pct`, `STRONGBUY`, `opinion`, `call` all pass; so do `dict[str, Any]` payloads (such as `explanation`) and routes without a `response_model`. Enum *values* are never scanned. Telegram and `Notification.body` are not covered. |
| NaN volume in reasons/export | **FIXED** | New related hole below (NaN via API JSON). |
| No compose, CI, lockfile | **FIXED** | GitHub Actions are pinned by tag, not SHA. |
| Weight formatting; `"1,234"` parsed as NaN | **FIXED** | |
| Unauthenticated, unbounded upload spooled to disk | **FIXED** | Verified: a 9 MB chunked body without Content-Length → 413; multipart → 415; GIF → 415; **0 temp files**; a lying `Content-Length` → 400 after ~3 MB; a PNG `tEXt` chunk with an owner name is not stored or echoed. Auth runs before the body is read. |
| Decompression bomb | **FIXED for one request; PARTIAL under concurrency** | A 60k×60k header gets 413, but **3 concurrent 25 MP RGBA PNGs (0.5 MB each) reached a 718 MB peak (verified)**. No decode semaphore, and the pixel cap ignores bytes per pixel. Only applies when the server decodes images (Gemini key set, or the Tesseract image); slim with no key answers 503 before decoding. |
| argon2 64 MiB per hash | **FIXED** | 8 concurrent logins peak 254 MB. |
| Non-stock OCR lines stored | **PARTIAL** | A line where qty × price ≈ value survives with a name like "Israel Israeli 012345678" (verified). `tase_number`, `currency`, `symbol` are unbounded free text (a 1 KB ID string and a 100 KB currency were stored). No server-side digit-run scrub. |
| Limiter keyed on peer IP; signup/upload unlimited | **NOT FIXED in the real deployment** | **web:** a Cloudflare Worker/Pages Function → Render subrequest is cross-zone, and Cloudflare overwrites `CF-Connecting-IP` to `2a06:98c0:3600::103` for every such subrequest. With `TRUSTED_PROXY_HEADER=CF-Connecting-IP`, **every user shares one IP**: signup is 10/hour globally and the login IP key is shared. The email backoff also lets an attacker lock a victim out **99.4 % of an hour with 20 requests (verified, simulated clock)**. Upload/rows limits work. |
| Bot token in httpx logs | **FIXED** | |
| Unvalidated PATCH JSON | **FIXED** for portfolio/holding PATCH | Import bodies remain loose (below). |
| Global `Security` rows leak; unlimited alerts | **PARTIAL** | Search is fixed; **the importer still uses unverified securities from all users.** |
| Signup enumeration; invite race | **FIXED** | |
| `DELETE /me` without password; sessions | **FIXED** | A deleting B's session id → 404 (verified). `last_seen_at` is written at most once per 300 s. |
| No security headers; docs public | **FIXED** | Production does not refuse `CORS_ORIGINS=["*"]` or SQLite. |
| Logout keeps SWR cache | **FIXED** | |
| Images not pinned | **FIXED for Docker** | |

## New problems
| Sev | Location | Problem | Fix | Why / failure scenario |
|---|---|---|---|---|
| high | `functions/api/[[path]].ts`, `auth/ratelimit.py`, `docs/deployment.md` | The limiter's client IP is the Cloudflare Worker IP for all users (web). | The Function sends `X-Client-IP` (from the incoming `CF-Connecting-IP`) plus `X-Proxy-Auth: <secret>`; the API trusts `X-Client-IP` only when the secret matches, otherwise uses Render's `CF-Connecting-IP`. Startup check that the secret is set when the header is trusted. Verify with a deployed echo endpoint. | 10 failed signups an hour block every invitee; 20 failures cause a site-wide login backoff. |
| high | `api/auth.py:113-128`, `BackoffLimiter` | An attacker keeps a victim locked out ~99 % of the time with 20 requests an hour (verified). | Key the backoff on (email, IP) with a much higher email-only threshold; a "known device" cookie exemption; optional Cloudflare Turnstile after N failures. | A family member can't log in during a market drop. |
| high (latent, SQLite) | `migrations/env.py` + `db.make_engine` (`PRAGMA foreign_keys=ON`) | A future **batch** migration on `user` or `portfolio` runs `DROP TABLE`, which cascades (verified: users 1, portfolios 0, sessions 0 after `batch_alter_table("user")`). | In `env.py` for SQLite: `PRAGMA foreign_keys=OFF` before the transaction, then `foreign_key_check`, then back on. Add a "migrate with data" test. | Option B (home device) or dev loses every portfolio on the first Phase 2 migration that alters `user`. |
| high | `api/portfolios.py:253-274, 312-319` | The two flow holes above. | Use `v.currency` from the valuation; record a *deferred* flow when a holding's first real price arrives (or keep unpriced holdings out of both value and flows). Tests. | Exit levels, track record and weekly review build on P&L. |
| med | `importer/parse.py:ParsedRow`, `diff.ProposedChange` | NaN and Infinity accepted (`quantity: 1e308`, `value: Infinity` confirm with 200; summary then returns `"value":{"ils":null}` and breaks `SummaryOut`; on Postgres NaN in JSON → 500). `ProposedChange.currency` is free text. | `allow_inf_nan=False` with `ge/le` bounds on every numeric field; `currency` as `Literal["ILS","USD"]`; one strict JSON parser that rejects NaN. | One malformed row breaks the dashboard. |
| med | `api/imports.py` rows + PATCH | `tase_number`, `symbol`, `currency`, `index` unbounded; `ImportPatch.rows` has no `max_length`; PATCH has no rate limit; names are not scrubbed. | `tase_number` `^\d{5,9}$`, cap symbol/currency, mask runs of 6+ digits in `name` server-side, `max_length=200` on PATCH rows, rate-limit PATCH. | PII can be stored although `docs/security.md` says it never is; each request can store ~1 MB. |
| med | `importer/service.finalize_rows` | The match index includes every user's unverified `Security`. | Index verified securities plus symbols already in *this* user's portfolios. | One user's typo changes another user's import. |
| med | `importer/service.confirm_draft:398` vs `match.apply_match` | The currency check uses `row.currency`; persistence uses `row.unit`. | One field; `unit` must be consistent with `currency` (agorot means ILS). | The ×3.6 cost bug returns through the on-device path. |
| med | `tests/verdict_contract.py` | Blind spots listed above. | Scan enum values; ban `dict[str, Any]` and untyped responses on non-allowlisted routes; add more verdict-like words; gate verdicts at a typed `Verdict` object constructible only through `LaunchGate`. | `CIOVerdict`, Telegram buy alerts and `Explanation` dicts land in Phase 2. |
| med | `importer/imageio.py`, `api/imports.py` | No limit on concurrent image decodes; pixel cap ignores bands/mode (3 × 25 MP RGBA → 718 MB, verified). | `Semaphore(1)` around decode; budget by `w*h*bands`; reject CMYK/RGBA over ~12 MP or convert in strips. | Any deploy that enables server reading gets OOM-killed along with the scheduler. |
| med | `config.gemini_model="gemini-2.5-flash"` | **web:** the 2.5 series shuts down "no earlier than 2026-10-16" and new users are steered to 3.5 Flash-Lite. | Model ids in config with a startup "model exists" probe and a fallback list; track in the quota ledger. | The Gemini OCR path and every Phase 2 LLM role would silently drop to templates in two weeks. |
| med | `scheduler/inprocess.py` | `leader_check` shares the **one** worker thread with long jobs, so a lost Postgres lock is noticed only after the job ends (split brain for that long). If the lock file can't be opened (read-only disk), `start()` raises and the scheduler is disabled after one log line. `/api/health` hides scheduler state. | Own thread/executor for the election; expose `leader`, `last_quotes_at`, `last_snapshot_at` in health and alert on staleness. | On Render the API looks healthy while quotes and snapshots stopped. |
| low/med | `Dockerfile.slim` CMD + `prepare_database` | Migrate-on-start plus "refuse if not at head" means rolling back to the previous image crash-loops after a new migration. | Accept a "DB ahead" state for known revisions; expand → contract migrations. | The first bad Phase 2 deploy can't be rolled back on Render. |
| low | `docker-compose.yml` | Option B reuses it: `ENV=dev`, a default `SECRET_KEY`, API on `:8000` with `TRUSTED_PROXY_HEADER` and no CIDR (spoofable from the LAN), docs open. | A separate `compose.prod.yml` or profile. | `deployment.md` Option B says "the same Docker Compose as development". |
| low | various | Expired `session` rows never purged; `CORS_ORIGINS=["*"]` not refused in production; an over-limit JSON body returns 400 not 413; the history `TTLCache` has no size cap; APScheduler `JobLookupError` at shutdown (harmless). | Purge job, production check, size cap. | Hygiene. |

## Memory re-check
- **Reproduced:** `memprobe.py` peaks at **370 MB** against the 400 MB limit (idle 139, 8 logins 254, 3000×6000 upload 335, 25 MP flat RGB 370); the claimed 359 MB is within allocator noise.
- **Not covered by the probe:** RGBA/CMYK images (~332 MB each at 25 MP) and concurrency (**3 concurrent RGBA uploads → 718 MB, verified**; resident stayed at 386 MB afterwards because glibc doesn't return freed memory). In the slim image with no Gemini key, uploads get 503 *before* decoding, so Option A is safe today; setting `GEMINI_API_KEY` makes it unsafe.
- **Postgres (not measured):** psycopg + a 3+2 pool + the leader-lock connection probably adds ~15–30 MB; estimated Option A steady state ~260–280 MB after the first summary, leaving ~120 MB spike headroom.
- **Phase 2 adds** `google-genai` (~16 MB), the committee's report objects, and especially **defeatbeta (DuckDB + Hugging Face downloads)**, which can take hundreds of MB and needs disk: run it in GitHub Actions or a separate job, never in the 512 MB API.

## Phase 2 readiness (what to refactor first)
1. **Money first:** `_record_flow` (currency + deferred flows), confirm's unit/currency mismatch, strict import bodies.
2. **A typed `Explanation`:** today `signals/base.Explanation` has only summary, inputs, rules, and the API exposes it as `dict[str, Any]` (which also blinds the verdict test). Build the CLAUDE.md shape now (summary, per-signal {score, weight, raw}, chart annotations, risk rules, invalidation risks, sources with `as_of`), version it and store it with every scored object.
3. **Provider layer:** add `FundamentalsProvider`, `NewsProvider`, `TranscriptProvider`, `FilingsProvider` protocols returning typed `Field[T](value, source, as_of, missing_reason)`; declare market coverage per provider (defeatbeta and Finnhub are US-only) so TASE gets `confidence=0` by design; persist Yahoo's currency in `Security`.
4. **LLM layer:** an `LLMProvider` protocol; a **DB-backed `LlmUsage` ledger** (provider, model, day, requests, tokens, fallbacks); the validate → retry → template helper; a response cache keyed by input hash; model ids with a startup check; a **personal-data scrubber** (emails, digit runs, names from `User`, account patterns) with its own fixture corpus, called inside the provider so no role can bypass it.
5. **LaunchGate as a type:** a `Verdict` value created only by `LaunchGate.release()`, used by API responses, Telegram, `Notification` and the weekly review; tables `BacktestRun`, `PaperCall` (with model/prompt/weights hashes, append-only).
6. **Scheduler:** own executor and an 8 RPM token bucket (in the DB) for the committee and screener queues; the election in its own thread; scheduler state in `/health`; move heavy jobs (defeatbeta, universe scoring) to GitHub Actions writing to Postgres.
7. **Migrations:** the SQLite foreign-key fix in `env.py` and a "migrate with data" test **before** the first Phase 2 migration.
8. **Exit levels:** move the horizon table (README) into config; ATR, moving-average and R:R inputs need normalised history plus a `price_stale` check: no levels on a cost or screenshot price.

## Suggested features & improvements
- **must:** an append-only public **track record** with the methodology printed on the page: every call (open and closed) measured from its timestamp against S&P 500 / TA-125 total return, wins/losses graded at the horizon, the distribution, and the share of returns from the top few calls. *Why:* it is the launch-gate metric, and Fool and TipRanks win trust by publishing methodology. Sources: https://www.fool.com/services/stock-advisor/ , https://www.stockbrokers.com/review/tools/tipranks
- **must:** factor-grade style "Why?" (value, growth, profitability, momentum, revisions, each A–F within its sector) and clicking a score shows which inputs moved it. *Why:* relative grades are easier to understand than a −100..100 number and map onto the typed `Explanation`. Sources: https://seekingalpha.com/article/4263303-quant-ratings-and-factor-grades-faq , https://www.fxstreet.com/press-releases/6-best-ai-stock-trading-tools-in-2026-ranked-for-what-ai-actually-does-well-202607010926
- **must:** Chandelier / ATR trailing stops (highest high over 22 periods − 3 × ATR as a preset), each with its reason and its "only moves up" history. *Why:* the standard explainable trailing method; fits the horizon table. Source: https://www.tradewink.com/learn/chandelier-exit-atr-trailing-stop-guide
- **must:** look-ahead control in `backend/evals/`: run each scenario both named and **anonymised** (ticker and company masked) and compare the verdict shift; store point-in-time availability timestamps for news, not publication time. *Why:* 2026 research shows LLM agents lean on memorised firm narratives and on misleading news timestamps. Sources: https://arxiv.org/abs/2605.28359v1 , https://arxiv.org/pdf/2601.13770 , https://arxiv.org/html/2605.19337v1
- **must:** a committee reflection/post-mortem step: when a paper call resolves, log what the Bear flagged against what happened (feeds Bear recall metrics). *Why:* TradingAgents and FinRobot separate data, reasoning and thesis layers and use reflection, but their reported backtests (Sharpe 8.2 over 3 months) show why forward-only validation matters. Sources: https://arxiv.org/abs/2412.20138 , https://github.com/AI4Finance-Foundation/FinRobot
- **nice:** a provident and investment-fund tracker from the official **GemelNet dataset on data.gov.il** (CKAN API: returns, fees, assets) instead of scraping Funder/Bizportal. *Why:* free, official, legal; Israeli portfolios hold many funds. Sources: https://github.com/api-evangelist/data-gov-il , https://skills.rest/skill/gemelnet-advisor-kaidanov
- **nice:** a dividend calendar with a 12-month income forecast plus ETF look-through in the X-ray. Source: https://snowball-analytics.com/dividend-tracker
- **nice:** management-change tracking from 8-K item 5.02 via `edgartools` (built-in 9 req/s throttle). Source: https://pypi.org/project/edgartools/
- **nice:** a bull/bear debate limited to 1 round, with its stability measured in evals (TradingAgents' extra rounds cost quota for unclear gain). Source: https://tradingagents-ai.github.io/

## Data-source status
| Source | Status (2026-10, web unless noted) | Impact |
|---|---|---|
| yfinance | Data-center IPs report `YFRateLimitError` on every endpoint for over 48 h (Sep 2026). [issue](https://github.com/TauricResearch/TradingAgents/issues/1425) | Keep the breaker; persist currencies; plan a second free quote source. |
| defeatbeta-api | 0.0.61 (2026-09-19, "US data layout"); US-only; DuckDB/HF based. [PyPI](https://pypi.org/project/defeatbeta-api/) | Run in GitHub Actions, not the 512 MB API. TASE → `confidence=0`. |
| edgartools | 5.58.0 (2026-09-11); default 9 req/s; identity (User-Agent) required. [PyPI](https://pypi.org/project/edgartools/) | Call `set_identity` with a non-personal contact. |
| Finnhub free | 60/min, non-commercial. Free: recommendation trends, company news, filings, insider transactions, earnings calendar. International and detailed financials are paid. [freeapi.watch](https://freeapi.watch/finnhub/) | US news and analysts only. |
| Gemini free | 2.5 Flash: ~10 RPM, 250K TPM, 500–1,500 RPD (sources disagree). **The 2.5 series shuts down no earlier than 2026-10-16**; new projects steered to 3.5 Flash-Lite. [guide](https://aipromptshub.co/limits/gemini-rate-limits-2026), [changelog](https://ai.google.dev/gemini-api/docs/changelog) | Change the `gemini_model` default now; add the quota ledger. |
| Groq free | gpt-oss-120b/20b: 30 RPM, 1K RPD, 8K TPM, 200K TPD. **Reported: Llama removed from the free tier in Sep 2026.** [klymentiev](https://klymentiev.com/blog/groq-pricing), [tokenmix](https://tokenmix.ai/blog/groq-api-access-2026-free-tier-rate-limits) | 8K TPM is too small for a full CIO prompt: compress the reports; model in config. |
| Cloudflare Worker → Render | Cross-zone subrequests carry `CF-Connecting-IP: 2a06:98c0:3600::103`. [CF](https://community.cloudflare.com/t/workers-always-pass-cf-connecting-ip-2a063600-103/189944) | Redesign the client-IP header (high finding above). |
| GemelNet (data.gov.il) | Free CKAN `datastore_search` with fund returns and fees. [ref](https://github.com/api-evangelist/data-gov-il) | Source for fund tracking. |
| exchange_calendars 4.13.2 (local) | XTAE already Mon–Fri with a Friday 13:50 close (verified). | Keep the config override for one-off closures. |

## Risks for Phase 2
1. **P&L correctness:** the verified flow holes and the import NaN/currency gaps would distort exit-level sizing, the weekly review and the track record, and look like model alpha or losses.
2. **The rate limiter fails closed in production:** with the shared Worker IP plus email lockout, one bot can block every signup and any specific user.
3. **Silent LLM degradation:** the Gemini model retirement (~Oct 16), Groq's 8K TPM and Llama leaving the free tier; without a DB-backed ledger and per-role fallback metrics, the "committee" will quietly be templates.
4. **Verdict leakage** through untyped dicts, enum values, Telegram, or a `score` the UI colours green.
5. **Data loss on SQLite migrations** (verified cascade) for Option B.
6. **Memory:** defeatbeta/DuckDB or server-side image decoding inside a 512 MB process; concurrent decodes already reach 718 MB.
7. **Thin TASE coverage:** US-only sources mean the CIO must be capped (e.g. `confidence ≤ coverage share`) and evals must include TASE data-empty cases.
8. **Look-ahead bias in evals** if scenarios use real tickers only.

## Recommended changes to the Phase 2 plan
1. **Phase 2.0 "money and gate" block, before any feature:** flow fixes plus deferred flows; strict import schemas (no NaN, Literal currency, bounded and scrubbed text, verified-only matching, one unit/currency rule); the SQLite migration foreign-key fix with a data test; `Verdict` as a gated type with the hardened contract test; the proxy client-IP redesign (shared secret) and (email, IP) backoff with a known-device exemption.
2. **LLM foundation before the committee:** provider protocol, DB quota ledger, scrubber, structured-output helper, model-id config with a startup check (change the Gemini default now), response caching. Write the template path first and treat it as the default path.
3. **Typed `Explanation` v1** (CLAUDE.md shape) before exit levels or "Why?".
4. **Paper-trading tables in the first Phase 2 PR** (`PaperCall`, `BacktestRun`, hashes for model, prompt and weights): the gate's 4-week clock only starts once calls are recorded.
5. **Heavy data jobs off the API:** defeatbeta, EDGAR and universe scoring as a GitHub Actions cron writing to Postgres; the API only reads.
6. **Evals:** anonymised-ticker variants and point-in-time news timestamps; mock LLM in CI plus a nightly live free-tier run that also measures template-fallback rate and quota.
7. **Operations:** scheduler state in `/health`, a separate election thread, a decode semaphore, a prod compose profile, a rollback-safe migration policy, GitHub Actions pinned by SHA.
8. **Fund tracking:** GemelNet (data.gov.il) for provident funds; dividends from yfinance, cached daily.
