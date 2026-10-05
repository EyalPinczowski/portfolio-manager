# Code review: frontend and deploy (2026-10-05)

Scope: frontend/ (Next.js static export, OCR parsers, import flow, formatting), worker/index.ts,
lib/proxy-handler.ts, lib/proxy-headers.ts, functions/api/[[path]].ts, wrangler.jsonc, public/_headers,
.github/workflows/deploy-site.yml + ci.yml, backend/Dockerfile.slim. Read-only review; no code changed.

NOTE: this session ran in plan mode, so the report could not be written to the requested path
(docs/reviews/... or the scratchpad). The full report is this file; copy it to
`docs/reviews/code-review-frontend-2026-10-05.md` (or the scratchpad path) when allowed.

Counts: **2 high, 3 medium, 7 low**.

---

## HIGH

### H1. `currency_changed` can never be cleared in the UI: every "hebrew_broker_cards" import (and any row flagged `currency_changed`) is unconfirmable — CONFIRMED
- Where: `frontend/lib/ocr/hebrewCards.ts:102` (every row gets `flags: ["currency_changed"]`);
  `frontend/components/ImportPage.tsx:290` (currency column is read-only text), `:415` (confirm button
  disabled conditions do not include it); backend `app/importer/service.py:400-405` (confirm returns 422
  while the flag is present); `app/importer/match.py:228-231` (apply_match keeps the flag even when the
  match agrees).
- What: the server says "confirm the currency (remove the flag) or edit the row", but the review table has
  no currency/unit control and no way to drop a flag. The 422 detail is a string, so `rowProblems` is empty
  and `fail(err, "confirm")` shows the "not matched" error (`ImportPage.tsx:76`), which is misleading.
- Scenario: user imports a "תיק אישי" screenshot -> rows appear, Confirm is enabled -> every press returns
  422 "shown in a different currency" -> UI says the rows are not matched. Only escape: delete every row.
  Same for a US stock shown in ILS by a generic screenshot.
- Fix: add a currency/unit select per row (ILS / agorot / USD) plus a "currency checked" checkbox that
  removes `currency_changed` from `r.flags` before `patchImport`; disable Confirm while any row still has
  the flag; map that 422 to its own error text.

### H2. Generic parser turns any leftover positive number into the per-unit cost, which is hidden from review and overwrites the holding's average cost — CONFIRMED
- Where: `frontend/lib/ocr/parse.ts:108-112` (first unused positive number -> `row.cost`);
  `ImportPage.tsx:209-212` (table columns: name, symbol, quantity, price, value, currency, change — no cost);
  backend `app/importer/parse.py:159-162` (`cost_native` = per-unit), `service.py:489-499` (cost written to
  `h.avg_cost` unless `cost_inferred`, which the generic parser never sets).
- Scenario: OCR line `AAPL 10 150.00 1,500.00 25.30` (last column = P&L in $). Triple 10x150=1500 is found,
  25.30 becomes `cost`; on confirm AAPL `avg_cost` = 25.30 (P&L shows about +490%). A total-cost column
  (`עלות 1,200.00`) becomes a per-share cost of 1,200. An existing holding's correct cost is overwritten.
- Fix: in the generic parser only set `cost` when a header/marker says it is a cost and it is plausible per
  unit (for example within 0.05x-20x of price), otherwise leave null; mark it `cost_inferred` so the server
  never overwrites an existing cost; show an editable cost column in the review table. Mirror in
  backend `parse.py`.

## MEDIUM

### M1. Meitav P&L % loses its minus sign when OCR emits an en/em dash -> wrong inferred cost — CONFIRMED (code path; trigger depends on OCR output)
- Where: `frontend/lib/ocr/meitav.ts:204` normalises only U+2212; `:261` and `:267` accept only ASCII `-`/`+`.
  The same file already treats `–` and `—` as OCR variants (`LOOSE_BULLET`, `:150`), and Tesseract often
  outputs them for minus.
- Scenario: card text `$1,000.00  –12.50%` -> pnl = +12.5 -> cost = price/1.125 instead of price/0.875
  (about 22% too low) and the row is flagged `cost_inferred` with "+12.5%".
- Fix: in `toBlocks` normalise `[−–—‐‑]` immediately before a digit or `%` to `-`; add fixture tests;
  mirror in backend `meitav.py`.

### M2. Worker deploy has no `not_found_handling`: unknown paths return an empty 404, not `404.html` — CONFIRMED (config)
- Where: `frontend/wrangler.jsonc:6-10`; `worker/index.ts:17`.
- What: Pages served `404.html` automatically. Workers static assets default to `not_found_handling: "none"`,
  so a miss falls through to the Worker, `env.ASSETS.fetch` returns a 404 with no body, and the user sees a
  blank page (for example `/fr/`, an old bookmark, or `/he/holdng/`).
- Fix: `"not_found_handling": "404-page"` in `assets` (`out/404.html` exists).

### M3. Deploy workflow exposes the Cloudflare API token to every npm lifecycle script and fetches an unpinned wrangler — CONFIRMED
- Where: `.github/workflows/deploy-site.yml:25-27` (job-level env), `:52` (`npm ci` runs every dependency's
  postinstall, plus `scripts/copy-ocr-assets.mjs`), `:54` (build), `:56` (`npx wrangler deploy`; wrangler is
  not in `package.json`, so npx downloads the latest version at deploy time).
