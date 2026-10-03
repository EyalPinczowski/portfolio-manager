# Phase 1.5 spec: hardening

Approved 2026-10-03 after the [Phase 2 pre-phase review](reviews/phase-2-2026-10-03.md). The plan: fix the money bugs and security gaps found there **before** building Phase 2 features. Binding for the backend and frontend agents. Also read `docs/security.md`, `docs/deployment.md`, `docs/phase-1-spec.md` and `CLAUDE.md`.

## User decisions
- **Week = Sunday to Saturday.** Setting `week_start_day` (default `sunday`). The summary returns `week_start`. Backend and frontend must agree, in the Asia/Jerusalem timezone.
- **Same-origin proxy** for the card-free host: a Cloudflare Pages Function forwards `/api/*` to the API. Cookies stay first-party (`SameSite=Lax`).
- **Screenshot rule:** keep only the stock rows, delete the screenshot (see `docs/security.md`).

## Contract changes (backend and frontend must both follow these exactly)
| Area | Change |
|---|---|
| Week | `Summary.week_start: date` (the Sunday of the current week). `weekly_bars[].week_start` are Sundays. |
| FX | `Summary.fx_stale: bool`. |
| Upload (server OCR path) | `POST /api/portfolios/{id}/imports` takes a **raw image body** (`Content-Type: image/png`, `image/jpeg` or `image/webp`) plus the CSRF header. **Not multipart.** Response is still `ImportDraftOut`. 413 if over the limit, 415 for another type. |
| Upload (on-device OCR path) | New `POST /api/portfolios/{id}/imports/rows` with JSON `{ "rows": ImportRow[] }`. The server validates (qty × price ≈ value), matches securities, drops non-stock rows, and returns `ImportDraftOut`. The image never reaches the server. |
| Draft expiry | `ImportDraftOut.expires_at: datetime` (24 h after creation). |
| Import flags | `ImportRow.flags` may contain `currency_changed` and `low_confidence_match`. Matching returns candidates, never silently picks a weak match. |
| Account delete/export | `DELETE /api/me` takes JSON `{ "password": string }`. Export becomes `POST /api/me/export` with JSON `{ "password": string }`. Wrong password → 403. |
| Sessions | `GET /api/auth/sessions` → `[{id, created_at, last_seen_at, current: bool}]`, `DELETE /api/auth/sessions/{id}`, `POST /api/auth/sessions/revoke-all` (keeps the current one). |
| Rate limits | 429 with a `Retry-After` header on login, signup and upload. |
| Launch gate | `GET /api/launch-gate` → `{ "open": bool, "reasons": string[] }`. |
| Nullability (already true in the backend) | `Holding.pnl`, `Summary.since_start_date`, `since_start_series[].sp500_pct/ta125_pct` and `Portfolio.tracking_started_at` are nullable. `Holding.price_stale: bool`, `ScoreCardDetail.available/.disclaimer` and `SignalBreakdown.nominal_weight` are present. |
| Enums | `market`, `asset_type`, `Me.locale`, import change types, `stop_type` become `Literal` types in the backend so OpenAPI has enums. |

## Backend work (agent `backend-A`, then `backend-B`, sequential: same files)
**backend-A: money correctness**
1. `yfinance_provider.get_quotes`: single-ticker MultiIndex frame; test with a 1-ticker MultiIndex frame.
2. Unknown currency: fall back to `Security.currency`/market; never store a quote with an unknown currency; `to_ils` raises on unknown; handle GBp/ILA; re-validate cached history. Tests: ILA stock with a failed currency lookup, an index in points, GBp.
3. TWR: sum transactions in `(prev snapshot date, current date]`; catch-up snapshot on scheduler start; `misfire_grace_time`. Test with a gap day and a deposit.
4. Importer matching: `token_sort_ratio`/`ratio`, margin over the runner-up, length check; auto-accept only exact symbol or TASE number; otherwise return candidates plus the `low_confidence_match` flag; `currency_changed` flag instead of overwriting; no global `PriceQuote` from screenshots (store source and stale flag; per-user only). Tests: "Apple Hospitality REIT" must not become AAPL; "Microstrategy" must not become MSFT.
5. FX: `fx_stale`, no silent 3.6 constant (use the last known value flagged stale, or raise a clear error and show stale).
6. Week start Sunday (config, `week_start`, Asia/Jerusalem boundary tests).
7. Calendars: `exchange_calendars` XTAE/XNYS plus the config override; holiday and half-day tests; one more quote fetch ~15 min after each close; empty tickers after `yf.download` count as rate-limited (circuit breaker plus single-ticker retry).
8. NaN-safe volume/OBV; property test: no reason string contains "nan".

