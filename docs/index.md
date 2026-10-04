# Docs index (read this first; open only the file you need)

Token-saving rule for Claude sessions: do not read whole docs. Grep for a heading or keyword, then read that line range. Delegate wide reads to an agent that returns a short report. Add one line here when a doc or module is added.

| Topic | Where |
|---|---|
| Current status and queue | `docs/status.md` (read only the last ~40 lines) |
| Product rules and decisions | `CLAUDE.md` (grep the section name) |
| Money, gate, foundations spec | `docs/phase-2.0-spec.md` |
| Investment Committee, backtest gate | `docs/analysis-committee.md` |
| RAG design (app) | `docs/rag-spec.md` |
| Settings, Telegram, admin | `docs/settings-spec.md`; code: `backend/app/usersettings.py`, `api/settings.py`, `api/telegram.py` + `telegram_link.py` + `alerts/telegram.py` (sender interface), `api/admin.py`, `alerts/weekly_review.py` (job `weekly_review`) |
| UI decisions | `docs/ui-decisions.md` |
| Security | `docs/security.md` |
| Deployment, migrations | `docs/deployment.md`, `docs/migrations.md` |
| Importer formats | `docs/import-formats.md` |
| Israeli funds (GemelNet) and dividend calendar | `backend/app/providers/gemelnet.py` (+ `dividends.py`, interfaces in `providers/base.py`), `funds.py` (symbol `GEMEL-<id>`, returns), `api/funds.py` (`GET /api/funds/search?q=`, `GET /api/funds/{id}`), `api/dividends.py` + `portfolio/dividends.py` (`GET /api/portfolios/{id}/dividends`), table `fund_holding` (migration 0014); status.md 2026-10-04 |
| Exit-level scale-out plans per risk preset | config `exit_levels_scale_out_plans`; code `backend/app/scoring/exit_levels.py` (`_plan_for`, `ScaleOutPlan`); status.md 2026-10-04 |
| RAG code (chunker, `ChunkIndex` FTS5/tsvector, `Retriever`, prompt builder, `rag-index` job), table `doc_chunk` (migration 0015) | `backend/app/rag/`, `docs/rag-spec.md` "Built"; status.md 2026-10-04 |
| Backlog, reminders | `docs/ideas.md`, `docs/reminders.md` |
| Reviews | `docs/reviews/` (one file per phase) |

Code map: `backend/app/scoring/` (combine, risk, exit_levels, screener, universe), `signals/`, `providers/` (chain, fallback_sources), `portfolio/` (valuation, freshness, performance, postmortem + postmortem_data: deterministic "why not my expected return" report, `GET /api/portfolios/{id}/post-mortem`), `backtest/` (data, simulator, experiment), `analyze/` (Scout, Chartist, portfolio fit, PublicFacts; route `api/analyze.py`), `userlists.py` + `api/lists.py` (search history and watchlist, symbols only; retention in `docs/security.md`), `launchgate.py`, `papertrading.py`, `trackrecord.py` + `api/track_record.py` (members-only `GET /api/track-record`: ended global calls vs benchmarks), `portfolio/xray_rules.py` + `api/xray_rules.py` (toggleable informational X-ray rules, `GET/PATCH /api/portfolios/{id}/xray-rules`, migration 0013), `api/` (routes), `scheduler/`. Frontend: `frontend/components/`, `lib/api.ts`, `lib/mock*.ts`, `messages/{he,en}.json`.
