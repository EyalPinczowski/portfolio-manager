# Handoff for the next session

Read this file, then `CLAUDE.md`, `docs/status.md` and `docs/reminders.md`, before doing anything.

## Branch and rules
- Work only on `claude/stock-portfolio-assistant-jgtbq9`. Never open a PR unless the user asks.
- Opus reviews before each phase. Sonnet agents write code. The main session verifies, commits and pushes.
- Agents return at most ~150 words and write details to `docs/status.md`.
- After each phase: `ruff check`, `ruff format --check`, `mypy app`, `pytest -q`, frontend lint, tsc, vitest, build, e2e. Then run `python scripts/test_inventory.py` (never read `docs/testing.md` back) and add tests for what changed.
- Never commit an agent's in-progress files.
- Commit trailers:
  - `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`
  - `Claude-Session: https://claude.ai/code/session_01JVgwQzNNLx5U7ZzP3nkJfp`

## State
- Committed and pushed through step 4 (committee roles with RAG, ask-my-portfolio tools, 41 mock-LLM evals).
- Backend: 1689 passed, 33 skipped. Frontend vitest: 481 passed. Playwright e2e: 54 tests, last run before the final few commits.
- Alembic is at 0015 and expand-only.
- After an API change: regenerate `backend/openapi.json`, then `cd frontend && npm run gen:api`.

## First thing to do
Check that Yahoo and Stooq are reachable. The user changed the environment's network policy.
```bash
curl -sS -m 15 -o /dev/null -w "%{http_code}\n" -A "Mozilla/5.0" "https://query2.finance.yahoo.com/v8/finance/chart/AAPL?range=5d&interval=1d"
curl -sS -m 15 -o /dev/null -w "%{http_code}\n" -A "Mozilla/5.0" "https://stooq.com/q/d/l/?s=aapl.us&i=d"
```
If both return 200, start the real-history backtest. If you get 403, tell the user the policy is not applied and stop. Do not work around the proxy.

## Backtest rules (decided)
- Targets per 6 months (return % / max drawdown %): conservative 3/4, balanced 5/7, balanced_aggressive 8/10, aggressive 12/15.
- Success means hitting the target return AND beating the benchmark AND staying under the drawdown cap.
- Method: real-history walk-forward. Tune only non-risk parameters and show each change. Report honestly if 80% success is unreachable.
- A technical+patterns-only backtest does NOT open the launch gate.
- Ask before changing scoring weights or risk logic.

## Remaining queue
1. Backtest and tuning on real history (needs the network).
2. Step 5: review the dark-mode screenshots (send the user at most 2–3 that show problems). Add a Playwright pass against the real backend (mock Turnstile, import, null-price flows).
3. Not built from step 4: ask tools `get_analysis` and `get_exit_levels`, chat history, the ask route and UI, the committee endpoint for Analyze.
4. Postgres test run, then an Opus review before deploy, then `docs/reminders.md`, then choose a host and deploy.
5. Optional, not yet accepted by the user:
   - codes so the English-only sentences translate in Hebrew: `skipped[].reason`, candidate notes, scale-out step reasons, fit `rules[].reason`;
   - default portfolio (`default_portfolio_id`), screenshot reminder days (`screenshot_reminder_days`) and the backend app version in `/api/health` (these need new backend fields).

## Awaiting the user's approval (do not treat as approved)
- Paper-resolver config: `paper_resolve_interval_minutes`=60, `paper_resolve_batch_size`=200, `paper_resolve_max_gap_days`=5.
- Committee and ask config: `committee_role_k`, `committee_cio_max_adjustment`, `committee_max_claims`, `ask_max_tools_per_question`, `ask_max_holdings_rows`.

## Approved decisions (keep)
- RAG: chunk 300 tokens with 40 overlap, `rag_max_k` 12.
- Role budgets: company_profile 2500, news 2000, bear 1500, cio 2000, ask_portfolio 2000.
- TTLs: news 14d, transcript 120d, filing 400d, profile 365d.
- Analyze a stock stores only search history and the watchlist.
- Rejected: cash/fee storage and a currency threshold.
- Quiet hours never hold back stop/target alerts.
- The portfolio switcher is hidden when there are fewer than 2 portfolios.
- The settings UI should look like phone settings.
- A `Verdict` can only be created through `LaunchGate.release()`.