- Scenario: a compromised transitive dependency (or a malicious wrangler release) reads
  `CLOUDFLARE_API_TOKEN` from env during `npm ci` and gains account access.
- Fix: put the secrets in `env:` of the deploy step only (the gate step can read `secrets.*` through its own
  step env), add `wrangler` as a pinned devDependency (or use `cloudflare/wrangler-action` pinned by SHA),
  and use `npm ci --ignore-scripts` followed by `npm run assets:ocr`.

## LOW

### L1. "Weeks start on Sunday" labels are hard-coded even when the user picked Monday — CONFIRMED
- `frontend/components/PnlStrip.tsx:20` (fallback `weekSundayStart`) and `:39` (`weeksStartSunday` always
  shown); `AppearanceSettings.tsx:81` allows `"monday"`. Scenario: setting = Monday -> the chart caption
  still says each bar starts on Sunday. Fix: pass `week_start_day` and use `days.<day>` in the message.

### L2. `parseLocaleNumber` rejects numbers that carry bidi marks or a trailing minus — CONFIRMED
- `frontend/lib/number.ts:14,18`: strips `\s`, NBSP, NNBSP, ₪ $ € % but not U+200E/U+200F/U+061C/U+202A-202E/
  U+2066-2069; a minus is accepted only at the start. Text pasted from a Hebrew broker web page
  (`‏1,234.50‏`, `1,234.50-`) is marked invalid in NumberCell. It is never misread (returns null), so the
  severity is low. Fix: strip `[‎‏؜‪-‮⁦-⁩]` and accept one trailing `-`/`−`.

### L3. `<img>` decode fallback uses a `blob:` URL that the CSP blocks — CONFIRMED
- `frontend/lib/ocr/image.ts:95-100` vs `public/_headers:5` `img-src 'self' data:`. The fallback runs only when
  `createImageBitmap` fails (older WebKit), and then always errors with "ocr". Fix: add `blob:` to img-src in
  `_headers`, `security-headers.ts` and the Caddyfile, or drop the fallback.

### L4. Modal steals focus on every parent re-render and has no focus trap or restore — CONFIRMED
- `frontend/components/Modal.tsx:6-12`: the effect depends on `onClose`, and `ImportPage.tsx:378` passes an inline
  arrow function, so every re-render (SWR revalidation, state changes) calls `ref.current.focus()` and pulls
  keyboard/screen-reader focus off the button the user is on. Tab also escapes the dialog. Fix: focus once on
  mount (empty deps, keep onClose in a ref), trap Tab, and restore focus on close.

### L5. Service worker caches error responses and never prunes old chunks — CONFIRMED
- `frontend/public/sw.js:22-28` stores any response (including 404/5xx) for `/_next/static/*` with no `res.ok`
  check. `CACHE` is a fixed name, so hashed chunks from every past deploy pile up. Fix: cache only `res.ok`, and
  version the cache name per build (or prune keys that are not in the current build).

### L6. Site deploy runs only from a feature branch — CONFIRMED
- `.github/workflows/deploy-site.yml:10` triggers on `ccr-8e00f184-rqshto`, not `main`. Once work merges to
  main, pushes there never deploy, and anyone who can push to that branch name can deploy. Fix: `main` (plus a
  protected environment).

### L7. `404.html` has no `<html lang dir>`/`<body>` — CONFIRMED
- `frontend/app/layout.tsx:5` returns bare children, and no `app/not-found.tsx` exists, so `out/404.html` starts
  with `<script>`/`<meta>` (checked) and has no language or direction. Screen readers guess the language, and
  Hebrew text renders LTR. Fix: add `app/not-found.tsx` with its own `<html lang="he" dir="rtl">` and links to `/he/`
  and `/en/`.

---

## Checked, no problem found
- **OCR text and screenshots are not persisted**: the canvas is zeroed (`image.ts:134`), tesseract runs with
  `cacheMethod:"none"`, `localStorage` holds only the portfolio choice, guide and mock flags, the server path
  uploads a re-encoded masked PNG as a raw body (`api.ts` `createImport`), and the file input is cleared
  (`ImportPage.tsx:58-62`).
- **Header spoofing**: `buildUpstreamHeaders` deletes all client IP/proxy-auth headers before setting
  `X-Client-IP` from `cf-connecting-ip` (set by Cloudflare) and `X-Proxy-Auth` only from env. Caddy overwrites
  `CF-Connecting-IP` and honours it only from `TRUSTED_PROXIES`.
- **API caching**: the proxy forces `no-store` and strips ETag/Last-Modified/Expires; the SW skips `/api/`.
- **CSRF**: the token is kept in memory and sent as `X-CSRF-Token` on non-GET requests, and cookies are first-party
  `SameSite=Lax` through the same-origin proxy.
- **Agorot**: Meitav/generic agorot rows keep price and cost in agorot with `unit: "agorot"`; `rowValueIls`
  and backend `cost_native` divide by 100. Values are in ILS.
- **Week start**: bars come from the server's `week_start`; the frontend computes no week boundaries.
- **Dockerfile.slim**: seed data is in `app/data`, alembic config is built in code, and `bootstrap-admin`
  always exits 0, so the `&&` chain cannot block startup.