**backend-B: security, launch gate, contract hygiene, infra**
1. Raw-body upload plus an ASGI body-size middleware on all `/api` routes; magic-byte format check; `Image.MAX_IMAGE_PIXELS ≈ 25e6` with the bomb warning as an error; tall-image downscale; bytes in memory only, released in `finally`; never send a half-redacted image to a third party; new `imports/rows` endpoint; drop non-stock rows; `expires_at` plus a 24 h purge job; clear `rows` on confirm. Tests: a >1 MB upload leaves no temp files (spy on `tempfile`), drafts contain only stock fields, no OCR text in captured logs, a 12000×12000 PNG is rejected with low memory.
2. Auth: argon2 m = 19 MiB, t = 2, p = 1 with `Semaphore(2)`; limiter before hashing; trust only `CF-Connecting-IP` from the configured proxy; per-IP + email backoff; signup and upload limits; bounded LRU; identical signup response for an existing email; atomic invite use; password for delete/export; sessions endpoints.
3. Validation: typed `RiskOverride`/`RiskFilter` with `ge/le` and `extra="forbid"` on every PATCH body; symbol regex; cap alerts per user (50); search shows only seeded or verified securities.
4. Logging: `httpx` logger to WARNING, `bot\d+:` redaction filter, Gemini `ValidationError` handled without echoing input.
5. Headers: CSP, `X-Content-Type-Options`, `Referrer-Policy`, HSTS, `Cache-Control: no-store` on `/api/*`; docs off in production; `cookie_secure` default True and the app refuses to start in production without it (`ENV=production`).
6. `LaunchGate` service plus `GET /api/launch-gate`, and a contract test that scans every response model for verdict fields (names such as `verdict`, `recommendation`, `action`, `buy`, `sell`).
7. Backend enums as `Literal`; regenerate `backend/openapi.json`.
8. Add every new or changed route to `tests/test_api_scoping.py`.
9. Infra: `uv.lock`; `.github/workflows/ci.yml` (ruff, mypy, pytest on SQLite and Postgres 16, pip-audit; frontend eslint, tsc, vitest, build, npm audit); `docker-compose.yml` for local dev; pin base images by digest where known.

## Frontend work (agent `frontend-A`, parallel with backend-A)
1. `Holding.pnl` nullable plus "—" rendering (`HoldingsList.tsx`); `since_start_date` null → no date shown; nullable benchmark points handled in the charts; `price_stale` indicator; `Portfolio.tracking_started_at`; scorecard `available`/`nominal_weight`/`disclaimer` (show "no data", not a 0 score); `Breach.symbol` links; `ImportRow.tase_number`.
2. Types generated from `backend/openapi.json` with `openapi-typescript` (a script `npm run gen:api`), the hand-written `api.ts` types replaced or derived from them. Mocks validated against the schema (a vitest test).
3. Week starts Sunday in the mocks and labels; show `week_start`.
4. One weight formatter; locale-aware number parser in the import review table (`1,234` and `1.234,5`).
5. Clear the SWR cache and the stored portfolio on logout and login.
6. Security headers in `next.config.ts` (`headers()` with CSP etc.) and Cloudflare `public/_headers` for the static export.
7. **On-device OCR import:** `tesseract.js` (Hebrew + English, loaded lazily so the main bundle stays small), parse the text into `ImportRow[]` in the browser (port the heuristics of `backend/app/importer/parse.py`), blur/redact digit runs before reading, then `POST /imports/rows`. The server-upload path stays as an explicit "Use server reading" fallback with a consent notice. Show "We keep only the stock list. The screenshot is deleted right away."
8. Settings page: password prompt for delete/export (the new contract), sessions list with revoke.
9. Static export: `output: "export"`-compatible build (the dynamic holding route becomes `/holding?id=` or similar), `NEXT_PUBLIC_API_URL` default same-origin, and the Cloudflare Pages Function `functions/api/[[path]].ts` that forwards `/api/*` to `API_ORIGIN`, passes `CF-Connecting-IP` and strips hop-by-hop headers.
10. Rebuild with the new contract: lint, tsc, vitest, build all pass. Mock mode must exercise a holding with `pnl: null`.

## Later (Block D, after backend-B)
Postgres (psycopg, Alembic, `ondelete`), in-process scheduler with a Postgres advisory lock, slim API image without Tesseract, a 512 MB memory probe, and the step-by-step deployment guide.
