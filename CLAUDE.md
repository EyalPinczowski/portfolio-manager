# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project

A personal portfolio analysis and recommendation assistant for **US and Israeli (TASE)** stocks. It combines technical/chart analysis, analyst consensus, geopolitical/news signals and market sentiment (Fear & Greed, VIX) into buy/sell/hold suggestions. Every suggestion is filtered through **risk limits the user sets**. The output goes to a web app that refreshes continuously. See `README.md` for the feature spec.

Status: **Phase 1 in progress** (see `docs/phase-1-spec.md` and README → "Decisions so far"). Follow the layout and conventions below when adding code.

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
- **Index references**: TA-35 (`TA35.TA`), TA-125 (`^TA125.TA`), S&P 500 (`^GSPC`), NASDAQ (`^IXIC`), VIX (`^VIX`), USD/ILS (`ILS=X`). Verify symbols before relying on them because Yahoo changes them occasionally.
- The CNN Fear & Greed endpoint is **unofficial** and can break. Wrap it, cache it, and fall back to a computed proxy (VIX, put/call, breadth, momentum).

## Conventions

- Type hints everywhere. Use Pydantic/SQLModel for all I/O schemas.
- Cache external calls (TTL per provider) and respect rate limits. Free tiers are tight (Finnhub ~60 req/min).
- Unit tests for every signal must use **fixed fixture data**, not live APIs. Mark live-API tests with `@pytest.mark.live` and skip them by default.
- Thresholds, weights and refresh intervals belong in config (`backend/app/config.py` / user settings in the DB), never inline.
- Secrets live only in `.env`, and `.env` is never committed. Keep `.env.example` up to date.
- Show the "not financial advice" disclaimer in the UI footer and in alert messages.

## Product decisions (from the user)

- Holdings come from **broker screenshots**: Gemini free-tier vision first, Tesseract `heb+eng` as fallback. **Never save OCR output without the user confirming it** in the review screen. Don't send account numbers to free-tier APIs; redact where possible.
- Every LLM call (Gemini/Groq free tier) needs a rule-based template fallback for when quota runs out.
- Multi-user: every query must be scoped to the logged-in user's portfolios.
- Risk profiles: presets Very Conservative → Very Aggressive (default Balanced-Aggressive), each expanding to a `RiskFilter`. They are set per portfolio and can be overridden per holding. The per-holding profile wins.
- Assets: US + TASE stocks, ETFs, crypto. Crypto has no analyst/insider signal, so those return `confidence=0`, and it uses the crypto Fear & Greed index.
- Tax is ignored.
- **Backtest gate**: the API must not return live recommendations until a passing backtest exists for the active weights config.
- **Analyze a stock** (`/analyze/[symbol]`): on-demand analysis of any ticker, plus optional user notes or a question answered by the LLM using the computed signal data.
- **Exit levels** (`scoring/exit_levels.py`): stop-loss / take-profit suggestions. They are computed from a `Horizon` (1w / 1m / 3m / 6m / 1y+) and a `RiskFilter`, which drive the chart timeframe, ATR multiple, moving average and take-profit sources. The horizon table in the README is the spec, and its numbers live in config. Two entry points:
  - single holding: `GET /api/holdings/{id}/exit-levels?horizon=&risk=`
  - full portfolio review: `POST /api/portfolios/{id}/exit-review`, which returns per-holding rows plus portfolio totals (total risk to stops, top contributors, positions with no stop)
- **There is no default horizon.** `Holding.horizon` is nullable. When it is null, the exit-levels endpoints return `needs_horizon` instead of guessing, and the UI asks the user. Never fill it in automatically.
- **Weekly review**: a scheduled job, on by default, Sunday 20:00 `Asia/Jerusalem` (configurable per user), sent via Telegram. Urgent stop/target alerts are still sent immediately.
- **Performance / P&L** (`portfolio/performance.py`): store a daily `PortfolioSnapshot` (value, cash, flows) for each portfolio, starting from its first import. **"Since start" means since the user started using the app** (`Portfolio.tracking_started_at`, the value on that day as the baseline), not since the broker purchase dates. Compute weekly, monthly and since-start P&L as **time-weighted** returns, so deposits and withdrawals are not counted as profit. Split realized and unrealized P&L and compare with `^GSPC` / `^TA125.TA`. Because holdings arrive as screenshots, the import review screen must ask whether a change in quantity or cash was a trade or a deposit/withdrawal.
- Never quietly tighten a stop to fit the risk filter. Suggest a smaller position size instead.
- A trailing stop only moves up (for longs). Every level carries a reason.
- `RiskFilter`: preset + max loss % per position + max % of portfolio per trade + max total portfolio risk + min R:R + stop type. Presets fill in the fields, and users can save named filters.

- **Main page** is My Portfolio (`frontend/app/page.tsx`):
  - live value and P&L
  - weekly/monthly P&L strip
  - three buttons: Review my portfolio / Suggest new stocks / Analyze a stock
  - holdings list, where tapping a holding opens exit levels
- **Screener** (`scoring/screener.py`):
  - Asks amount, horizon, risk filter and markets with **no defaults**.
  - Diversification-aware: never suggest something that breaks the sector/country caps.
  - Reads cached universe scores, which background jobs refresh.
  - Reuses `exit_levels.py` for entry/stop/TP.
- **"Why?" everywhere**: every `SignalResult`, `Recommendation`, `ExitLevel`, `BuyIdea` and review item carries a structured `Explanation`, built at scoring time and stored with it:
  - summary
  - per-signal score, weight and raw data
  - chart annotations
  - risk rules applied
  - invalidation risks
  - sources with timestamps

  No suggestion may ship without one. Telegram messages get a "Why?" inline button.
- **Buy alerts**: push (Telegram + Web Push/VAPID) only when a candidate *newly* becomes a buy that matches the user's saved Buy-alerts filter. Nothing is sent until the user sets that filter. Dedupe per symbol and state, and apply a cooldown, a daily cap and quiet hours.

## Workflow (user rule; follow it every phase)

1. **Before every new phase**, run a **review agent on Opus** (`Agent` with `model: "opus"`). It must:
   - review all code so far: bugs, missing tests, risk-logic, agorot/FX/timezone, auth scoping, secrets, rate limits
   - **search the web** for comparable apps (TradingView, Finviz, TipRanks, Seeking Alpha, Simply Wall St, Getquin, Delta, Snowball, Ghostfolio, OpenBB, Israeli tools such as Bizportal / Funder) and recent changes in free data APIs
   - return problems, suggested features and improvements, and risks for the next phase, each with a **"Why"** line and a source link
2. Save the report to `docs/reviews/phase-<n>-<YYYY-MM-DD>.md` and show it to the user. The user chooses which suggestions to include. The rest go to `docs/ideas.md`.
3. **Write code with Sonnet agents** (`Agent` with `model: "sonnet"`). The main session plans, verifies (tests, lint, typecheck) and commits.
4. Problems the review found are fixed in the same phase.

## When unsure

Ask the user before you change scoring weights, the risk logic, or add a paid data source. Those choices are theirs.
