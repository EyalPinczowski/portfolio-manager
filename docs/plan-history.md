# Plan history (archived 2026-10-04)

Full planning notes from earlier phases, moved out of the live plan file to save tokens. Current state is in `docs/status.md`.

# Plan: Lock in user decisions, then build a backtest-gated MVP

## RESUME NOTE (2026-10-04)
Scheduled resume fired. Next steps once plan mode is exited: read `docs/status.md`; in `backend/` run `ruff check .`, `mypy app`, `pytest` on the ~18 uncommitted 2.0-E files. If green, commit and push to `claude/stock-portfolio-assistant-jgtbq9`; if not, relaunch the Sonnet 2.0-E agent (item by item, tree green after each item, spec in `docs/phase-2.0-spec.md`). Then continue the queue in `docs/status.md`: frontend follow-up (`npm run gen:api`), block 2.1 fallback quotes, exit levels, settings, Investment Committee.

### User feedback on plan exit (2026-10-04)
1. **Analyze a stock** (already planned, Step 3b): user types any ticker and gets a full analysis, an explicit "does it fit my portfolio?" section (max size, sector/country exposure after buying, risk-filter result) and a buy / don't-buy suggestion (gated by the launch gate). Make the portfolio-fit verdict a headline element.
2. **NEW: Portfolio performance post-mortem** ("why didn't I get the return I expected?"): after enough use, a review of the portfolio's history that attributes P&L (per-holding contribution, timing of adds/trims, concentration, FX effect ILS/USD, realized vs unrealized, stops hit, vs benchmark and vs the user's stated expectation). Uses PortfolioSnapshot + transactions; deterministic attribution first, LLM only phrases it from PublicFacts/templates (personal data stays off free LLMs). Needs a user-set expected-return field. Add to Phase 2 queue after exit levels; spec in docs/ideas.md → docs/phase-2-spec as a block.

## Context
The repo has only `README.md` and `CLAUDE.md`, which describe a US + TASE portfolio recommendation assistant. The user answered the design questions. This plan records those answers in the docs and then builds the first working version. The user asked for the scoring to be **backtested before live recommendations are shown**.

## User decisions
| Topic | Decision |
|---|---|
| Platform | Responsive web app that can be installed on a phone (PWA) |
| Alerts | Telegram bot |
| Hosting | ~$5/mo cloud VPS (Docker Compose), running 24/7 |
| Holdings input | **Broker screenshots** (plus manual edit). Free and good at Hebrew → Gemini free-tier vision with a structured-JSON prompt as the primary reader, local Tesseract (`heb+eng`) as the offline fallback, and **always a review/confirm screen** before saving |
| Horizon | Swing (weeks to months) → default weights: technical 30, patterns 15, analysts 25, geo/news 15, sentiment 15 |
| Risk | Profiles: Conservative / Balanced / **Balanced-Aggressive (default)** / Aggressive. Set per **portfolio**, with a **per-stock override** |
| Assets | US + TASE stocks, ETFs (US + TASE tracker funds), crypto |
| Tax | Ignore |
| Analyst signal | Wall Street consensus (yfinance + Finnhub) + **insider trading** (SEC Form 4 via Finnhub/EDGAR; MAYA reports for TASE, best-effort) |
| AI text | Free LLM tier (Gemini free; Groq as fallback) behind an `LLMProvider` interface; rule-based templates when quota runs out |
| Language | Hebrew + English toggle, RTL support (`next-intl`) |
| Portfolios / users | Multiple users (family/friends), each with multiple portfolios plus a combined view; password login |
| Refresh | ~5 min, free data |
| Backtest | **Required before launch**: live recommendations are hidden until a backtest report exists |

Balanced-Aggressive preset: max 15% per position, 35% per sector, defensive mode after a 20% drawdown, stop-loss at 2.5×ATR.

## CURRENT BUILD PLAN (2026-10-03): Phase 1.5 "Hardening", then Phase 2 features
Phase 1 (backend + frontend) is built and committed. The Opus review (`docs/reviews/phase-2-2026-10-03.md`) found money bugs and security gaps. The user's rule: problems found are fixed in the same phase. So the next build step is **Phase 1.5 (hardening)**, using Sonnet agents, followed by a short Opus re-review of that diff, then Phase 2 (features).

**User decisions on the review:**
- Week = **Sunday to Saturday** (a setting, `week_start_day`; the summary returns `week_start`; backend and frontend must agree).
- Login across the Pages site and the Render API = **same-origin proxy** (Cloudflare Pages Function `functions/api/[[path]].ts` forwarding `/api/*` to Render with `CF-Connecting-IP`). No cross-site cookies, no paid domain.
- Features **added**: public **track-record page**, **toggleable X-ray rules**, **fund tracking + dividend calendar**.
- Features **to backlog** (`docs/ideas.md`): thesis notes per holding, alert schedules/quiet hours for valuation alerts, watchlist scans, MCP server (already there).

### PHASE 1.5 IS DONE; NEXT = PHASE 2.0 "money, gate and foundations", THEN PHASE 2 FEATURES (2026-10-03, after the Opus re-review)
Phase 1.5 is committed and pushed (backend 460 tests on SQLite, 472 on Postgres; frontend 118 tests; memory probe 370 MB vs the 400 MB limit). The Opus re-review (`docs/reviews/phase-1.5-rereview-2026-10-03.md`) verified most fixes but found partial fixes and new problems. The user's rule: fix them in the same phase, before features.

**User answers to the re-review:**
- **GemelNet** (data.gov.il, free) for Israeli provident/mutual fund tracking.
- **Cloudflare Turnstile** after repeated failed logins, with per-(email, IP) backoff and a "known device" exemption.
- The four optional suggestions got "no preference": **Chandelier trailing stop** is already inside the exit-levels design (ATR/Chandelier trailing), so it stays; **factor grades A–F, anonymised-ticker AI evals and the committee post-mortem step go to `docs/ideas.md`** unless the user asks for them later. Evals still get TASE data-empty cases.

