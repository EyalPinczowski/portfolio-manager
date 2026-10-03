# Phase 1 spec: Core + main page shell

The user approved this on 2026-10-03, after the [Phase 1 review](reviews/phase-1-2026-10-03.md).

## The user's choices from the review
- **In:**
  - the screenshot P&L model
  - privacy + security
  - simple price alerts
  - Portfolio X-ray
  - score card + sector heat map
  - free MAYA scraping for TASE insiders (Phase 3, best-effort)
  - **free hosting** (Oracle Cloud Always Free, see `docs/deploy-oracle-free.md`)
- **Not picked (backlog in `docs/ideas.md`):**
  - the full Israeli data-correctness bundle: Data Hub master, manual/cash holdings, Bank of Israel FX, splits/dividends
  - the "scores-until-backtest" forward-test log
  - broker CSV import
  - MWR/IRR

  The basics that Phase 1 needs anyway stay in: agorot normalization by the Yahoo `currency` field, and a small seeded securities table for Hebrew-name matching.
- **Backtest gate stays** (an earlier user decision): the API never returns buy/sell **verdicts** before a passing backtest. Phase 1 shows **scores** in the score card and **no verdict column**.
- **pandas-ta is abandoned upstream.** Indicators are implemented in-house in `signals/indicators.py` (pandas/numpy only). This keeps the install light on ARM.

## Stack
- Backend: Python 3.12, uv, FastAPI, SQLModel + SQLite (WAL, busy_timeout), pydantic-settings, APScheduler (a **separate process**: `python -m app.scheduler`), httpx, yfinance, pandas, numpy, argon2-cffi, itsdangerous, pytesseract + Pillow, google-genai (optional), pytest, ruff, mypy.
- Frontend: Next.js (App Router) + TypeScript + Tailwind + next-intl (he default RTL / en), SWR polling every 60 s, `lightweight-charts`, PWA manifest + offline shell (no push yet), vitest, eslint.
- Infra: Dockerfiles, docker-compose (`api`, `scheduler`, `web`, `caddy`), GitHub Actions CI that builds multi-arch (amd64 + arm64) images.

## Backend layout
```
backend/app/
  main.py config.py db.py deps.py
  auth/        # invite-only signup, argon2id, cookie session, CSRF, rate limit, disclaimer, export/delete
  models/      # SQLModel tables
  providers/   # base.py (Protocols), yfinance_provider.py, fx_provider.py, ocr/ (gemini.py, tesseract.py), cache.py
  importer/    # redact.py, parse.py, match.py, diff.py, service.py
  signals/     # base.py (SignalResult, Explanation), indicators.py, technical.py, patterns.py
  scoring/     # combine.py (weights + confidence redistribution), risk.py (RiskFilter, presets, exposure)
  portfolio/   # valuation.py, performance.py (TWR), xray.py, heatmap.py
  alerts/      # price_alerts.py, telegram.py (send only if TELEGRAM_BOT_TOKEN is set and the user is linked)
  scheduler/   # __main__.py, jobs.py, calendars.py (TASE override from config)
  api/         # routers
  data/securities_seed.csv   # ~150 rows: TASE majors (Hebrew + English names, security number, yahoo symbol, sector, dual_listing_group) + US megacaps + main ETFs + top crypto
backend/tests/   # fixtures only; mark live tests with @pytest.mark.live (skipped by default)
```

## Data model (the important fields)
- `User`: id, email, password_hash, locale, disclaimer_accepted_at, ocr_consent_at, telegram_chat_id?, is_admin
- `Invite`: code, created_by, used_by?, expires_at. The admin creates invites with the CLI `python -m app.cli create-invite`.
- `Session`: id (random), user_id, csrf_token, expires_at
- `Security`: symbol (yahoo), name_en, name_he, tase_number?, asset_type (stock|etf|crypto|fund|bond|cash), market (US|TASE|CRYPTO), currency, sector, country, dual_listing_group?
- `Portfolio`: id, owner_id, name, base_currency, risk_filter (JSON), created_at
- `Holding`: id, portfolio_id, symbol, quantity, avg_cost, cost_currency (as shown by the broker), horizon (nullable: 1w|1m|3m|6m|1y), risk_override (JSON, nullable)
- `HoldingsSnapshot`: id, portfolio_id, taken_at, source (screenshot|manual), rows (JSON)
- `ImportDraft`: id, portfolio_id, rows (JSON, with per-row validation flags), proposed_changes (JSON), status. The image is deleted after parsing.
- `Transaction`: id, portfolio_id, symbol?, type (buy|sell|deposit|withdrawal), quantity?, price?, amount, currency, date, inferred (bool)
- `PortfolioSnapshot`: portfolio_id, date, value_ils, value_usd, net_flow_ils. Taken daily at 23:59 Asia/Jerusalem.
- `PriceQuote` cache: symbol, price (normalized), currency, change_pct, as_of
- `PriceAlert`: id, user_id, symbol, op (above|below), price, active, triggered_at?
- `Notification`: id, user_id, kind, title, body, created_at, read

