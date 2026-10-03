# Deployment: free hosting, no credit card

Status: **host not chosen yet** (user decision, 2026-10-03: "decide later, build host-agnostic").
Constraints: no credit card, no always-on home device, free.
Researched 2026-10-03 from search results. Items marked *verify* must be checked at deploy time.

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
| Website | **Cloudflare Pages** (or Vercel Hobby) | static hosting, no card | Needs the Next.js static export with locale-prefixed routes. |
| API + scheduler | **Render free web service** | 512 MB RAM, 0.1 CPU, 750 h/month (one service 24/7), no card | Sleeps after 15 min without inbound requests. Disk is **ephemeral**. |
| Keep-awake | **UptimeRobot** free | 5-minute checks, no card | Pings `/api/health` so Render never sleeps. *Verify Render's terms on keep-alive pings.* |
| Database | **Supabase** free Postgres | 500 MB, no card, 2 projects | Pauses after 1 week without activity; our 5-minute jobs keep it active. |
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
- Run the **same Docker Compose** as development: SQLite, Tesseract, separate scheduler. No code changes.
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

## Decision checklist (when Phase 1 is ready to deploy)
1. Measure the API's memory in a 512 MB container.
2. Run the test suite against Postgres.
3. Confirm Render's free-tier terms and restart behaviour.
4. Confirm Supabase's pause rules for a database that gets a write every 5 minutes.
5. Pick the option, then write the step-by-step setup guide here.
