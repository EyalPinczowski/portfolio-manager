# Deployment: free hosting, no credit card

Status: **host not chosen yet** (user decision, 2026-10-03: "decide later, build host-agnostic"). The code is ready for Option A (Phase 1.5-D): see the setup guide below.
Constraints: no credit card, no always-on home device, free.
Researched 2026-10-03 from search results. Items still marked *verify* must be checked at deploy time.

## What the app needs
- Something that runs the **5-minute price refresh and alerts all day**, which means a process that stays awake.
- A **database that survives restarts**.
- HTTPS, so the phone app and Telegram links work.
- Memory for the API (FastAPI + pandas). Screenshot reading adds more if Tesseract runs on the server.

## What is NOT possible without a card
| Option | Why not |
|---|---|
| Oracle Cloud Always Free | Needs a real credit/debit card. Its free ARM VM is also now 2 OCPU / 12 GB (halved in June 2026). |
| Google Cloud e2-micro, AWS, Azure | Card required. AWS and Azure free credits expire. |
| Fly.io, Railway, Koyeb | Free tiers closed to new accounts. |
| Hugging Face Spaces | Docker Spaces need a paid PRO plan to create. Disk is ephemeral and the Space sleeps after 48 h. |
| Cloudflare Workers + D1 | Free, but would need the backend rewritten in TypeScript. |

**No card-free cloud offers a real always-on server.** Two designs work:

## Option A: card-free cloud stack (expected choice)

| Part | Service | Free limits | Notes |
|---|---|---|---|
| Website | **Cloudflare Pages** (or Vercel Hobby) | static hosting, no card; Pages Functions (the `/api` proxy) are capped at **100,000 requests/day** on the free plan | Needs the Next.js static export with locale-prefixed routes. |
| API + scheduler | **Render free web service** | 512 MB RAM, 0.1 CPU, 750 h/month (one service 24/7), no card | Sleeps after 15 min without inbound requests. Disk is **ephemeral**. |
| Keep-awake | **UptimeRobot** free | 5-minute checks, no card | Pings `/api/health` so Render never sleeps. The free plan is **personal, non-commercial use only**; fallback: a GitHub Actions cron (`curl` every 5-10 min, delays of several minutes happen, so it may let Render nap). |
| Database | **Supabase** free Postgres | 500 MB, no card, 2 projects | **Pauses after 7 days without activity** (database activity, not `/api/health`); our 5-minute scheduler writes keep it active, but if the API is down for a week the project pauses and must be restored by hand in the dashboard. |
| Heavy jobs (universe screener) | **GitHub Actions** cron | free minutes, no card | Keeps the 512 MB API light. Actions cron has delays of several minutes. |
| Screenshot OCR | Gemini free + **in-browser tesseract.js** | free | The image can be read on the phone, so it never leaves the device. Tesseract is dropped from the server image. |
| Bot | Telegram | free | Alerts and weekly review. |

Required code changes (tracked in `docs/ideas.md` until chosen):
1. Postgres support next to SQLite (SQLModel already allows it), with migrations (Alembic) and tests on both.
2. Run the API and scheduler **in one process** (the in-process APScheduler mode), because Render's free tier allows one service.
3. Fit in **512 MB**: measure the API's memory with pandas loaded, lazy-load heavy modules, and keep the screener out of the API process.
4. In-browser OCR provider and redaction in the frontend.
5. Static export of the frontend. **Do not use cross-site cookies:** a Pages site and a `*.onrender.com` API are different sites, so `SameSite=Lax` cookies are not sent and Safari blocks third-party cookies. Use a **same-origin proxy**: a Cloudflare Pages Function (`functions/api/[[path]].ts`) forwards `/api/*` to Render and passes `CF-Connecting-IP`. The cookie stays first-party, there is no CORS, and the rate limiter trusts only that header. (Review 2026-10-03.)
6. An in-process scheduler with a Postgres advisory lock, a startup catch-up snapshot and `misfire_grace_time`, so a restart or a second instance never loses or duplicates jobs.
7. Argon2 parameters of m = 19 MiB with a concurrency semaphore: the default 64 MiB per hash blew past 512 MB in tests (664 MB with 8 logins).

