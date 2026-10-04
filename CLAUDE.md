# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

A personal portfolio analysis and recommendation assistant for **US and Israeli (TASE)** stocks. It combines technical/chart analysis, analyst consensus, geopolitical/news signals and market sentiment (Fear & Greed, VIX) into buy/sell/hold suggestions. Every suggestion is filtered through **risk limits the user sets**. The output goes to a web app that refreshes continuously. See `README.md` for the feature spec.

Status: **Phase 1 and 1.5 built; Phase 2.0 (money, gate, foundations) in progress** (see `docs/phase-2.0-spec.md`, `docs/reviews/phase-1.5-rereview-2026-10-03.md`, `docs/phase-1.5-spec.md`, `docs/reviews/phase-2-2026-10-03.md`, `docs/phase-1-spec.md` and README → "Decisions so far"). Follow the layout and conventions below when adding code.

## Stack

- **Backend**: Python 3.12, FastAPI, SQLModel (SQLite now, Postgres later), APScheduler, pandas, in-house indicators (`signals/indicators.py`; don't add pandas-ta, which is abandoned), `yfinance`, `httpx`.
- **Frontend**: Next.js (App Router) + TypeScript + Tailwind, TradingView `lightweight-charts` for price charts, WebSocket for live updates.
- **Tests**: `pytest` (backend), `vitest` (frontend). Lint/format: `ruff`, `mypy`, `eslint`, `prettier`.

## Commands (once scaffolded)

```bash
cd backend && uvicorn app.main:app --reload     # run API
cd backend && pytest                             # tests
cd backend && ruff check . && ruff format . && mypy app
cd frontend && npm run dev | npm test | npm run lint
```

## Architecture rules

1. **Providers are pluggable.** Every external data source implements a provider interface in `backend/app/providers/`. Signal code never calls `yfinance`, `requests` etc. directly. Free providers are the default. Paid ones turn on only when their API key is in the env.
2. **Each signal is independent and explainable.** Each module in `backend/app/signals/` returns `SignalResult(score: float in [-100, 100], confidence: float in [0, 1], reasons: list[str], data_as_of: datetime)`. No signal may produce a score without a human-readable reason.
3. **Scoring and risk are separate.** `scoring/` combines signals with configurable weights. `scoring/risk.py` then applies the user's limits (position size, sector/country exposure, stop-loss, drawdown, volatility cap, blacklist). The risk engine can block or downgrade a recommendation, but it never upgrades one. When it blocks a recommendation, record why so the UI can show it.
4. **Missing data degrades gracefully.** If a signal has no data (e.g. no analyst coverage for an Israeli small cap), it returns `confidence=0` and its weight is spread across the other signals. Never treat missing data as a neutral score of 0 at full confidence.
5. **Alerts fire only on changes.** Re-scoring runs often. Notify only when a recommendation changes or a price rule (stop/target) triggers.
6. **No auto-trading.** The app only suggests. Don't add order-execution code unless the user explicitly asks.

## Market-specific gotchas

- **TASE tickers** use the `.TA` suffix in Yahoo (e.g. `TEVA.TA`, `LUMI.TA`, `NICE.TA`). Yahoo prices them in **agorot (ILA)**. Divide by 100 to get ILS, and always normalize at the provider layer.
- Some Israeli companies are dual-listed on TASE and in the US (e.g. TEVA, NICE, ESLT), while others trade only in the US (e.g. CHKP, WIX). Keep a mapping table so dual listings are never double-counted.
- **Trading hours**: TASE trades Mon–Fri since Jan 2026, roughly 09:59–17:25 Israel time (Friday closes earlier). US trades 09:30–16:00 ET. Use `zoneinfo` and exchange calendars (`exchange_calendars` / `pandas_market_calendars`) and never hard-code UTC offsets. DST changes on different dates in Israel and the US.
- **Currency**: the portfolio is shown in both ILS and USD. Store each transaction in its native currency together with the FX rate on the trade date.
- **Index references**: TA-35 (`TA35.TA`), TA-125 (`^TA125.TA`), S&P 500 (`^GSPC`), NASDAQ (`^IXIC`), VIX (`^VIX`), USD/ILS (`ILS=X`). All verified live on 2026-10-04; re-check if one stops answering, because Yahoo changes them occasionally.
- The CNN Fear & Greed endpoint is **unofficial** and can break. Wrap it, cache it, and fall back to a computed proxy (VIX, put/call, breadth, momentum).

## Conventions

- Type hints everywhere. Use Pydantic/SQLModel for all I/O schemas.
- Cache external calls (TTL per provider) and respect rate limits. Free tiers are tight (Finnhub ~60 req/min).
- Unit tests for every signal must use **fixed fixture data**, not live APIs. Mark live-API tests with `@pytest.mark.live` and skip them by default.
- Thresholds, weights and refresh intervals belong in config (`backend/app/config.py` / user settings in the DB), never inline.
- Secrets live only in `.env`, and `.env` is never committed. Keep `.env.example` up to date.
- Show the "not financial advice" disclaimer in the UI footer and in alert messages.

## Product decisions
Full list in `docs/product-decisions.md` (launch gate, Analyze a stock, committee, ask-my-portfolio, exit levels, weekly review, performance, screener, "Why?", buy alerts, Phase 2.0 review decisions, price-source terms). Hard rules that apply everywhere:

- Holdings come from screenshots; never save OCR output without user confirmation.
- Free AI sees `PublicFacts` only; every LLM call has a template fallback.
- No verdicts until the launch gate opens; no TradingView data; no paid service or auto-trading without explicit approval.
- Never quietly tighten a stop; no default horizon (`Holding.horizon` stays null until the user sets it).
- Every suggestion carries an `Explanation`; every query is scoped to the logged-in user.

## Workflow (user rule; follow it every phase)

1. **Before every new phase**, run a **review agent on Opus** (`Agent` with `model: "opus"`). It must:
   - review all code so far: bugs, missing tests, risk-logic, agorot/FX/timezone, auth scoping, secrets, rate limits
   - **search the web** for comparable apps (TradingView, Finviz, TipRanks, Seeking Alpha, Simply Wall St, Getquin, Delta, Snowball, Ghostfolio, OpenBB, Israeli tools such as Bizportal / Funder) and recent changes in free data APIs
   - return problems, suggested features and improvements, and risks for the next phase, each with a **"Why"** line and a source link
2. Save the report to `docs/reviews/phase-<n>-<YYYY-MM-DD>.md` and show it to the user. The user chooses which suggestions to include. The rest go to `docs/ideas.md`.
3. **Write code with Sonnet agents** (`Agent` with `model: "sonnet"`). The main session plans, verifies (tests, lint, typecheck) and commits.
4. Problems the review found are fixed in the same phase.
5. After every phase, run the whole test suite (backend pytest on SQLite and, when available, Postgres; ruff, mypy; frontend lint, tsc, vitest, build), regenerate `docs/testing.md` with `python scripts/test_inventory.py`, and add tests for anything the phase changed; move items from 'Planned tests' into the suite as they become runnable.

- **Token saving:** agents return at most ~150 words and write details to a file; run tests quietly (failures and totals only, `pytest -q`) and never read `docs/testing.md` back.
- **Week = Sunday to Saturday** (`week_start_day` setting, Asia/Jerusalem); backend and frontend must agree.

## Security rules (user decision; details in `docs/security.md`)

- **Screenshots are never kept.** Store only the stock rows (name, symbol, quantity, price, value, cost, currency). Never store the image, raw OCR text, account numbers or owner names.
- Upload images as a **raw request body**, not multipart, because Starlette spools multipart uploads over 1 MB to disk. Hold bytes in memory only.
- Prefer on-device OCR (in-browser tesseract.js). Never send a half-redacted image to a third party.
- Purge unconfirmed import drafts after 24 hours, and clear a draft's rows once it is confirmed.
- Production must run with secure cookies. Never log bodies, OCR text, tokens, passwords or emails.

- **No TradingView data.** TradingView has no public data API and its terms prohibit scraping and non-display use, so never add `tradingview-ta`, `tvdatafeed` or similar. Only TradingView's official embeddable widgets are allowed, display-only, loaded after a user tap, never fed into scores.
- **AI providers:** free providers first, in the configured order, with templates as the final fallback. A paid provider (Claude API) may exist behind a disabled-by-default setting (`llm_paid_enabled`); never enable or add a paid service without the user's explicit approval of the exact service.

## Host-agnostic deployment (user decision: free, no credit card, host chosen later)

See `docs/deployment.md`. Until a host is chosen:
- All settings come from env vars.
- Database access goes only through SQLModel, so both SQLite (dev) and Postgres (cloud) work. No SQLite-only features outside dev and tests.
- The server image must run in **512 MB**.
- OCR sits behind a provider interface (server Tesseract / Gemini / in-browser tesseract.js).
- The scheduler can run in-process or as a separate process.
- The frontend can be served as static files and call the API at a configurable URL.

## Reminders for the user
`docs/reminders.md` lists things the user said they would provide or decide later (for example more broker screenshots). At each milestone listed there, **remind the user in plain words and ask**, and at the start of every session read that file. Remove an item once it is done.

## When unsure

Ask the user before you change scoring weights, the risk logic, or add a paid data source. Those choices are theirs.