## Core rules
- **Agorot**: if Yahoo `currency == "ILA"`, divide prices by 100 and report ILS. Indices are in points; never divide them. Decide by the currency field, never by the `.TA` suffix.
- **Owner scoping**: every portfolio, holding, alert or draft query goes through a repository function that takes `user_id`. Tests prove user B gets 404 on user A's resources.
- **Explainability**: every `SignalResult` has `score` [-100, 100], `confidence` [0, 1], `reasons: list[str]`, `data_as_of` and `explanation: Explanation`. `Explanation` has summary, inputs (name → value) and rules_applied. Missing data gives `confidence=0`.
- **Combine**: the weights are in config. Signals with confidence 0 get their weight redistributed.
- **Risk presets**: very_conservative, conservative, balanced, balanced_aggressive (default), aggressive, very_aggressive. Each sets max_position_pct, max_sector_pct, max_country_pct, max_loss_per_position_pct, max_portfolio_risk_per_trade_pct, max_total_portfolio_risk_pct, min_rr, stop_type, drawdown_defensive_pct. Phase 1 computes the exposures and limit breaches only.
- **TWR**: daily-chained, with flows treated as happening at the start of the day. Weekly, monthly and since-first-import figures come from `PortfolioSnapshot` and inferred `Transaction`s.
- **Screenshot pipeline**:
  1. upload (requires consent)
  2. local redaction: Tesseract word boxes, then blur runs of ≥6 digits and the top ~12% header (configurable)
  3. Gemini vision with a JSON schema if `GEMINI_API_KEY` is set, otherwise Tesseract `heb+eng`
  4. parse the rows (name, symbol?, quantity, price, value, cost, currency, unit agorot/ILS)
  5. validate: qty × price ≈ value within 2%, otherwise flag the row
  6. match against `Security` (exact symbol / tase_number / fuzzy Hebrew or English name via rapidfuzz)
  7. diff against the last snapshot, proposing buy/sell or deposit/withdrawal changes for the user to confirm
  8. delete the image

  On confirm: write the Holdings, a `HoldingsSnapshot` and the `Transaction`s.
- **Scheduler jobs**:
  - quotes every 5 min, one batched `yf.download` call for held + watchlist symbols. US/TASE symbols only while their market is open (calendar override in config); crypto always.
  - daily snapshot at 23:59 Asia/Jerusalem
  - price-alert check after each quotes cycle
  - exponential backoff on 429 responses, and stale cache with `as_of`

## API contract (all under `/api`; JSON; cookie auth; CSRF header `X-CSRF-Token` on mutations)
| Method | Path | Notes |
|---|---|---|
| POST | /auth/signup | {invite_code, email, password, accept_disclaimer: true, locale} |
| POST | /auth/login · /auth/logout | sets/clears the cookie; login is rate-limited |
| GET | /auth/me | {id, email, locale, disclaimer_accepted, ocr_consent, csrf_token} |
| POST | /auth/consent/ocr | |
| GET | /me/export · DELETE /me | data export (JSON) / account deletion |
| GET/POST | /portfolios | list / create {name, base_currency} |
| GET/PATCH/DELETE | /portfolios/{id} | PATCH can include risk_filter |
| GET | /portfolios/{id}/summary | {value:{ils,usd}, day_pnl:{ils,usd,pct}, week_pnl, month_pnl, since_start_pnl, weekly_bars:[{week_start, pnl_ils, pct}], monthly_bars:[{month, pnl_ils, pct}], since_start_series:[{date, pct, sp500_pct, ta125_pct}], as_of, markets:{US:{open}, TASE:{open}, CRYPTO:{open:true}}} |
| GET | /portfolios/combined/summary | same shape, across all of the user's portfolios |
| GET | /portfolios/{id}/holdings | [{id, symbol, name_en, name_he, asset_type, market, quantity, price, currency, day_change_pct, value_ils, pnl:{ils,usd,pct}, weight_pct, horizon, stop_tp_status: "missing"|"needs_horizon", score_card:{total, technical, patterns, confidence}}] |
| POST/PATCH/DELETE | /portfolios/{id}/holdings[/{hid}] | manual add/edit (including horizon) |
| GET | /portfolios/{id}/xray | {concentration:[...], currency_exposure, country_exposure, sector_exposure, home_bias, breaches:[{rule, value, limit, why}]} |
| GET | /portfolios/{id}/heatmap | [{symbol, sector, weight_pct, day_change_pct}] |
| GET | /risk/presets | |
| POST | /portfolios/{id}/imports | multipart image → ImportDraft |
| GET/PATCH | /imports/{draft_id} | view/edit rows and change types |
| POST | /imports/{draft_id}/confirm | |
| GET | /securities/search?q= | Hebrew/English/symbol |
| GET | /holdings/{hid}/scorecard | score card with signal breakdown + explanation (no verdict) |
| GET/POST/DELETE | /alerts[/{id}] | price alerts |
| GET | /notifications · POST /notifications/{id}/read | |

## Frontend pages
- `/[locale]/login` and `/[locale]/signup` (invite code + disclaimer checkbox)
- `/[locale]`: **main page**
  - live header: value ₪/$, day / since-start P&L, last updated time, market badges
  - P&L strip: week/month tiles, weekly + monthly bar charts, since-start line vs S&P 500/TA-125
  - three buttons: Review my portfolio / Suggest new stocks / Analyze a stock. Disabled, with a "coming soon" tooltip.
  - holdings list; tapping a holding goes to the holding page
  - portfolio switcher (each portfolio + combined), sticky bottom button bar on mobile
  - footer disclaimer
- `/[locale]/holding/[id]`:
  - score card (score per signal, "not yet validated" label)
  - "Why?" expander showing the Explanation
  - horizon picker (asks if not set)
  - price-alert form
  - Exit-levels placeholder ("coming in Phase 2")
- `/[locale]/import`: upload (consent prompt at first use), editable review table (flagged rows highlighted), change-type pickers, confirm
- `/[locale]/xray` (X-ray + sector heat map) and `/[locale]/settings` (risk filter presets/fields, language, export/delete account)
- Every user-facing string goes through next-intl, in both `he.json` and `en.json`.
