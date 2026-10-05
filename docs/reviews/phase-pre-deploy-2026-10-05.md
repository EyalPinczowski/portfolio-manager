# Pre-deploy review (2026-10-05)

Branch `ccr-8e00f184-rqshto`, HEAD `e9100b2`. Read-only review; no code changed. Uncommitted importer files (`backend/app/importer/*`) were ignored.

**Verified here:** backend `pytest -q` on SQLite: **1765 passed, 33 skipped** (one `PytestUnhandledThreadExceptionWarning`, see L9).
**Not verified here:** the Postgres suite (starting a scratch cluster was blocked in this sandbox), the Docker image (no Docker daemon), the frontend suite.

Counts: **Blockers 5 · High 4 · Medium 8 · Low 11** · features 8 · passes 9.

---

## Blockers (fix before the first real deploy)

### B1. The default Gemini model may not work for a new key, and it shuts down soon
`config.py` defaults to `gemini_model = "gemini-2.5-flash"`, and the code comment already notes that the 2.5 series shuts down "no earlier than 2026-10-16". On 2026-09-18 Google also limited 2.5 access to "users who have actively used them in the past". A key made at deploy time may therefore list the model but fail on calls. The startup probe only checks `models.list()`, so it would not notice. `gemini-3.5-flash-lite` and `gemini-3.6-flash` are GA (2026-07-21), and `gemini-3.8-flash` is out (2026-09-02).
- Fix: set the default to a 3.x model (ask the user which one, since the model is a config choice). Also make the probe do one tiny `generateContent` instead of only listing models.
- Also: `docs/deployment.md` step 3 says "Leave `GEMINI_API_KEY` unset", which turns the committee's Gemini path off. Decide whether the key is set for LLM use; the OCR path already refuses to send unredacted images. Then fix the doc.
- Why: without this, every committee role silently falls back to Groq or the template, and from about 2026-10-16 the configured model returns errors.
- Source: https://ai.google.dev/gemini-api/docs/changelog

### B2. The slim image has never been built or measured in a container
`docs/deployment.md` → "Not verified here: the Docker image was never built". CI (`.github/workflows/ci.yml`) has no Docker job. `memprobe.py` ran in a plain process, not in a 512 MB cgroup. Since that measurement, Ask, the committee (four sequential LLM calls), RAG and the token ledger were all added.
- Fix: add a CI job that runs `docker build -f backend/Dockerfile.slim backend`, then `docker run --memory=512m` with migrate, `/api/health`, and `memprobe.py` plus a committee/Ask run using stub providers.
- Why: Render kills the instance when it goes over 512 MB. A build failure (uv export, hatchling, the non-root user) would only show up on Render.
- Source: https://render.com/articles/platforms-with-a-real-free-tier-for-developers-in-2026

### B3. Postgres has not been run since 0015–0017, and CI does not run on this branch
- CI triggers only on `main` and `claude/**`. This branch is `ccr-*`, so neither the SQLite job nor the Postgres job has run on the last commits.
- `docs/status.md:94` lists the Postgres RAG path (`PostgresIndex`, GIN, `to_tsquery`) as UNVERIFIED. The ask-history (0016) and ledger (0017) migrations have only been tested on SQLite in this session.
- Fix: run `DATABASE_URL=postgresql+psycopg://… pytest -q` locally (handoff queue item 4) or push to a branch CI covers. Add `ccr-**` to the CI trigger.
- Why: production is Supabase Postgres. A dialect bug in the FTS index or in `UPDATE … SET col = col + n` would surface on the first committee run.
- Source: `.github/workflows/ci.yml` lines 3–6; `docs/status.md`

