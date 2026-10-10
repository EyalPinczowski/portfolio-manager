# Product decisions (moved from CLAUDE.md, 2026-10-04)

The rules here still apply. Read the relevant part before touching that feature.

## Product decisions (from the user)

- Holdings come from **broker screenshots**: Gemini free-tier vision first, Tesseract `heb+eng` as fallback. **Never save OCR output without the user confirming it** in the review screen. Don't send account numbers to free-tier APIs; redact where possible.
- Every LLM call (Gemini/Groq free tier) needs a rule-based template fallback for when quota runs out.
- Multi-user: every query must be scoped to the logged-in user's portfolios.
- Risk profiles: presets Very Conservative → Very Aggressive (default Balanced-Aggressive), each expanding to a `RiskFilter`. They are set per portfolio and can be overridden per holding. The per-holding profile wins.
- Assets: US + TASE stocks, ETFs, crypto. Crypto has no analyst/insider signal, so those return `confidence=0`, and it uses the crypto Fear & Greed index.
- Tax is ignored.
- **Launch gate**: the API must not return live buy/sell verdicts until (a) a passing backtest exists for the active weights config, **and** (b) the paper-trading gate passes: ≥4 weeks with no critical errors, ≥50 calls resolved at 1 month, and beating the S&P 500/TA-125. Thresholds live in config.
- **Analyze a stock** (`/analyze/[symbol]`): on-demand analysis of any ticker, plus optional user notes or a question answered by the LLM using the computed signal data. It uses the **Investment Committee** (`docs/analysis-committee.md`, `backend/app/committee/`):
  - Scout and Chartist are deterministic code; the News analyst, Bear and CIO are LLM roles.
  - Every role hand-off is a Pydantic model, with a JSON response schema, validate → retry once → template fallback.
  - Numbers must be grounded in the input reports. LLMs never calculate indicators.
  - The CIO adjusts the deterministic score by at most ±15 and must answer every Bear risk.
  - Roles also include **Company Profile** (business, segments, management changes, competitors), a code-only **Peer Comparator**, and **earnings-call tone tracking** (cached per quarter).
  - The screener runs the committee only on the top finalists.
- **Ask-my-portfolio chat**: LLM tool-use over **read-only, user-scoped** internal tools only. Answers are grounded and cited; it never trades or edits settings.
  - Evals live in `backend/evals/` (≥15 scenarios × 3–5 trials, with a mock LLM in CI).
  - The LLM layer is validated by **forward paper trading**, not by historical backtests, because LLMs leak future knowledge.
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

## Decisions from the Phase 2.0 review (2026-10-03)
- **Free AI sees public stock data only.** Free-tier providers (Gemini, Groq, OpenRouter) may train on prompts and Israel is not exempt from Gemini's terms. Anything about the user's holdings, amounts, notes or chat uses templates, unless a paid or no-training provider is explicitly enabled by the user. Build prompts from a typed `PublicFacts` object so personal data cannot reach a free provider by construction (the scrubber stays as defence in depth).
- **The track-record page is for logged-in members only** (never public): only calls whose horizon has ended, global calls only, behind auth, with its own contract-test entry.
- **A second free quote source comes before exit levels.** Add a fallback quote provider behind the existing interface (candidates: Stooq for US stocks/ETFs, CoinGecko for crypto; TASE has no free fallback, so TASE keeps the last close, labelled "as of", and `price_is_fresh` stays false there). Exit levels never silently use a stale or cost price.

- **UI decisions** live in `docs/ui-decisions.md` (theme follows the phone, ₪ main currency, bottom tab bar, balanced cards, green/red with signs, a "+" actions menu, a guided first-run setup). Follow them when changing the frontend.

- **Price-source terms (user accepted the grey area, 2026-10-03):** free price APIs are "personal, non-commercial". Safeguards are mandatory: data only behind login, no public data API, no export or redistribution of market data, a footer credit line ("Data: Yahoo Finance, Finnhub, CoinGecko, ECB / Bank of Israel, TASE" as applicable), limits and TTLs in config. Never scrape sites whose terms forbid it (investing.com, Globes, Bizportal, TASE web pages, TradingView).


- **Real-history backtest result accepted (user decision, 2026-10-04):** the technical+patterns walk-forward on real Yahoo history reached 3–9% held-out success per profile against the 80% bar (`docs/reviews/backtest-real-2026-10-04.md`); tuning non-risk parameters did not help. The user chose to keep the targets, drawdown caps and stop rules unchanged. The backtest half of the launch gate stays closed, the screener shows candidates only (no verdicts), and forward paper trading is the real test.

- **Paper resolver and committee/Ask limits approved (user decision, 2026-10-05):** paper resolver every 60 min, 200 calls per run, benchmark gap up to 5 days; committee 5 runs/hour and 10 runs/day per user (lowered by the user's later request to limit free-version runs; every tap counts), 8 claims, CIO nudge at most 15 points; Ask 60 questions/hour, 500 characters, 100 saved conversations, history kept 90 days. The user also asked that free-tier API and token use stay low: prompts are built so repeat runs hit the response cache, and an admin view shows today's AI usage.

- **Deep review is readable (Update 12, 2026-10-06):** the template roles and the launch-gate reasons shown in the committee return stable codes plus params, and the page translates them (en and he); AI-written text stays as written. A template Bear risk never gets a template CIO answer (the template CIO answers nothing and never "accepts" a risk). Low data completeness is one plain line with a percentage ("Only 19% of the checks had data"), not a risk. When profile and news both have no coverage and every role is a template, the panel shows one short card (not enough public information, why, what to do) instead of empty sections; empty profile and news are hidden. One banner says the AI models are not connected (replaces the per-section "Template" chips). Plain names: "Risks to watch", "Reviewer's reply", "Importance n of 5", "Effect on the score" ("The review did not change the score" when 0).

- **Analyst display rule (Update 17, 2026-10-10):** analyst data is shown as the analysts' own neutral distribution (strong buy / buy / hold / sell / strong sell counts as reported), with the **as-of date**, the number of analysts and the data source, plus the low / average / high price target against the current price when targets exist. The app adds **no verdict** of its own from it: the card is titled "Analysts' ratings", says "not the app's view", and never feeds the score (the analyst signal mode is unchanged). No coverage is its own state ("No analyst coverage"), never a neutral bar or a 0. The disclaimer stays on the page. Tests assert the app's own wording around the card has no buy/sell/hold/recommend words in he and en.
