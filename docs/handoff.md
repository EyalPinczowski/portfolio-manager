# Handoff for the next session

Read this file, then `CLAUDE.md`, `docs/status.md` and `docs/reminders.md`, before doing anything.

## Branch and rules
- Work only on the branch the session names (last: `ccr-8e00f184-rqshto`). Never open a PR unless the user asks.
- Opus reviews before each phase. Sonnet agents write code. The main session verifies, commits and pushes.
- Agents return at most ~150 words and write details to `docs/status.md`.
- After each phase: `ruff check`, `ruff format --check`, `mypy app`, `pytest -q`, frontend lint, tsc, vitest, build, e2e, `npm run e2e:real` (real backend). Then run `python scripts/test_inventory.py` (never read `docs/testing.md` back) and add tests for what changed.
- Never commit an agent's in-progress files.
- Commit trailers:
  - use the trailers the session's system prompt gives (no model id in repo files).

## State
- The app is named **Holdwise** (green H mark on a rising line, green brand tokens; `frontend/components/Logo.tsx`, `public/icons/*.svg`, `scripts/gen-icons.mjs` rasterises the PWA PNGs).
- Queue item 3 is done: Ask my portfolio (`/ask`, saved conversations, structured `needs_horizon`) and the Investment Committee on the Analyze result.
- Backend: 1724+ passed, 33 skipped. Frontend vitest: 506 passed. Mock e2e (light + dark): 93 passed. `npm run e2e:real`: passed.
- Alembic is at 0016 (ask history) and expand-only.
- After an API change: regenerate `backend/openapi.json`, then `cd frontend && npm run gen:api`.

## First thing to do
Queue item 3 is done. Next is queue item 4 (Postgres run, Opus review, reminders, host and deploy). The backtest is closed: the user accepted the result (`docs/product-decisions.md`, 2026-10-04) and wants no target, cap or stop changes.

## Backtest rules (decided)
- Targets per 6 months (return % / max drawdown %): conservative 3/4, balanced 5/7, balanced_aggressive 8/10, aggressive 12/15.
- Success means hitting the target return AND beating the benchmark AND staying under the drawdown cap.
- Method: real-history walk-forward. Tune only non-risk parameters and show each change. Report honestly if 80% success is unreachable.
- A technical+patterns-only backtest does NOT open the launch gate.
- Ask before changing scoring weights or risk logic.

## Remaining queue
1. ~~Backtest and tuning on real history~~ done; user accepted the result and relies on paper trading.
2. ~~Step 5~~ done (2026-10-04). Was: review the dark-mode screenshots (send the user at most 2–3 that show problems). Add a Playwright pass against the real backend (mock Turnstile, import, null-price flows).
3. ~~Ask route and UI, chat history, ask tools, committee on Analyze~~ done (2026-10-05).
4. Postgres test run, then an Opus review before deploy, then `docs/reminders.md`, then choose a host and deploy.
5. Optional, not yet accepted by the user:
   - codes so the English-only sentences translate in Hebrew: `skipped[].reason`, candidate notes, scale-out step reasons, fit `rules[].reason`;
   - Hebrew answers in Ask/committee templates (they are English-only now);
   - default portfolio (`default_portfolio_id`), screenshot reminder days (`screenshot_reminder_days`) and the backend app version in `/api/health` (these need new backend fields).

## Approved decisions (keep)
- Approved 2026-10-05 ("Approve", reply to the explained list): paper resolver 60 min / 200 per run / 5 days gap; committee `committee_role_k`, CIO cap 15, max 8 claims; run caps changed 2026-10-05 to 5 an hour and 10 a day per user (user: limit committee runs on the free version); Ask 4 tools per question, 30 holdings rows, 500 chars, 100 conversations, 60 questions/hour, 90-day history. Same message: keep the free-tier API and token use low (cache-stable prompts, usage view).
- Free APIs only for now; flag any non-free service before adding it.
- RAG: chunk 300 tokens with 40 overlap, `rag_max_k` 12.
- Role budgets: company_profile 2500, news 2000, bear 1500, cio 2000, ask_portfolio 2000.
- TTLs: news 14d, transcript 120d, filing 400d, profile 365d.
- Analyze a stock stores only search history and the watchlist.
- Rejected: cash/fee storage and a currency threshold.
- Quiet hours never hold back stop/target alerts.
- The portfolio switcher is hidden when there are fewer than 2 portfolios.
- The settings UI should look like phone settings.
- A `Verdict` can only be created through `LaunchGate.release()`.
- App name Holdwise and its logo/colours (approved 2026-10-05 from a preview).