### B4. The committee's daily cap is in memory, and committee LLM calls bypass the per-user DB quota
- `committee_daily_limiter` is a `CountLimiter` (process memory). Every Render restart or redeploy resets the "10 a day" cap. On the free tier, restarts happen on deploy, on crash and when the instance spins down.
- `run_committee(...)` (`committee/roles.py:445`) takes no `user_id`, so `_admit` skips both `llm_user_daily_budget` (DB-backed, 60/day) and the per-user RPM bucket for committee calls. The only per-user guard on 4 LLM calls per run is the in-memory limiter.
- Fix: keep the daily count in the ledger (`quota_add("committee:user:N")`, check-then-add in one transaction like `record_usage`). Pass `user_id` (no names, the prompt is public) so the per-user LLM budget and RPM apply.
- Why: this cap was a user decision on 2026-10-05 ("limit committee runs on the free version"). The free Gemini and Groq quotas are shared by everyone.
- Source: `app/api/analyze.py:293-301`; `app/llm/structured.py:112-138`

### B5. Yahoo blocks or rate-limits data-center IPs, so pick the Render region on purpose
yfinance users on cloud hosts, especially outside the US, report `YFRateLimitError` on every endpoint for more than 48 hours. Render's egress is a data-center IP. The 5-minute quote cycle and the 15-minute universe job (H1) both hit Yahoo from it.
- Fix: choose a **US region** on Render (Virginia or Ohio) and put Supabase in the matching AWS region. Set `FINNHUB_API_KEY` so the fallback chain has a second source. Before inviting family, run a one-off live check from the deployed instance, for example an admin "data sources" probe or the existing `live-data-check` script.
- Why: if quotes stop, stops and targets stop firing silently. The breaker only pauses fetches; it does not alert anyone.
- Sources: https://github.com/TauricResearch/TradingAgents/issues/1425 · https://github.com/ranaroussi/yfinance/discussions/2431

---

## High

### H1. The universe screener runs inside the 512 MB API process
`register_jobs` adds `universe_scores` (20 history calls every 15 minutes) to the in-process scheduler. `docs/deployment.md` Option A says heavy jobs (the universe screener) run in GitHub Actions to "keep the 512 MB API light".
- Fix: add a `SCHEDULER_UNIVERSE_ENABLED` flag (false in the slim image) plus an Actions cron, or measure it under B2 first.
- Why: this is the biggest memory and Yahoo-request cost after quotes, and it runs on 0.1 CPU next to user requests.
- Source: `app/scheduler/setup.py:93-99`; `docs/deployment.md` table row "Heavy jobs"

### H2. The free-tier LLM budgets do not match the real vendor quotas
- `llm_daily_budget = 900` requests per provider per day. Groq's free tier on `openai/gpt-oss-20b` is 1,000 RPD but only **200,000 tokens per day**. One committee run is about 4 × 2–3k tokens, so roughly 20 runs a day empty Groq's TPD long before 900 requests. Gemini free RPD for Flash models is quoted between 20 and 500 depending on model and date.
- Fix: add a per-provider **tokens-per-day** budget (`tokens_in + tokens_out` is now recorded, thanks to 0017). Set `llm_daily_budget` per provider and model from the vendor's limits page at deploy time.
- Why: today the guard never trips. The vendor's 429 does the limiting, which wastes calls and gives every user templates for the rest of the day.
- Sources: https://inferenceapis.com/reference/rate-limits · https://klymentiev.com/blog/groq-pricing · https://aipromptshub.co/blog/gemini-api-free-tier-rate-limits

### H3. A committee run can exceed the 100-second proxy timeout
The committee route runs 4 LLM calls one after another (30 s timeout each, plus provider fallback and a repair retry) in one sync request. The path is Pages Function → `*.onrender.com`, and Render's edge is Cloudflare, which returns 524 after about 100 s. Render free is also 0.1 CPU.
- Fix: cap the total committee time (an overall deadline, after which the remaining roles use templates), or run news and profile in parallel.
- Why: the user sees an error after waiting, and the run has already been counted against the daily cap.
- Source: https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-524/

