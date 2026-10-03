# Ideas backlog

These are suggestions from the phase reviews that the user didn't pick yet. Each one links to the review that raised it.

| Idea | From | Why it was suggested |
|---|---|---|
| Full Israeli data-correctness bundle: TASE Data Hub securities master, manual/cash holdings, Bank of Israel representative FX, splits/dividends | [phase 1](reviews/phase-1-2026-10-03.md) | Without it, broker totals for funds, bonds, מק"מ and cash won't match, and splits break P&L and stops |
| Forward-test log (store every score with its price from day 1) | [phase 1](reviews/phase-1-2026-10-03.md) | Gives the Phase 6 backtest real out-of-sample data |
| Broker Excel/CSV import (IBI, Meitav, Excellence, IBKR) | [phase 1](reviews/phase-1-2026-10-03.md) | More accurate than OCR |
| Money-weighted return (MWR/IRR) next to TWR | [phase 1](reviews/phase-1-2026-10-03.md) | Getquin shows both; TWR alone confuses users who add money often |
| Lawyer opinion before inviting people outside the family | [phase 1](reviews/phase-1-2026-10-03.md) | Investment Advice Law (1995) and the ISA directive on algorithmic advice |
| MCP server exposing our portfolio + providers (ask Claude Desktop about your portfolio) | [committee design](analysis-committee.md) | User-shared article: agents built on ready-made data tools instead of hand-written API integrations |
| In-browser OCR (tesseract.js, Hebrew + English) with client-side redaction | [deployment](deployment.md) | Option A prerequisite: no Tesseract on a 512 MB server, and screenshots never leave the phone |
| Postgres support + Alembic migrations + tests on both databases | [deployment](deployment.md) | Option A prerequisite: Render's disk is ephemeral, so the data lives in Supabase |
| Run the universe screener as a GitHub Actions cron | [deployment](deployment.md) | Option A: keeps the API process under 512 MB |
| Static export of the frontend + configurable API URL (CORS/cookies) | [deployment](deployment.md) | Option A: the site is on Cloudflare Pages, the API on Render |
| Thesis notes per holding, with alerts when data contradicts the thesis | [phase 2 review](reviews/phase-2-2026-10-03.md) | Simply Wall St Narratives, Finviz Portfolio Notes; a home for the Analyze "notes" and the Bear's invalidation points |
| Alert schedules, quiet hours and valuation-metric alerts | [phase 2 review](reviews/phase-2-2026-10-03.md) | TradingView 2026 alert features |
| Watchlist scans on our own indicators | [phase 2 review](reviews/phase-2-2026-10-03.md) | TradingView Pine Screener |
| Factor grades A–F in "Why?" (value, growth, profitability, momentum, revisions, graded within sector) | [phase 1.5 re-review](reviews/phase-1.5-rereview-2026-10-03.md) | Seeking Alpha factor grades are easier to read than a -100..100 number; maps onto the typed Explanation |
| Anonymised-ticker variants of every AI eval scenario, and point-in-time news availability timestamps | [phase 1.5 re-review](reviews/phase-1.5-rereview-2026-10-03.md) | 2026 research shows LLM agents lean on memorised firm narratives (look-ahead leakage) |
| Committee post-mortem: log what the Bear flagged vs what happened when a paper call resolves | [phase 1.5 re-review](reviews/phase-1.5-rereview-2026-10-03.md) | Measures Bear recall; TradingAgents/FinRobot use reflection |
| Management-change tracking from 8-K item 5.02 (edgartools) | [phase 1.5 re-review](reviews/phase-1.5-rereview-2026-10-03.md) | Already part of the Company Profile role; this is the data source |
| One-round bull/bear debate, with stability measured in evals | [phase 1.5 re-review](reviews/phase-1.5-rereview-2026-10-03.md) | TradingAgents' extra rounds cost quota for unclear gain |