Risks:
- Measured: the idle API uses about 150 MB, so steady state fits in 512 MB. The risks are spikes (login hashing, large screenshots).
- Render may restart the service, which loses in-memory state (alerts' dedupe state must live in the DB).
- The 512 MB limit is tight.
- 750 hours/month covers exactly one service, so nothing else can run there.
- Free tiers change; keep the app portable.

## Option B: home device
- A spare PC or laptop, or a Raspberry Pi 5 (~$60 one-time), always on and connected.
- Run **`compose.prod.yml`** (no dev defaults, secure cookies, a separate `migrate` step, read-only containers, web bound to 127.0.0.1) with SQLite, Tesseract and a separate scheduler. No code changes. Do not use the dev `docker-compose.yml` for a device that other people can reach.
- Reach it from the phone and from family through **Tailscale** (free, no card, private) or Cloudflare Tunnel (free; *verify whether a card is needed for Zero Trust*).
- Risks: the app stops updating when the device or the internet is down, and each family member installs Tailscale.

Not available to the user today (no spare device), so this is a fallback only.

## Host-agnostic rules (enforced in `CLAUDE.md`)
- All settings come from environment variables.
- The database is reached only through SQLModel, so SQLite (dev/home) and Postgres (cloud) both work.
- No SQLite-only features outside dev and tests.
- The server image must run in **512 MB**.
- OCR is behind a provider interface: server Tesseract, Gemini, or in-browser tesseract.js.
- The scheduler can run in-process or as a separate process.
- The frontend can be served as static files and call the API at a configurable URL.

## Measured facts (Phase 1.5-D, 2026-10-03)
- **Memory:** `backend/scripts/memprobe.py` runs the API (one worker, in-process scheduler, no Tesseract) through 8 concurrent sign-ups and logins, a 3000×6000 screenshot, a pixel bomb and a 25-megapixel image. **Peak 359 MB** against the 400 MB limit it enforces (Render's limit is 512 MB). Idle is 139 MB. Biggest remaining costs: decoding a 25 MP image (~75 MB) and password-hashing bursts. `MALLOC_ARENA_MAX=2` is set in the image and saved ~80 MB.
- **Tests:** 460 pass on SQLite and 472 on Postgres 16; ruff and mypy strict are clean. Alembic migrations match the models on both databases and downgrade cleanly.
- **Not verified here:** the Docker image was never built (no Docker daemon in the sandbox), and the CI docker job is unverified. Supabase's direct host is IPv6-only and the session pooler (port 5432) is IPv4 (confirmed). Pick a **US Render region** (e.g. Ohio or Virginia) and the matching **Supabase region** (e.g. us-east-1) to keep database latency low.

## Setup guide: Option A step by step (about 45 minutes, no card)
Do these yourself; they need your accounts. Check each *verify* item against the service's current docs.

**1. Supabase (database)**
1. Create a free project at supabase.com (note the database password).
2. Open *Connect* and copy the **session pooler** connection string (port **5432**). The direct host is IPv6-only (confirmed) and Render cannot reach it; the pooler on 5432 is IPv4.
3. Turn it into `DATABASE_URL`: `postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres?sslmode=require`. The app rewrites `postgres://`/`postgresql://` to the psycopg driver itself.
4. If you ever use the **transaction pooler (port 6543)** instead, also set `DATABASE_PREPARE_THRESHOLD=none` and `SCHEDULER_LOCK_DATABASE_URL` to the session-pooler URL (the scheduler's lock needs a session-level connection).

**2. Create the first admin and invite (from your own computer)**
```bash
cd backend && uv sync --extra postgres
export ENV=production COOKIE_SECURE=true SECRET_KEY="$(python -c 'import secrets;print(secrets.token_urlsafe(48))')" DATABASE_URL='<the URL above>'
python -m app.cli migrate
python -m app.cli create-admin --email you@example.com
python -m app.cli create-invite
```
Render's free plan probably has no shell, so do this locally against Supabase.

**3. Render (API + scheduler)**
1. New *Web Service* from this repository, **Docker** runtime, Dockerfile path `backend/Dockerfile.slim`, build context `backend`, instance type **Free**.
2. Environment variables:

| Name | Purpose | Example | Secret |
|---|---|---|---|
| `ENV` | production mode (docs off, secure cookies enforced) | `production` | no |
| `COOKIE_SECURE` | must be true in production | `true` | no |
| `SECRET_KEY` | signs sessions, 32+ characters (the app refuses to start otherwise) | output of `secrets.token_urlsafe(48)` | **yes** |
| `DATABASE_URL` | Supabase session pooler | see step 1 | **yes** |
| `TRUSTED_PROXY_HEADER` | which header carries the client IP from the Pages Function (**not** `CF-Connecting-IP`: Cloudflare overwrites it for Worker-to-Render requests, so every user would share one IP) | `X-Client-IP` | no |
| `PROXY_SHARED_SECRET` | the same random 16+ character value on Render **and** in the Pages project; the API trusts `X-Client-IP` only when `X-Proxy-Auth` matches it, and refuses to start if the header is trusted without it | `python -c "import secrets;print(secrets.token_urlsafe(32))"` | **yes** |
| `TURNSTILE_ENABLED`, `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY` | free Cloudflare human check after 3 failed logins (create a Turnstile widget in the Cloudflare dashboard, no card) | `true`, site key, secret key | secret key **yes** |
| `TELEGRAM_BOT_TOKEN` | alerts (optional) | | **yes** |
| `TELEGRAM_WEBHOOK_SECRET` | webhook for `/start <code>` linking; the route refuses everything while empty (call `setWebhook` with the same `secret_token`) | | **yes** |
| `TELEGRAM_BOT_USERNAME` | only for the t.me deep link | | no |
| `SCHEDULER_IN_PROCESS`, `MALLOC_ARENA_MAX`, `PORT` | already set by the image / Render | | no |

   **Set `GEMINI_API_KEY` and `GROQ_API_KEY`** (free keys): the committee needs at least one LLM provider, otherwise it falls back to templates. The slim image has no Tesseract, so it only refuses to send *screenshots* to a third party without server-side redaction; text-only committee calls (public facts) are fine. Choose a US region for both Render and Supabase (see above). Leave `CORS_ORIGINS` empty (same-origin proxy).
3. The image's start command runs `python -m app.cli migrate` and then uvicorn with one worker. Health check path: `/api/health`.
4. Note the service URL, e.g. `https://pm-api.onrender.com`.

**4. UptimeRobot (keeps Render awake)**
Add an HTTP monitor on `https://<service>.onrender.com/api/health`, interval **5 minutes**. `/api/health` does not touch the database, so Supabase stays active only because the scheduler writes every 5 minutes (Supabase pauses after 7 days of inactivity). UptimeRobot's free plan is for personal, non-commercial use only; if that is a problem, use a GitHub Actions cron that curls `/api/health` instead.

**5. Cloudflare Pages (website + same-origin proxy)**
1. Create a Pages project from this repository: root directory `frontend`, build command `npm ci && npm run build`, output directory `out`.
2. Environment variables: `API_ORIGIN` = the Render URL (used by `functions/api/[[path]].ts`), and `PROXY_SHARED_SECRET` (the same value as on Render). Leave `NEXT_PUBLIC_API_URL` empty so the browser calls the same origin.
3. The Function forwards `/api/*` to Render and passes `CF-Connecting-IP`; the browser only ever talks to the Pages domain, so the login cookie is first-party.

**6. First login**
Open the Pages URL, sign up with the invite code from step 2, accept the disclaimer, create a portfolio and import a screenshot (read on your phone).

**7. Backups**
Supabase keeps its own backups on paid plans only, so use Settings → Export regularly (it downloads your data as JSON), and run a periodic `pg_dump` from your computer *(optional)*.

## Decision checklist (when Phase 1 is ready to deploy)
1. Measure the API's memory in a 512 MB container.
2. Run the test suite against Postgres.
3. Confirm Render's free-tier terms and restart behaviour.
4. Supabase pauses a free project after 7 days without activity; a write every 5 minutes prevents it (see the reminder in `docs/reminders.md`).
5. Pick the option, then write the step-by-step setup guide here.


## Client IP behind each hosting option (Phase 2.0-F)
- **Option A (Cloudflare Pages Function → Render):** the Function sends `X-Client-IP` and `X-Proxy-Auth` (shared secret). Set `TRUSTED_PROXY_HEADER=X-Client-IP` and `PROXY_SHARED_SECRET` on Render; nothing else. The Turnstile widget uses the action `login`; the server verifies the action and the hostname (`TURNSTILE_EXPECTED_ACTION`, `TURNSTILE_HOSTNAME` or the hostnames in `CORS_ORIGINS`).
- **Option B (home device, `compose.prod.yml`):** Caddy trusts **nobody** by default (`TRUSTED_PROXIES` defaults to a dummy address). Behind a **Cloudflare Tunnel** (`cloudflared` sets `CF-Connecting-IP`) or **Tailscale Serve** (sets `X-Forwarded-For`), set `TRUSTED_PROXIES` to the daemon's address and `CLIENT_IP_HEADERS` to the header it sets; otherwise every user shares one IP.