### H4. The `onrender.com` origin is publicly reachable without the proxy
Anyone can call `https://<svc>.onrender.com/api/*` directly. Rate limits then key on Render's edge peer, so all direct callers share one bucket. Turnstile and CSRF still apply.
- Fix: add an optional `REQUIRE_PROXY_AUTH=true` for production that answers 404 or 403 to every `/api/*` request except `/api/health` and the Telegram webhook when `X-Proxy-Auth` does not match.
- Why: defense in depth. It also stops anyone outside from burning the per-IP buckets or the shared LLM quota through the origin.
- Source: `app/auth/ratelimit.py:client_ip`; `docs/deployment.md` "Client IP behind each hosting option"

---

## Medium

- **M1. The committee's hourly slot is used even when the daily cap rejects, and an unknown symbol uses up a run.** `enforce_limit(committee_limiter …)` runs before the daily check, and both run before `_market()` validates the symbol. Fix: validate first, check both caps, then count both. Why: a typo burns one of 5 hourly or 10 daily runs. Source: `app/api/analyze.py:291-302`.
- **M2. UptimeRobot's free plan is for personal, non-commercial use only (ToS since 2024-12-01), and Render discourages keep-alive pings.** It is fine for this personal or family app. Write it down in `docs/deployment.md` and keep a fallback (a GitHub Actions cron every 10 minutes). Why: if the ping stops, Render sleeps, the scheduler stops and alerts are missed. Sources: https://dev.to/r0tten0x/uptimerobot-free-plan-in-2026-the-limits-thatll-actually-bite-you-445g · https://odown.com/blog/how-to-keep-a-render-free-service-from-sleeping/
- **M3. Supabase free has no backups.** Automate an encrypted weekly `pg_dump` from GitHub Actions (secret `DATABASE_URL`, artifact encrypted with `age`). Why: the rule "Encrypt backups" in `docs/security.md` §4; the free plan's backups are paid-only. Source: https://supabase.com/docs/guides/database/connecting-to-postgres
- **M4. The Supabase connection notes are now confirmed.** The direct host is IPv6-only on free, and the session pooler (5432) is IPv4. Remove the "*verify*" markers in `docs/deployment.md` step 1. Also check the connection count: pool 3 + overflow 2 + leader-lock connection + the `migrate` step at boot. Source: https://supabase.com/docs/guides/database/connecting-to-postgres/pooling-and-limits
- **M5. Supabase pauses after 7 days without database activity.** Activity means queries, not dashboard visits. The quote job keeps it awake only while Render is awake (M2), so a ping outage of more than a week would pause the database, and someone has to unpause it by hand. Add a reminder to `docs/reminders.md`. Source: https://www.itpathsolutions.com/supabase-free-tier-limits
- **M6. The model probe imports `google-genai` in the API process just to list models.** That costs memory at startup in 512 MB. Use a plain `httpx` GET on `/v1beta/models` (the key goes in a header) and do the B1 test call the same way. Why: one less heavy import in the slim image.
- **M7. The Pages Function free quota is 100,000 requests per day, with 10 ms CPU per request.** SWR polls every 60 s for each hook on a page, and the Telegram webhook also goes through the Function. That is fine for a family. Write it down and keep `refreshWhenHidden` off. Source: https://dev.to/david_viejo_4d48fdfa7cfff/cloudflare-pages-free-tier-limits-pricing-2026-1f8f
- **M8. Render free has a 30–60 s cold start.** If the instance is asleep when Telegram calls the webhook, Telegram retries, and a `/start` link code could be processed twice. `bind_from_start` should be idempotent; add a test for a replayed update. Source: https://justinmckelvey.com/blog/is-render-free

## Low