**Phase 2.0 blocks (Sonnet agents; I verify and commit each; keep the tree green after every item; stop and report on an API error):**
- **2.0-A money correctness (backend):**
  1. `_record_flow`: use the valuation's currency; record a **deferred flow** when an unpriced holding gets its first real price (or exclude unpriced holdings from value and flows). Tests for both verified holes (the +1,666 % case and the AAPL ₪700 cost → USD flow case).
  2. Importer: one unit/currency rule (agorot ⇒ ILS, `currency` and `unit` must agree; the `currency_changed` flag compares the stored field); strict bodies on `ParsedRow`/`ProposedChange`/`ImportRowModel`/`ImportPatch` (`allow_inf_nan=False`, bounds, `currency: Literal["ILS","USD"]`, `tase_number` `^\d{5,9}$`, capped `symbol`, `PATCH rows max_length=200`, rate-limit PATCH, mask digit runs of 6+ in `name` server-side); reject NaN/Infinity in all JSON bodies.
  3. Matching: the index contains **verified securities plus symbols already in this user's portfolios** only; handle GOOG/GOOGL (Hebrew "אלפבית" ambiguity ⇒ candidates, not auto-accept).
  4. Persist Yahoo's currency in `Security` so a restart doesn't blank TASE quotes under a 429; single-ticker retry rotation so junk symbols can't starve real ones.
  5. Image decode: `Semaphore(1)` and a budget by `w*h*bands` (reject CMYK/RGBA over ~12 MP).
  6. SQLite migrations: in `migrations/env.py` turn `PRAGMA foreign_keys` off around batch operations, run `foreign_key_check`, and add a **"migrate with data"** test (the verified cascade data loss).
- **2.0-B auth and gate (backend + the Pages Function):**
  1. Client IP: the Function sends `X-Client-IP` (from the incoming `CF-Connecting-IP`) and `X-Proxy-Auth: <shared secret>`; the API trusts `X-Client-IP` only when the secret matches, and refuses to start with the header trusted and no secret. Update `docs/deployment.md`.
  2. Login backoff keyed on **(email, IP)** with a much higher email-only threshold and a **known-device cookie** exemption; **Cloudflare Turnstile** challenge after N failures (verify server-side, config for the site/secret keys, tests with a mocked verifier; a frontend widget on the login form).
  3. **`Verdict` as a gated type** constructible only through `LaunchGate.release()`; harden `tests/verdict_contract.py` (scan enum values, ban `dict[str, Any]`/untyped responses on non-allowlisted routes, add `rating`, `outlook`, `target`, `bullish`/`bearish`, `trim`, `add`, `strong`, `opinion`, `call`, `signal`); cover Telegram and `Notification.body` paths.
  4. Production refuses `CORS_ORIGINS=["*"]`; over-limit JSON → 413; purge expired sessions; cap the history `TTLCache`; **move `gemini_model` and Groq model ids to config with a startup "model exists" probe and a fallback list** (the 2.5 series shuts down no earlier than 2026-10-16).
  5. Scheduler: the leader election in its own thread; `/api/health` exposes `leader`, `last_quotes_at`, `last_snapshot_at`; a read-only disk for the lock file logs loudly and degrades to "no scheduler" with a health flag.
  6. Rollback-safe migrations (accept a "DB ahead" state for known revisions; expand → contract policy written into `docs/`), `compose.prod.yml` for Option B (no dev defaults, no spoofable proxy header), GitHub Actions pinned by SHA.
- **2.0-C foundations for Phase 2 (backend):**
  1. **Typed `Explanation` v1** (the `CLAUDE.md` shape: summary, per-signal {score, weight, raw}, chart annotations, risk rules, invalidation risks, sources with `as_of`), versioned and stored with each scored object; replaces the `dict[str, Any]` in the API.
  2. **Provider protocols** `FundamentalsProvider`, `NewsProvider`, `TranscriptProvider`, `FilingsProvider` returning typed `Field[T](value, source, as_of, missing_reason)`, with declared market coverage per provider (defeatbeta and Finnhub US-only ⇒ TASE gets `confidence=0` by design).
  3. **LLM foundation:** `LLMProvider` protocol; DB-backed **`LlmUsage` ledger** (provider, model, day, requests, tokens, fallbacks); validate → retry once → template helper; response cache keyed by input hash; a DB-backed token bucket (8 RPM); the **personal-data scrubber** (emails, digit runs, names from `User`, account patterns) with its own fixture corpus, called inside the provider so no role can bypass it; the template path is the default path and is built and tested first.
  4. **Paper-trading tables** `PaperCall` (append-only, with model/prompt/weights hashes) and `BacktestRun`, so the launch gate's 4-week clock can start.
  5. Exit-level inputs: move the README horizon table into config; "no levels on a cost or screenshot price" (`price_stale` check).
- **2.0-D frontend:** Turnstile widget and the known-device flow; types regenerated (`npm run gen:api`); surface `fx_stale`, scheduler-health and Verdict-gate states; tests.
- **Verification for 2.0:** every verified failure in the re-review turned into a regression test; backend pytest on SQLite **and** Postgres, ruff, mypy; frontend lint, tsc, vitest, build; the memprobe stays under 400 MB; a real-backend browser pass (Playwright, as in frontend-B) for the login/Turnstile (mock verifier), import and null-price flows; then a short Opus diff review before Phase 2 features.

**Phase 2 features (after 2.0), in this order:** (1) `exit_levels` (single stock + full review, horizon + RiskFilter, Chandelier/ATR trailing, per-level reasons, no levels on stale prices); (2) the Investment Committee and **Analyze a stock** (`docs/analysis-committee.md`), heavy data jobs (defeatbeta, EDGAR) as a **GitHub Actions cron writing to Postgres**, the API only reads; (3) the **track-record page** (append-only paper calls vs S&P 500 / TA-125, methodology printed); (4) **toggleable X-ray rules** feeding "Why?"; (5) **fund tracking from GemelNet** + the dividend calendar; (6) the evals (`backend/evals/`, mock LLM in CI, TASE data-empty cases, nightly live free-tier run measuring template-fallback rate and quota). Each feature ships with the typed `Explanation`.

### STATUS (2026-10-03, 19:35 UTC): Phase 2.0 is built and pushed; resume with the Opus Phase 2.0 review, then Phase 2 features
- **Done and pushed:** Phase 2.0-A (money), 2.0-B (auth/gate/ops), 2.0-C (foundations), 2.0-D (frontend). Backend 804 tests pass on SQLite (825 on Postgres per the agent), ruff/mypy strict clean; frontend 152 tests, lint, tsc, static build pass. HEAD `3481e48`, working tree clean.
- **Interrupted by a session rate limit (reset 19:30 UTC, now past):** the short Opus review of the Phase 2.0 diff (agent `a40916318ea395deb`) failed with an API 429 before producing a report.
- **Resume steps (the user said "Resume"):**
  1. Relaunch the **Opus Phase 2.0 diff review** with the same prompt as before (verify each 2.0 fix by trying to break it; probe the scrubber, token bucket, cache scoping, prompt-injection isolation, append-only guards, Verdict gate, Turnstile and device cookie; frontend XSS/CSP; Phase 2 readiness and build order; web checks for GemelNet, current Gemini/Groq model ids, Turnstile free limits, edgartools/Finnhub). Ask it to write its findings **incrementally to a file under `/tmp`** so a rate-limit interruption doesn't lose them, and to return the report at the end.
  2. Save the report to `docs/reviews/phase-2.0-diff-2026-10-03.md`, show it to the user with its "Why"/source lines, and fix what it finds (a short 2.0-E fix block with a Sonnet agent) before features.
  3. Then Phase 2 features in the order already in the plan: exit levels → Investment Committee + Analyze a stock → track-record page → toggleable X-ray rules → GemelNet fund tracking + dividend calendar → evals.
