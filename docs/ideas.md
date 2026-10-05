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
| Optional TradingView chart/technical-analysis **widget** on Analyze and holding pages (TASE symbols), shown only after a tap, display-only, with attribution | user question 2026-10-03 | Gives a second view for TASE stocks without scraping; TradingView forbids using its data in our scores |
| Undo last portfolio update (restore holdings and transactions from the previous HoldingsSnapshot within 7 days) | user request 2026-10-03 (update from screenshots) | A safety net for a wrong confirm; needs a reversible transaction model |
| **Portfolio post-mortem**: after some weeks of use, a review of the portfolio's history explaining why the return differs from the user's expectation (per-holding contribution, add/trim timing, concentration, ILS/USD effect, realized vs unrealized, stops hit, vs benchmark). Deterministic attribution; LLM only phrases it from templates/public facts | user request 2026-10-04 | Needs `PortfolioSnapshot` history and a user-set expected return. **Planned for Phase 2 after exit levels.** |
| Merge News + Bear into one call (4 to 3 cold calls) | [token saving review](reviews/token-saving-2026-10-05.md) | Saves one request and ~1,500 input tokens, but the Bear may anchor on the neutral summary and one validation failure loses both; measure with the evals first |
| Run Bear/CIO lazily (only when "Why?" / "Committee" is opened; screener finalists get News only) | [token saving review](reviews/token-saving-2026-10-05.md) | Likely 30-50% fewer calls, but a UX change (wait on open): a product decision |
| Cheap extractive compression of passages (strip boilerplate, keep sentences with the symbol, a number or a risk word) | [token saving review](reviews/token-saving-2026-10-05.md) | 15-30% fewer passage tokens without a local model (LLMLingua is too big for 512 MB); the grounding check still guards numbers |
| Compact facts block (drop `reasons` for Bear/CIO when indicators carry the same numbers, short keys) | [token saving review](reviews/token-saving-2026-10-05.md) | 50-150 tokens per Bear/CIO call; small |
| Order passages best-first and best-last | [token saving review](reviews/token-saving-2026-10-05.md) | "Lost in the Middle": models use the start and end of the context best |
| Screener batching of several finalists in one prompt (rejected) | [token saving review](reviews/token-saving-2026-10-05.md) | Conflicts with the one-symbol prompt rule (no cross-symbol leakage); Gemini Batch API is not on the free tier |

## From the pre-deploy review (2026-10-05, chosen by the user for later)
- **F1 Import from a broker CSV or PDF export** next to screenshots (no image at all; fits the "screenshots never kept" rule). Source: getquin AI document import.
- **F2 "Why is it moving?"** on a big daily move, built from cached news chunks plus a template (RAG already exists).
- **F4 Score-change alerts on the watchlist**, only on change (rule 5).
- **F6 "Copy portfolio for my AI"**: privacy-safe export of symbols and weights; costs zero free-tier quota.
- Not chosen yet: F3 insider-transaction notices, F5 dividend forecast chart, F7 read-only MCP endpoint, F8 outbound Bizportal/Funder links. Details in `docs/reviews/phase-pre-deploy-2026-10-05.md`.