- **L1. No repo-root `.gitignore`.** Only `backend/` and `frontend/` have one, so a `.env` at the repo root could be committed. Add a root `.gitignore` with `.env`, `*.db`. Why: the "never commit `.env`" rule.
- **L2. `.env.example` is missing `COMMITTEE_RUNS_PER_USER_PER_DAY`** and possibly other newly added settings. Add a test that compares `Settings` fields with `.env.example` (allow-list the internal ones). Why: CLAUDE.md says to keep `.env.example` up to date.
- **L3. `TELEGRAM_WEBHOOK_SECRET` is not validated.** Telegram accepts 1–256 characters from `A-Za-z0-9_-`. Validate this at startup and require at least 32 characters in production. Why: a secret with other characters makes `setWebhook` fail with a confusing error. Source: https://core.telegram.org/bots/api#setwebhook
- **L4. Log redaction only rewrites `record.msg`, not tracebacks (`exc_info`).** `scrub.py:148,188` and `retriever.py:146` log with `exc_info=True`. Add the same redaction to the formatter's `formatException`. Why: an httpx exception string can contain a Telegram URL with the bot token.
- **L5. The session cookie is `pm_session`.** In production, use `__Host-pm_session` (Secure, Path=/, no Domain). Why: a sibling subdomain on `pages.dev` cannot overwrite it. Source: https://developer.mozilla.org/en-US/docs/Web/HTTP/Cookies#cookie_prefixes
- **L6. The ledger tables grow forever.** `llm_usage` (including `quota:user:N` rows) and `llm_bucket` (`gemini|user:N` rows) are never purged. Add a purge older than 90 days. Why: the 500 MB Supabase limit.
- **L7. Migration 0017 sets `server_default="0"`, but the model does not.** That is harmless now (the tests pass). If `compare_server_default` is ever turned on, autogenerate will report a diff. Add `sa_column_kwargs={"server_default": "0"}` for symmetry.
- **L8. `docs/deployment.md` step 5.3 still says the Function "passes `CF-Connecting-IP`".** It sends `X-Client-IP` and `X-Proxy-Auth`. Fix the doc. Why: following it would trust the wrong header.
- **L9. The SQLite run printed one `PytestUnhandledThreadExceptionWarning`** (a background thread raised during a test). Run `pytest -W error::pytest.PytestUnhandledThreadExceptionWarning` to find it. Why: it may hide a real scheduler or race bug.
- **L10. Admin `GET /admin/invites` returns other admins' unused codes.** That is acceptable for admins; note it in `docs/settings-spec.md`.
- **L11. The CIO can nudge the displayed score up by as much as +15.** Make sure `apply_adjustment` output never reaches `scoring/risk.py` or the alert engine; a test that asserts this exists only implicitly. Why: architecture rule 3 (risk never upgrades).

## What passed (checked)
- Ask history: every helper filters on `user_id`, so a foreign conversation or portfolio returns 404. Purge job, 100-conversation cap, export and cascade on account and portfolio delete are all in place.
- Admin `/admin/llm-usage` is behind `AdminDep` and returns counters only (no user ids, prompts or emails). `quota:` rows are filtered out.
- The committee sees only `PublicFacts` and public passages. Ask only uses providers declaring `no_training` (none by default). The LLM layer scrubs every request (`complete` is `@final`).
- Secrets travel in headers (`x-goog-api-key`, `Authorization`, `X-Finnhub-Token`). Provider errors log only `type(exc).__name__` or HTTP status. httpx is set to WARNING. Bot tokens are redacted in messages.
- The ledger is atomic (`SET col = col + n`, insert-race fallback, CAS bucket) and works the same on both dialects.
- Migration 0017 is expand-only, with a migrate-with-data test and a rollback test.
- Production refuses to start with weak `SECRET_KEY`, insecure cookies or a `*` CORS origin. `AUTO_MIGRATE` defaults to false in production, the image runs `cli migrate` before uvicorn, and the schema check refuses a stale or unsafe schema.
- The Telegram webhook refuses everything while the secret is unset, compares in constant time, and rate-limits `/start` per chat.
- The proxy drops every client IP or proxy-auth header before setting its own. The API trusts `X-Client-IP` only with the shared secret.

