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