- Keep each Sonnet agent's prompt item-by-item with the tree green after every item, and tell it to stop and report on an API error.

### USER DECISIONS (2026-10-03, 19:45 UTC), summary round
- **Deploy after all Phase 2 features** (not earlier). The paper-trading clock therefore starts at that deploy; keep everything host-agnostic until then.
- **TASE data: stay free and show "no data" honestly.** The user asked about pulling data from **TradingView**. Research (WebSearch): TradingView has **no public data API**; its terms prohibit automated collection, scraping and non-display use, and unofficial libraries (`tradingview-ta`, `tvdatafeed`, scrapers) risk breach of terms and account bans. **Decision: do not use TradingView as a data source for scores.** Optional, display-only alternative (nice-to-have, user can veto): embed TradingView's official free **chart / technical-analysis widget** on the Analyze and holding pages for TASE symbols, loaded only after the user taps a "Show TradingView chart" button (no third-party request otherwise), with the required attribution and a CSP exception for `*.tradingview.com`, clearly labelled as TradingView's own view and not part of our score. Our own indicators remain the source of the technical signal.
- **AI: free providers now, a paid option later.** Use several free providers behind the existing `LLMProvider` order (Gemini, Groq, and optionally another free provider such as OpenRouter's free models or Cerebras/Mistral free tiers; verify their current limits and terms in the Opus review), with the templates as the final fallback. Add a **disabled-by-default paid slot** (a Claude API provider adapter, enabled only when `ANTHROPIC_API_KEY` is set and `llm_paid_enabled=true`) so the paid version can be switched on later with no redesign; the quota ledger tracks cost per provider. Never add a paid source without the user's explicit approval of the exact service.
- **Screenshots:** the user will provide 1–2 redacted broker screenshots; ask them to attach them in the chat, with names and account numbers hidden. Use them for the OCR parser fixtures and tuning (Hebrew and English).
- **Not provided / later:** past-profit statements, the Telegram bot and Gemini key, and the hosting accounts (do these at the deploy step; give exact steps then).

### (Done) STATUS (2026-10-03, 14:30 UTC) and how to continue
- **Done and pushed:** backend-A (money correctness), commit `0259e4a`; 260 tests pass, ruff and mypy clean.
- **Interrupted by the session rate limit (limit reset 14:20 UTC; it has now reset):** `backend-B` (security, launch gate, infra) and `frontend-A` (hardening). Both ended with an API 429, not a code error.
  - `frontend-A` left a partial, **uncommitted** change set in `frontend/` (about 53 files, including the Pages Function `functions/api/[[path]].ts`). Its lint, tsc, vitest and build state is unknown.
  - `backend-B` left nothing usable on disk (no backend changes beyond backend-A's commit were found).
- **Continue plan (the user said "Continue"):**
  1. Check the state of the partial `frontend/` work: run `npm run lint`, `npx tsc --noEmit`, `npm test`, `npm run build` there. Do not commit it until it passes.
  2. Relaunch `backend-B` (Sonnet) with the same spec (`docs/phase-1.5-spec.md`, backend-B list), now that the rate limit has reset.
  3. Relaunch `frontend-A` (Sonnet) to **finish** its list from the files already on disk, with the first task being to get lint, tsc, vitest and build green and then the remaining items. Run it in parallel with backend-B (different directories).
  4. Verify each result myself, commit and push each block, then Block D (Postgres, in-process scheduler, memory probe, deployment guide), then the Opus re-review before Phase 2.
- To avoid another rate-limit loss: tell each agent to keep the working tree green after every item (already in the prompts) and to stop and report if it gets an API error.

### Phase 1.5 work blocks (Sonnet agents; I verify and commit each block)
**Block A: money correctness (backend + frontend), each with a regression test**
1. `yfinance_provider.get_quotes`: handle the single-ticker MultiIndex frame.
2. Currency: never store a quote with an unknown currency (fall back to `Security.currency`/market), `to_ils` raises on unknown, handle GBp/ILA; re-validate cached history.
3. TWR: sum transactions in `(prev snapshot date, current date]` instead of the exact date; startup catch-up snapshot; `misfire_grace_time`.
4. `Holding.pnl` nullable in `api.ts` plus the UI renders "—" (fix `HoldingsList.tsx`); also `since_start_date` null (no 01/01/1970), nullable benchmark points, `price_stale`, `tracking_started_at`, scorecard `available`/`nominal_weight`/`disclaimer`.
5. Importer matching: `token_sort_ratio`, margin over the runner-up, length check; auto-accept only exact symbol/TASE number; flag `currency_changed` instead of silently overwriting; screenshot prices are not written to the global `PriceQuote` (store source and stale flag).
6. FX staleness (`fx_stale`), no silent 3.6; Bank of Israel rate as the fallback if cheap, else a clear stale warning.
7. Week start Sunday (config, summary field, Asia/Jerusalem boundary tests, mock aligned).
8. Calendars: `exchange_calendars` (XTAE/XNYS) plus the config override; holiday and half-day tests; one more quote fetch ~15 min after each close; treat empty tickers as rate-limited (circuit breaker + single-ticker retry).
9. NaN-safe volume/OBV (no "nan" in any reason text).

**Block B: security (follows `docs/security.md` and the review's security table)**
1. **Screenshot rule (user request):** upload as a **raw `image/*` body** (not multipart); ASGI body-size middleware on every `/api` route (reject over-limit `Content-Length`, count streamed bytes); bytes only in memory and wiped in `finally`; magic-byte format check (PNG/JPEG/WebP), `Image.MAX_IMAGE_PIXELS ≈ 25e6` with the bomb warning as an error, tall-image downscale; **never send a half-redacted image to a third party**; drop non-stock OCR lines (keep a row only if qty × price ≈ value validates or it matches a security); 24 h purge job for unconfirmed drafts; clear `rows` on confirm; tests: no temp files for a >1 MB upload, draft has only stock fields, no OCR text in logs.
2. Auth: argon2 m = 19 MiB, t = 2, p = 1 with a `Semaphore(2)` and the rate limit before hashing; proxy-aware limiter (trust only `CF-Connecting-IP` from the proxy), per-IP + email backoff, limits on signup and upload, bounded LRU; same response for existing-email signup; atomic invite use (`UPDATE … WHERE used_by IS NULL`); password required for `DELETE /me` and export; sessions list plus revoke-all.
3. Validation: typed `RiskOverride`/`RiskFilter` with `ge/le` and `extra="forbid"` on every PATCH body; symbol regex and provider verification; cap alerts per user; search shows only seeded or verified securities.
4. Logging: set `httpx` logger to WARNING and add a `bot\d+:` redaction filter; handle Gemini `ValidationError` without echoing input.
5. Headers: CSP, `X-Content-Type-Options`, `Referrer-Policy`, HSTS, `Cache-Control: no-store` on `/api/*`; disable `/api/docs` in production; `cookie_secure` defaults True and the app refuses to start in production without it.
6. Frontend: clear the SWR cache and stored portfolio on logout/login; locale-aware number parser in the import review table; one weight formatter.
7. Add every new route to `tests/test_api_scoping.py`.

**Block C: launch gate and contract hygiene**
1. `LaunchGate` service (config thresholds, backtest record, paper metrics) as the single place verdicts are allowed; a contract test that scans every response model for verdict fields, written **before** any `CIOVerdict`.
2. Backend enums as `Literal`; generate TypeScript types from `openapi.json` (`openapi-typescript`); mocks validated against the schema so mock mode can't hide null crashes.
3. `uv.lock`, GitHub Actions CI (ruff, mypy, pytest on SQLite **and** Postgres, eslint, tsc, vitest, pip-audit/npm audit), docker-compose for local dev, images pinned by digest.

**Block D: Option A deployment prerequisites (card-free hosting)**
1. Postgres: add `psycopg`, Alembic migrations, `ondelete` rules, CI on PG16, session-pooler-safe settings (verify the Supabase pooler).
2. In-process scheduler mode with a Postgres advisory lock, catch-up snapshot, `misfire_grace_time`; dedupe state in the DB.
3. **On-device OCR** (tesseract.js, Hebrew + English, client-side redaction) so the screenshot never leaves the phone; the server accepts parsed rows only. Gemini stays an optional, consented upgrade.
4. Static export of the frontend plus the Cloudflare **Pages Function same-origin proxy**; a slim API image without Tesseract; measure RSS in a 512 MB container.
5. `docs/deployment.md` step-by-step setup guide (Cloudflare Pages, Render, Supabase, UptimeRobot) once the above passes.

**Verification for Phase 1.5:** backend pytest on SQLite and Postgres, ruff, mypy; frontend lint, tsc, vitest, build; the new regression tests listed above; the contract test; a memory probe of the API under a simulated 8 concurrent logins and a large image (must stay under 512 MB); a Playwright pass on the mocked frontend (null `pnl` holding renders); then an **Opus re-review of the hardening diff** before Phase 2.

### Phase 2 (features, after the re-review)
As in Steps 3b/3c/3d and the committee design (`docs/analysis-committee.md`): Analyze a stock, Investment Committee, exit levels (one stock + full review), Why? on every suggestion, plus the **three new user-chosen features**:
- **Track-record page:** the paper-trading calls and their hit rate vs the S&P 500 / TA-125; feeds the launch gate.
- **Toggleable X-ray rules:** concentration, currency, country/home-bias and sector rules the user can switch on or off; each breach feeds a "Why?".
- **Fund tracking + dividend calendar:** Israeli mutual/provident funds as manual or priced holdings (prices from the free TASE securities list where available, otherwise manual entry), and an upcoming-dividends calendar (yfinance corporate actions). Best-effort for TASE funds; flag data gaps.
Also: `week_start_day`, defeatbeta used only for US fundamentals (weekly refresh, no `.TA`), TASE names get `confidence=0` where data is missing, per-provider daily LLM quota tracker, personal-data scrubber before every LLM call, Groq model chosen in config (Llama may be gone).

## Process rule: review gate before every phase (user request)
Before starting each new phase (Steps 1, 2, 3, 3b, 3c, 3d, 4, 5, 6), run a **review agent** (the `general-purpose` Agent with web search/fetch) and only then start the phase.

**Model split (user request):**
- review agents run on **Opus**: `Agent(..., model: "opus")`
- code-writing agents run on **Sonnet**: `Agent(..., model: "sonnet")`

So each phase works like this: an Opus review report goes to the user, the user approves items, Sonnet agents implement the phase, and I verify (tests, lint) and commit.

The review agent's tasks:
1. **Code review** of everything built so far: bugs, failing or missing tests, risk-logic errors, agorot/FX/timezone mistakes, security (auth scoping, secrets), and performance or rate-limit problems. Also run the `code-review` skill on the diff since the last phase.
2. **Web comparison**: search the web for current comparable apps and tools, and for what they added recently:
   - TradingView, Finviz, TipRanks, Seeking Alpha, Simply Wall St, Getquin, Delta, Snowball Analytics
   - Israeli: Bizportal, Funder, Globes, TheMarker portfolio tools
   - open-source projects (e.g. Ghostfolio, OpenBB)

   Also search for new or changed free data APIs and for broken endpoints (CNN F&G, Yahoo symbols, Finnhub limits).
3. **Report** to the user before coding the phase:
   - problems found, with fixes (fixed in the same phase)
   - suggested new features and improvements with the source link, each marked must / nice-to-have
   - risks for the upcoming phase

   The user picks which suggestions go in. Unapproved items go to a `docs/ideas.md` backlog.
4. Save each report to `docs/reviews/phase-<n>-<date>.md` so findings are tracked over time.

Also add this rule, including the Opus-review / Sonnet-code split, to `CLAUDE.md` under a new "Workflow" section, so future sessions follow it.

## Rule: a "Why?" on every suggestion (user request)
- **In the app**, every suggestion has a **"Why?"** button that expands a detailed explanation. This covers buy/sell/hold verdicts, new-buy ideas, stop-loss and take-profit levels, trailing raises, position-size cuts, review items and risk-blocked items. The explanation contains:
  1. a plain-language summary (LLM, with a template fallback), in Hebrew or English
  2. each signal's score, weight and the exact data behind it (e.g. "RSI 28 on 2026-10-02", "price 3% above support ₪41.2", "analyst mean target $185, 32 analysts")
  3. the chart with the relevant levels and patterns highlighted
  4. which risk-filter rules were applied or what they changed
  5. the main risks or what would invalidate the suggestion
  6. data sources and timestamps

  Backend: every `SignalResult` / `Recommendation` / `ExitLevel` / `BuyIdea` carries a structured `Explanation` object, built at scoring time and stored with it. Telegram messages include a "Why?" button (inline keyboard) that sends back the same explanation in short form.
- **In my process**, every suggestion in the phase review reports includes a "Why" line: the reasoning plus a source link.

## Push notifications for new buy opportunities (user request)
- A background screener job runs after each universe re-score (Step 3d). When a candidate **newly** becomes a buy that passes the user's saved **"Buy alerts" filter**, the app sends a push:
  - Telegram, plus Web Push to the installed PWA (VAPID, free)
  - content: "🟢 Buy idea: NICE.TA, confidence 78%, entry ₪…, stop ₪…, TP ₪…", with **Why?** and **Analyze** buttons
- The "Buy alerts" filter must be **set by the user once in settings** (consistent with "no defaults, ask"):
  - minimum confidence
  - horizon
  - risk filter
  - markets / asset types
  - max alerts per day
  - quiet hours

  Until it is set, no buy pushes are sent, and the app prompts the user to set it up.
- Dedupe: one push per symbol per state change, with a cooldown so the same idea is not repeated every cycle (consistent with the "alerts only on change" rule).

## Free hosting, revised: NO CREDIT CARD (user constraint, 2026-10-03)
The user declined to give a card, so Oracle, Google Cloud and AWS/Azure are out. Researched card-free options:

| Card-free piece | Free limits | Catch |
|---|---|---|
| **Render free web service** | 512 MB RAM, 0.1 CPU, 750 h/month (= one service 24/7), no card | Sleeps after 15 min with no inbound traffic; ephemeral disk. A free uptime pinger (UptimeRobot, 5-min) keeps it awake. 512 MB is too small for Next.js + Tesseract, so frontend goes elsewhere and OCR changes. |
| **Supabase free Postgres** | 500 MB, no card, 2 projects | Pauses after 1 week with no activity (our 5-min jobs prevent that). Needs SQLite → Postgres. |
| Neon free Postgres | 0.5 GB, 100 compute-h/month, no card | Suspends after 5 min idle, but our scheduler would keep it awake and could burn the 100 h. Supabase fits better. |
| **Cloudflare Pages / Vercel Hobby** | Static/Next hosting, no card | Frontend only. |
| Hugging Face Spaces | 2 vCPU / 16 GB | Docker Spaces need a paid PRO plan to create, disk is ephemeral, sleeps after 48 h. Not viable. |
| Koyeb, Fly.io, Railway | closed to new free accounts | No. |
| Cloudflare Workers + D1 + Cron | free, no card | Would need a rewrite of the backend in TypeScript. No. |

**Honest finding: no card-free cloud gives a real always-on VM.** Two workable designs:

**Option A: card-free cloud stack** (zero hardware)
- Frontend → Cloudflare Pages (static export, locale-prefixed routes).
- API + scheduler in **one process** on Render free, kept awake by UptimeRobot every 5 min.
- DB → Supabase Postgres (migrate SQLModel from SQLite; drop the SQLite WAL pragmas; keep SQLite for tests).
- OCR: **Gemini free only, plus in-browser `tesseract.js`** (Hebrew+English) as the fallback. The image never leaves the phone, which also fixes the privacy problem. Tesseract is dropped from the server image to fit in 512 MB.
- Backups: Supabase's own plus a weekly JSON export to the user's email/Telegram.
- Risks: 512 MB is tight with pandas, so keep the universe screener out of the API process (run it as a GitHub Actions cron, which is free). Render may restart the service, and a pinger is a grey area of Render's terms. *Verify in the Phase 2 review.*

**Option B: home device** (spare PC/laptop or a ~$60 Raspberry Pi 5, always on)
- The same Docker Compose as planned, with SQLite and Tesseract, so nothing in the code changes.
- Reach it from the phone and the family via **Tailscale** (free, no card, private) or **Cloudflare Tunnel** (free, needs a domain). *Verify the card requirement for Cloudflare Zero Trust.*
- Risks: updates stop when the device is off or the home internet is down, and the family needs Tailscale installed.

**Decision rule:** build the app host-agnostic (Docker + env config) so either design works, and develop and test locally in the meantime.

**User decision (2026-10-03): "decide later, build host-agnostic". The user has no always-on home device and no card, so Option A (card-free cloud stack) is the expected landing spot.**

**Next actions after approval (docs and config only; no cloud accounts needed):**
1. `docs/deployment.md`: both designs, the card-free table above, setup steps for Option A (Cloudflare Pages, Render, Supabase, UptimeRobot) and Option B, backups, and what to verify.
2. README "Hosting" row: replace "Oracle Cloud Always Free" with "Free, card-free; host chosen at deploy time (see docs/deployment.md)". Remove Oracle-specific wording.
3. `CLAUDE.md`: add the **host-agnostic rule**:
   - all config via env
   - DB access only through SQLModel, so SQLite and Postgres both work
   - no SQLite-only features outside tests/dev
   - the server image must run in 512 MB
   - OCR runs behind a provider interface (server Tesseract / Gemini / in-browser tesseract.js)
   - the scheduler can run in-process or as a separate process
4. `docs/ideas.md`: add "in-browser OCR (tesseract.js)", "Postgres support + migration tests (Alembic)" and "run the screener as a GitHub Actions cron" as Option-A prerequisites. They enter the plan if Option A is chosen.
5. Then run the **Opus Phase 1 code review** (the review agent also checks Option A feasibility: 512 MB memory profile of the API, Postgres compatibility of the models, Render and UptimeRobot terms, Supabase pause rules).

Superseded Oracle material below is kept only in case the user later accepts a card.

### (Superseded unless the user later accepts a card) Oracle Cloud Always Free
**Recommendation: Oracle Cloud Always Free, Ampere A1 ARM VM, converted to Pay-As-You-Go (stays $0 inside the free limits).**

Requirements the host must meet: always-on (the scheduler refreshes prices every 5 min and sends alerts), persistent disk for SQLite, ~2–4 GB RAM for FastAPI + Next.js + Tesseract, HTTPS, Docker.

| Option | Verdict | Why |
|---|---|---|
| **Oracle Always Free A1** | **Best** | Permanent, no cold starts. Since June 2026 the allowance is **2 OCPU / 12 GB RAM** (halved from 4/24). That is still ~10× what we need. 200 GB block storage, 10 TB/month egress. |
| Google Cloud e2-micro | Backup | Free forever, but **1 GB RAM**, US regions only, 30 GB disk. Too small for the full stack, and would work only with a stripped-down build. |
| Render free | No | Sleeps after 15 min idle, so no 5-min scheduler or alerts. |
| Fly.io / Railway / Koyeb | No | Free tiers closed to new accounts (2024–Feb 2026). |
| AWS / Azure | No | Credits or 12 months only, then they bill. |
| Hugging Face Spaces, Back4app | No | Sleep or restart; no persistent worker. |

**Catches and how we handle them:**
1. **Idle reclaim:** Oracle may reclaim an Always Free VM if, over 7 days, CPU p95 < 20% **and** network < 20% **and** memory < 20% (A1). Our app is mostly idle, so it could qualify. **Fix: upgrade the account to Pay-As-You-Go.** Idle reclaim does not apply there, and nothing is charged while usage stays within the Always Free limits. Set a **$1 budget alert** as a safety net.
2. **Signup:** needs a real credit or debit card (no prepaid or virtual cards) for identity verification; ~$1 temporary hold. The user must do the signup and enable PAYG; I can't do that for them.
3. **"Out of capacity" for A1:** common. Workarounds: retry script (`oracle-cloud-repeater`), request fewer resources first (1 OCPU / 6 GB is plenty), or create an A2.flex then resize to A1.
4. **Home region is permanent** and Always Free A1 only runs in it. IL-Jerusalem-1 has had A1 capacity shortages; Frankfurt is the usual fallback. Latency doesn't matter for a 5-minute refresh cycle.
5. **Backups are on us:** nightly encrypted SQLite backup, with a copy to a second free place (e.g. a private GitHub repo release or Cloudflare R2 free 10 GB). The 2026 policy changes show free tiers can disappear.
6. **Pin the allowance:** deploy at **1–2 OCPU / 6–12 GB**, never above 2/12, because over-limit instances were terminated from Aug 2026.

**Deployment shape (fits Step 2/6):** one A1 VM running Docker Compose: `api`, `scheduler` (separate process, single worker), `web`, `caddy` (automatic HTTPS; needs a domain, free option: DuckDNS or a Cloudflare-managed domain). Images built in GitHub Actions for **arm64** and pulled onto the VM, so we never run `next build` on it.

**To do after approval (write-only, no cloud access needed from me):**
- `docs/deploy-oracle-free.md`: signup, PAYG upgrade, budget alert, VM creation, firewall (80/443 only), Docker install, Compose file, Caddy, backups, update procedure, capacity-retry tips.
- `deploy/docker-compose.prod.yml`, `deploy/Caddyfile`, `deploy/backup.sh`, and a GitHub Actions workflow that builds and pushes arm64 images.
- Update the README "Hosting" row with the 2/12 GB figure and the PAYG note.

## Step 0: Docs update for the latest requests (do first, after approval)
- `README.md`:
  - Add a **"Main page"** section: live portfolio value and P&L, weekly/monthly P&L strip, 3 buttons, tap a holding for exit levels.
  - Add a **"Suggest new stocks"** section (Step 3d).
  - Add "New buy ideas" to the weekly Telegram review contents.
  - Add the "Why?" explanations rule and the buy-opportunity push notifications.
  - Add rows to the decisions table and update the roadmap so step 1 lands the main page shell.
- `CLAUDE.md`: add the "Workflow" review-gate rule above, plus the screener rules:
  - asks amount/horizon/risk with no defaults
  - diversification-aware
  - reads cached scores
  - reuses `exit_levels.py`
- Open question still pending from the user: past P&L history import (broker statements). Don't block on it.

## Step 1: Update docs (small commit)
- `README.md`: replace the generic options with the decisions above. Add sections on the screenshot import flow, crypto (alternative.me Crypto Fear & Greed, `BTC-USD` etc. via yfinance), insider signal, multi-user, Telegram, and backtest gating. Remove the tax roadmap item.
- `CLAUDE.md`: add rules:
  - Never save OCR output without user confirmation.
  - Every LLM call must have a rule-based fallback.
  - Users only see their own portfolios.
  - Recommendations stay hidden until a backtest exists for the current weight config.
  - Free-tier Gemini may use submitted data, so don't send account numbers. Crop or redact before upload where possible.

## Step 2: Scaffold
- `backend/` (FastAPI, SQLModel/SQLite, APScheduler, pandas-ta, yfinance, httpx, pytest, ruff, mypy)
- `frontend/` (Next.js + TS + Tailwind + next-intl + lightweight-charts, PWA manifest)
- `docker-compose.yml`, `.env.example` (`GEMINI_API_KEY`, `GROQ_API_KEY`, `FINNHUB_API_KEY`, `FRED_API_KEY`, `TELEGRAM_BOT_TOKEN`, `SECRET_KEY`)
- CI: GitHub Actions running lint and tests

## Step 3: Core backend (milestone 1)
- `models/`: User, Portfolio (risk profile), Holding (per-stock risk override, asset_type, market, currency), Transaction, Recommendation, BacktestRun, Alert.
- `auth/`: email + password (argon2), cookie session; every portfolio query is filtered by owner.
- `providers/`:
  - `yfinance_provider` (normalizes `.TA` agorot → ILS, and handles crypto and ETFs)
  - `finnhub_provider` (analyst recommendations, price targets, insider transactions)
  - `fng_provider` (CNN, with a VIX-proxy fallback, plus crypto F&G from alternative.me)
  - `gdelt_provider` + `rss_provider` (Globes, Calcalist, TheMarker, Reuters)
  - `fx_provider` (ILS=X)
  - `llm_provider` (Gemini/Groq/templates)
  - `ocr_provider` (Gemini vision → Tesseract fallback)
- `signals/`: technical, patterns, analysts (consensus + insider), geopolitics, sentiment. Each returns `SignalResult(score, confidence, reasons, data_as_of)`. Crypto skips analysts/insider and uses the crypto F&G.
- `scoring/`: weighted combine with redistribution when confidence is missing; `risk.py` applies the profile (portfolio level, then the per-stock override) and can only block or downgrade, recording the reason.
- `import/screenshot.py`: upload → OCR → parsed rows (ticker/name, qty, avg price, currency) → fuzzy-match Hebrew names to TASE tickers via a seeded TASE securities table → return a draft for confirmation.

## Step 3b: Single-stock analysis ("Analyze a stock")
The user requested this. It works on any ticker, owned or not.
- **Input**: a search box that accepts a ticker or company name in English or Hebrew (e.g. `NVDA`, `טבע`, `TEVA.TA`, `BTC`), with autocomplete from the US + TASE securities table. Optional fields:
  - which portfolio to evaluate against
  - the risk profile for this stock (overrides the portfolio's)
  - the user's own input: a thesis or notes, or a free-text question (e.g. "is this a good entry before earnings?")
- **Backend**: `GET /api/analyze/{symbol}?portfolio_id=&profile=` runs all five signals on demand, with caching. `POST /api/analyze/{symbol}/ask` sends the user's question or thesis, plus the computed signal data, to the LLM provider (template fallback when quota runs out).
- **Output page** (`frontend/app/analyze/[symbol]`):
  - Verdict: buy / hold / sell with a confidence level. If the stock is held, add / trim instead.
  - Signal breakdown with the reasons behind each signal.
  - Interactive chart with SMA 20/50/200, Bollinger bands, support/resistance and detected patterns drawn on it, plus RSI/MACD panes.
  - Analyst consensus and price targets, recent insider buys and sells, latest news with sentiment.
  - **Portfolio fit**: the maximum position size the risk rules allow, the sector/country exposure after buying, and a suggested entry, stop-loss (ATR-based) and target.
  - An AI answer to the user's question, saved alongside their notes.
- Actions on the page: add to watchlist, add to portfolio, set a Telegram alert for a price or verdict change. Before launch, the verdict carries the same backtest-gating label as the main feed.
- Telegram: `/analyze TICKER` returns a short version of the analysis.

## Step 3c: Stop-loss / take-profit recommendations (from "My Portfolio")
The user requested this. Tapping a holding on the **My Portfolio** page opens a holding detail page (`frontend/app/portfolio/[portfolioId]/holding/[holdingId]`) with an "Exit levels" panel at the top. The rest of the page reuses the Step 3b analysis.
- **Backend**: `scoring/exit_levels.py` + `GET /api/holdings/{id}/exit-levels`. It computes several candidate levels and recommends one stop-loss and one or two take-profit levels.
  - **Stop-loss candidates**:
    - ATR-based: entry or current price minus k×ATR, with k set by the stock's risk profile (Conservative 1.5, Balanced 2, Bal-Aggressive 2.5, Aggressive 3)
    - just below the nearest support level or swing low
    - below the SMA 50
    - the profile's maximum loss % from the average cost
  - **Trailing stop**: once a position is in profit, suggest raising the stop (ATR or Chandelier trailing) and never move it down. Show "move stop to breakeven" when the price is above cost + 1×ATR.
  - **Take-profit candidates**:
    - the nearest resistance levels
    - the analyst mean and high price targets
    - risk/reward multiples (2R, 3R) from the chosen stop
    - Bollinger/Fibonacci extensions for breakouts
  - **Scale-out plan**: e.g. sell ⅓ at TP1, ⅓ at TP2, and trail the rest. Each level shows its price, the % distance from the current price, the P&L at that level in ILS/USD, the risk/reward ratio, and a one-line reason (e.g. "below support at ₪41.2 + 2×ATR").
  - Context adjustments: tighter stops when Fear & Greed is in Extreme Greed or the geopolitical score is sharply negative, and a warning for upcoming earnings or ex-dividend dates. Crypto uses wider ATR multiples.
- **UI actions**: accept or edit the levels, which saves them to the Holding (`stop_loss`, `take_profit[]`, `trailing`). Saved levels feed the Telegram alerts ("TEVA.TA hit your stop ₪41.0") and get updated automatically when trailing.
- Re-computed on every scheduler cycle. Notify the user only when a suggested level moves meaningfully (e.g. a trailing raise of more than 0.5×ATR).
- Tests: fixture-based tests for each candidate method, profile multipliers, the trailing-never-lowers rule, and agorot handling.

## Step 3d: New-buy suggestions (user request)
- `scoring/screener.py` + `POST /api/portfolios/{id}/buy-ideas`. It scans a universe (S&P 500, NASDAQ-100, TA-125, main US + TASE ETFs, top ~20 crypto), scores each candidate with the same five signals, and keeps only candidates that:
  - pass the risk filter (volatility cap, blacklist, minimum R:R)
  - **improve diversification**: an under-weight sector, country or asset type is a plus; adding to an already-capped exposure is excluded.
- **The app asks, with no defaults** (consistent with the no-default-horizon rule):
  - amount to invest (₪/$)
  - holding period
  - risk filter (preset or saved)
  - markets / asset types to include
- **Output**: the top 5–10 ideas, each with:
  - verdict, confidence and signal reasons
  - suggested size within the amount and the risk rules
  - entry zone, stop-loss and TP1/TP2 for the chosen horizon (reusing `exit_levels.py`)
  - "Analyze" (Step 3b) and "Add to watchlist / portfolio" buttons
- Universe scans run in the background scheduler (nightly + intraday refresh). The button reads from the cached scores, so it responds instantly.
- The weekly Sunday Telegram review gains a **"New buy ideas"** section with the top 3. It uses the user's last-used amount, horizon and filter. If the user hasn't set them yet, it asks them in Telegram instead.

## Step 4: Backtesting (gate before launch)
- `backtest/`: walk-forward over 3–5 years of daily history for a universe (TA-35 + S&P 100 + main ETFs + BTC/ETH), using only the signals that have point-in-time history. Technical, patterns, VIX/F&G history and FRED are reproducible. Analyst, insider and news history are limited, so they are backtested on the available window or reported as "not backtested".
- Metrics: hit rate of buy/sell calls at 5/20/60 days, how often the suggested stop/TP levels were hit first, return vs. benchmark (SPY / TA-125), max drawdown, turnover.
- Report page in the app. The API returns recommendations only when a passing BacktestRun exists for the active weight config.

## Step 5: Frontend

### Main page = My Portfolio (user request)
The landing page after login (`frontend/app/page.tsx`), top to bottom:
1. **Live header**: total value in ₪/$, today's P&L (₪, $, %), and a since-start P&L. Updates live over WebSocket (~5 min data cadence) with a "last updated" timestamp and a market open/closed badge for TASE and the US.
2. **P&L strip**:
   - this week and this month P&L tiles
   - a small weekly bar chart and a monthly bar chart (last 12 weeks / 12 months)
   - a since-start line compared with S&P 500 / TA-125
   - tapping it opens the full Performance page
3. **Three action buttons**:
   - **Review my portfolio** → full portfolio review (Step 3c table and totals)
   - **Suggest new stocks** → new-buy flow (Step 3d), which asks for amount, horizon, risk and markets
   - **Analyze a stock** → Step 3b search
4. **Holdings list**: one row or card per holding showing price, day change, P&L (₪/$/%), weight, current verdict badge, and its stop/TP status (set / hit soon / missing / needs horizon). **Tap a holding** → holding page with the Exit-levels panel (Step 3c), which asks for the horizon if it isn't set yet.
5. A portfolio switcher (each portfolio or combined) and the risk filter chip in the header.
6. A footer disclaimer.

On mobile, the buttons become a sticky bottom bar.

### Other pages
Login, portfolio list and combined view, **Analyze-a-stock search (Step 3b)**, **My Portfolio → tap holding → exit levels (Step 3c)**, a dashboard (ILS/USD, allocation, risk gauges), the recommendations feed (cards with signal breakdown and reasons), a watchlist, market pulse (both F&G meters, VIX, indices, USD/ILS, headlines), the screenshot import with an editable review table, risk settings (portfolio + per-stock), the backtest report, and a He/En toggle with RTL.

## Step 6: Scheduler + alerts
APScheduler jobs as in the README. Telegram bot: `/start` links a user via a one-time code, and alerts are sent only when a recommendation changes or a stop/target is hit.

## Verification
- `pytest` with fixture data for each signal, risk rule, agorot normalization, and the OCR parser (sample Hebrew/English screenshot fixtures; I'll ask the user for 1–2 redacted real screenshots).
- `ruff`, `mypy`, `npm run lint`, `npm test`.
- Run `docker compose up` locally and use Playwright to check: log in, the main page shows live value + weekly/monthly P&L + the 3 buttons, "Suggest new stocks" asks for amount/horizon/risk then lists ideas, analyze a stock (US, TASE in Hebrew, crypto) with a question, open a holding from My Portfolio and accept its stop/TP levels, import a screenshot, confirm holdings, run a backtest, see recommendations, toggle Hebrew RTL.
- Send a manual Telegram test alert.

## Out of scope for now
Tax, auto-trading, real-time paid feeds, native mobile app, Israeli research-house coverage.

## /batch REQUEST (2026-10-04): 6-month simulation across 4 risk sets, tune until >=80% success
User answers: success = target return per profile AND beat benchmark (S&P 500 / TA-125) AND stay under a max-drawdown cap; data = real history, walk-forward; tuning = non-risk parameters only, each change shown to the user before it becomes default, risk-preset limits untouched; if 80% is not honestly reachable, report that.

**Deviations from the /batch template (deliberate):** units are sequential (each needs the previous module), so no parallel worktrees; work stays on `claude/stock-portfolio-assistant-jgtbq9` and no PRs are opened (repo rule: PR only when asked). Sonnet writes code, I verify and commit.

**Preconditions (not built yet):** exit levels (agent a6f8e540 was stopped by plan mode; relaunch with its plan: skip migration 0010, analyst targets optional, `levels|needs_horizon|no_levels`), screener (`scoring/screener.py`), Analyze-a-stock and the post-mortem. The simulation needs exit levels + screener + risk filter, so it is built right after them. Frontend 2.0-E follow-up is finished and must be verified (lint, tsc, vitest, build) and committed first; open question from it: TASE `avg_cost` unit (check backend).

**Findings:** there are 6 risk presets, not 4 (very_conservative..very_aggressive). Proposed 4 for the run: conservative, balanced, balanced_aggressive, aggressive (user to confirm; adding the other two is cheap). No backtest runner, no point-in-time data, `HistoryProvider` has no as-of parameter, `backend/evals/` missing, numeric pass criteria undefined.

**Units (sequential):**
1. Verify + commit frontend 2.0-E follow-up.
2. Relaunch exit levels (backend), verify, commit.
3. Screener `scoring/screener.py` (universe from cached scores, diversification-aware, uses exit_levels).
4. Historical data store: download once to local parquet/CSV (US large caps + ETFs + BTC/ETH + TA-125 names; skip what is unavailable), as-of slicing so no future data leaks; fixtures for tests.
5. `backend/app/backtest/` simulator: day-by-day replay over 6-month windows, starting empty portfolio per profile with a fixed capital; picks come from the screener ("random recommendations" = random sample among passing candidates, seeded, plus the top-ranked variant), sizing from RiskFilter, stops/TP from exit_levels, trailing stops, costs/slippage in config, ILS/USD handling. Metrics: return, max drawdown, benchmark excess, stops hit, hit rate.
6. Experiment harness `python -m app.cli backtest --profile --windows --seed`: many rolling 6-month windows (e.g. 2019-2026), split train (earlier years) / held-out (latest years). Success per run = return >= profile target AND excess vs benchmark > 0 AND drawdown <= cap; targets/caps in config (user to approve the numbers). Output report to `docs/reviews/backtest-<date>.md`.
7. Tuning loop (Sonnet agent, my verification): vary only non-risk parameters (signal weights, ATR multiples, min score, entry filters) on train windows; report held-out success rate per profile; log every change with before/after for user approval. Stop and report if a profile cannot reach 80% on held-out windows.
8. Wire the result into `BacktestRun` via `record_backtest` only for the approved weights config (feeds the launch gate; paper-trading gate still applies).

**Honest caveats to print in the report:** only the technical/patterns signals have history, so the backtest covers those (others "not backtested"); LLM roles are excluded; survivorship bias in the symbol list; an 80% in-sample number means little, the held-out figure is the headline; high return targets for aggressive sets may be unreachable without loosening risk, which we will not do silently.

**Verification:** simulator unit tests on fixed series (stop hit, trailing never lowers, no look-ahead test: results identical when future rows are altered), deterministic seeds, ruff/mypy/pytest, then run the harness and show the table of success rate per profile (train vs held-out).

## RESUME NOTE (2026-10-04 08:36 UTC)
Tree clean at 333a8dd (pushed). Done: 2.0-E, 2.1, exit levels (BE+FE), screener (BE), backtest simulator, Analyze a stock (BE+FE), search history + watchlist, post-mortem (BE+FE). The settings backend agent (afb7d8dc) died on the session rate limit (reset 08:30 UTC, now past) and left nothing on disk: relaunch it with the same spec (UserSettings + migration 0012 + GET/PATCH /api/settings with Buy-alerts filter unset by default, Telegram link flow with fake sender, admin endpoints, weekly-review job honoring per-user settings). Then: frontend Settings screens, frontend 'Suggest new stocks' screen (buy-ideas), members-only track-record page, toggleable X-ray rules, GemelNet funds + dividend calendar, RAG (docs/rag-spec.md), evals, real-history backtest once network domains are allowed (yfinance/stooq) and tuning, Opus review before deploy, reminders (docs/reminders.md).