---

## Comparable apps: suggested features (for the user to choose)

| # | Idea | Why | Source |
|---|---|---|---|
| F1 | **Import holdings from a broker PDF or CSV export**, alongside screenshots | getquin added AI document import (PDF/CSV/Excel) in June 2026. A CSV needs no OCR, so there is no image at all, which fits the "screenshots never kept" rule even better. | https://www.matchmybroker.com/tools/getquin-review |
| F2 | **"Why is it moving?"** on a big daily move, built from cached news chunks plus a template | Delta's most-cited feature. We already have RAG news and the "Why?" explanation, so the cost is low. | https://www.matchmybroker.com/tools/delta-investment-tracker-review |
| F3 | **Insider-transaction notices** for held US stocks (Finnhub free `insider-transactions`) | Delta and TipRanks both surface insider activity, and TipRanks' Smart Score uses it as an input. | https://www.askatlantis.com/blog/tipranks-review-2026 |
| F4 | **Score-change alerts on the watchlist** (not only on holdings) | Seeking Alpha Premium emails when a watched stock's quant rating changes. This matches rule 5 (alert only on change). | https://www.stockbrokers.com/review/tools/seeking-alpha |
| F5 | **Multi-year dividend forecast chart** | getquin and Simply Wall St both show forward dividend income. `portfolio/dividends.py` already has the data. | https://simplywall.st/features/portfolio |
| F6 | **"Copy portfolio for your own AI"**: a privacy-safe export (symbols and weights only) | Ghostfolio 3.78 added "copy portfolio data for AI prompt" and an experimental MCP server. It costs zero LLM quota. | https://ghostfol.io/en/about/changelog |
| F7 | **Read-only MCP endpoint** (later) | Same Ghostfolio direction. It lets the user's own Claude or Gemini read the portfolio without our free quota. | https://github.com/ghostfolio/ghostfolio/discussions/6434 |
| F8 | **Outbound links** to Bizportal or Funder pages for TASE securities (display-only, no scraping) | Israeli users already rely on these for TASE fund and stock depth. | https://play.google.com/store/apps/details?id=com.bizportal |

## Free API and host changes found (2026)
- **Gemini:** 2.5 Flash shuts down no earlier than 2026-10-16, new users have been cut off from 2.5 since 2026-09-18, and the 3.5–3.8 Flash family is GA (B1). https://ai.google.dev/gemini-api/docs/changelog
- **Groq free tier:** gpt-oss-20b allows 30 RPM, 1,000 RPD, 8k TPM and 200k TPD. Limits are per organization, Llama models have left the free tier, and cached tokens do not count (H2). https://inferenceapis.com/reference/rate-limits
- **Render free:** 750 instance hours, spin-down after 15 minutes, 30–60 s cold start, free Postgres expires after 30 days (we use Supabase, so this does not affect us). https://justinmckelvey.com/blog/is-render-free
- **Supabase free:** 500 MB, 2 projects, pause after 7 days without database activity, direct host IPv6-only, session pooler IPv4 (M4, M5). https://supabase.com/docs/guides/database/connecting-to-postgres
- **Cloudflare Pages Functions:** count against the Workers free plan (100k requests per day, 10 ms CPU) (M7). https://temps.sh/blog/cloudflare-pages-free-tier-limits-2026
- **Finnhub free:** unchanged at 60/min, personal use. https://finnhub.io/pricing
- **yfinance 1.7.0 (locked):** blocking of cloud IPs continues (B5). https://github.com/ranaroussi/yfinance/discussions/2431

## Suggested order before deploy
B3 (Postgres + CI) → B1 (model id) → B4 (DB-backed committee cap + user quota) → B2 (build and measure the image in CI with H1's flag) → choose a US region (B5) → H2/H3 → deploy → live check from Render → L-items.
