# Docs index (read this first; open only the file you need)

Token-saving rule for Claude sessions: do not read whole docs. Grep for a heading or keyword, then read that line range. Delegate wide reads to an agent that returns a short report. Add one line here when a doc or module is added.

| Topic | Where |
|---|---|
| Current status and queue | `docs/status.md` (read only the last ~40 lines) |
| Product rules and decisions | `CLAUDE.md` (grep the section name) |
| Money, gate, foundations spec | `docs/phase-2.0-spec.md` |
| Investment Committee, backtest gate | `docs/analysis-committee.md` |
| RAG design (app) | `docs/rag-spec.md` |
| Settings, Telegram, admin | `docs/settings-spec.md` |
| UI decisions | `docs/ui-decisions.md` |
| Security | `docs/security.md` |
| Deployment, migrations | `docs/deployment.md`, `docs/migrations.md` |
| Importer formats | `docs/import-formats.md` |
| Backlog, reminders | `docs/ideas.md`, `docs/reminders.md` |
| Reviews | `docs/reviews/` (one file per phase) |

Code map: `backend/app/scoring/` (combine, risk, exit_levels, screener, universe), `signals/`, `providers/` (chain, fallback_sources), `portfolio/` (valuation, freshness, performance, postmortem + postmortem_data: deterministic "why not my expected return" report, `GET /api/portfolios/{id}/post-mortem`), `backtest/` (data, simulator, experiment), `analyze/` (Scout, Chartist, portfolio fit, PublicFacts; route `api/analyze.py`), `userlists.py` + `api/lists.py` (search history and watchlist, symbols only; retention in `docs/security.md`), `launchgate.py`, `papertrading.py`, `api/` (routes), `scheduler/`. Frontend: `frontend/components/`, `lib/api.ts`, `lib/mock*.ts`, `messages/{he,en}.json`.
