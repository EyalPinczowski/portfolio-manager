# Project status and work queue (kept current for session restarts)

Last updated: 2026-10-03 21:05 UTC. Branch: `claude/stock-portfolio-assistant-jgtbq9`. Read this first after any restart or usage-limit pause, then `docs/reminders.md`, `CLAUDE.md`, `docs/phase-2.0-spec.md`.

## Built and pushed
- Phase 1, 1.5, 2.0-A/B/C/D/F (money correctness, security, gate, foundations, review fixes). Backend 1076 tests (SQLite) / 1109 (Postgres); frontend 193 tests; lint, types, static build clean.
- Frontend: Meitav on-device parser, Explanation fixes, **redesign part 1** (tokens, tab bar, "+" menu, home, cards, first-run guide).

## In progress when this was written
- **backend-2.0-E** (importer): server `meitav_trade` parser (parity with `frontend/tests/fixtures/meitav/`), new flags, matching keys, duplicate merge, update-from-screenshots scope + atomic confirm + `last_screenshot_update_at`, manual-add endpoint. If its files are uncommitted and its tests pass (pytest, ruff, mypy), verify and commit; if it died, relaunch it from `docs/phase-2.0-spec.md` (block 2.0-E).

## Queue (do automatically, in this order; verify each result yourself, commit, push)
1. 2.0-E importer (above), then a frontend step to follow its contract (`npm run gen:api`, new flags in the review table, update-from-screenshots scope UI).
2. Block 2.1: fallback quote providers (Finnhub, FMP/Stooq, CoinGecko, BoI/Frankfurter, TASE Data Hub optional, last close) per `docs/reviews/quote-sources-2026-10-03.md`; key-gated; fixtures only.
3. Exit levels (`scoring/exit_levels.py`, Chandelier/ATR trailing, `StopState`, endpoints, UI on the holding page, "Why?"), with `UserSettings` table.
4. Settings backend + Telegram link flow + admin endpoints (`docs/settings-spec.md`), then the Settings screens (redesign part 2).
5. Investment Committee + Analyze a stock; track-record page (members only); toggleable X-ray rules; GemelNet funds + dividend calendar; evals; optional TradingView widget (ask the user first).
6. Before the deploy: remind the user (see `docs/reminders.md`), run an Opus review, then deploy per `docs/deployment.md`.

## Rules to keep
- Opus for review agents, Sonnet for code agents; failing test first; keep the tree green after each item; commit only verified work; never enable paid services or add accounts without the user.
- Stop only for decisions that are the user's: product choices, paid services, accounts/keys, legal.

## 2026-10-04
- Backend 2.0-E importer verified (1186 tests, ruff/mypy clean) and committed. Next: frontend follow-up (gen:api, new flags, scope UI), then 2.1, exit levels, Analyze a stock (with a headline "fits my portfolio?" section), portfolio post-mortem (user idea), settings, committee.

## 2026-10-04 (exit levels, backend)
- Built `backend/app/scoring/exit_levels.py` (engine, `StopState` ratchet, typed `Explanation` on every level), `backend/app/api/exit_levels.py` (`GET /api/holdings/{id}/exit-levels?horizon=&risk=` plus optional `prior_stop`, `analyst_mean`, `analyst_high`; `POST /api/portfolios/{id}/exit-review` with per-holding rows and totals) and `exit_levels_*` tunables in `Settings`. Result kinds: `levels | needs_horizon | no_levels` (stale price, no history, history/price mismatch). Tests: `tests/test_exit_levels.py`, `tests/test_api_exit_levels.py`, scoping rows in `tests/test_api_scoping.py`; `openapi.json` regenerated.
- Deliberate deferrals: no migration 0010 (no `UserSettings` / saved named filters / persisted `StopState` yet; the caller passes `prior_stop` / `prior_stops`, so the ratchet works without storage); analyst targets are an optional input (no provider feeds them, the source is skipped with a reason); 4h bars are not offered by the history provider (daily bars stand in); `stop_ma_period` is read in daily bars at every horizon; market-context tightening (Extreme Greed, earnings dates) and "accept and save levels" are not built.
- Next: frontend `npm run gen:api` and the exit-levels panel and review table; then Settings backend.

## 2026-10-04 (screener, backend)
- Built `backend/app/scoring/screener.py` (`screen()`), `backend/app/scoring/universe.py` (universe file loader, cached bars, `refresh_universe`), `backend/app/api/buy_ideas.py` (`POST /api/portfolios/{id}/buy-ideas`), universe seed `backend/app/data/universe_seed.txt` (S&P 100 subset, main ETFs, TA-35 subset, BTC/ETH; only symbols present in the security table are used) and the scheduler job `run_universe_score_refresh` (job id `universe_scores`; batched, paced, config-driven `universe_*` / `screener_*` settings). Tests: `tests/test_screener.py`, a scoping row in `tests/test_api_scoping.py`; `openapi.json` regenerated.
- Request path is cache-only (`SignalCache` score card + `bars:<symbol>` rows, `PriceQuote`). All inputs are required (amount + currency, horizon, risk preset, markets, asset types); `exclude_symbols` is optional. Output is neutral candidates (score, confidence, size, entry/stop/take-profits from `exit_levels`, typed `Explanation`) plus a `skipped` list with a code and a reason. The response has no verdict field; it reports `launch_gate_open` / `launch_gate_reasons` only. A real buy/sell-style verdict for ideas still has to go through `LaunchGate.release()`: nothing is built for that yet.
- Needs the user's decision (risk logic is theirs): the per-preset volatility caps (`screener_max_volatility_pct`), the score/confidence floors and the diversification bonus are proposed defaults; the preset has no blacklist field, so the blacklist is the per-request `exclude_symbols`.
- Limit: universe quotes are refreshed only while a market is open (plus the post-close fetch), so outside those windows a candidate's price age decides (`price_fresh_window_minutes`); stale ones are skipped with `stale_price`.
- Next: frontend `npm run gen:api` and the "Suggest new stocks" screen; Buy-alerts filter and push; Telegram "Why?" for ideas.

## 2026-10-04 (token saving, user request)
- Added `docs/index.md` (doc and code map; Claude reads only what it needs via grep and line ranges, wide reads go to agents) and `docs/rag-spec.md` (app RAG: free local retrieval, FTS first, chunks cited with source/as_of, token budget per role, indexing in the Actions cron, public data only for free LLMs). Queue: RAG block comes with Analyze a stock / committee.

## 2026-10-04 (user approval)
- User approved ("Aprove") the proposed 6-month backtest targets/drawdown caps: conservative 3%/6%, balanced 5%/10%, balanced_aggressive 8%/15%, aggressive 12%/22%. Not yet approved (still proposals): screener volatility caps, score/confidence floors, diversification bonus, exit-level take-profit/scale-out defaults, and whether a technical+patterns-only backtest may open the launch gate. `BACKTEST_TARGETS_APPROVED` stays an env setting for the deployer to set; recording is still refused on synthetic data.
- User asked to lower the drawdown caps (2026-10-04): now conservative 4%, balanced 7%, balanced_aggressive 10%, aggressive 15% (return targets unchanged: 3/5/8/12% per 6 months). Lower caps make the 80% bar harder; if a profile cannot reach it honestly the report will say so.
- User approved the new drawdown caps (2026-10-04): conservative 4%, balanced 7%, balanced_aggressive 10%, aggressive 15%; return targets 3/5/8/12% per 6 months. Targets and caps are now both approved. `BACKTEST_TARGETS_APPROVED` is still an env flag for the deployer; recording stays refused on synthetic data.
