# Frontend

Next.js (App Router) + TypeScript (strict) + Tailwind v4 + next-intl (`he` default, RTL; `en`) + SWR (60 s polling) + `lightweight-charts`. Production output is a **fully static export** (`out/`).

```bash
npm install                 # also copies the OCR engine files into public/tesseract/ (postinstall)
npm run dev                 # proxies /api to http://localhost:8000 (override with API_PROXY_TARGET)
npm run dev:mock            # NEXT_PUBLIC_API_MOCK=1: fixture data, no backend needed (any login works)
npm run lint && npm run typecheck && npm test && npm run build
npm run gen:api             # regenerate lib/api-schema.d.ts from ../backend/openapi.json
```

## Layout and conventions
- API client and types: `lib/api.ts`. Types are **derived from the generated** `lib/api-schema.d.ts`; contract items the backend has not published yet are hand-written in `lib/api-pending.ts` (see its header). Mock fixtures: `lib/mock.ts`; `tests/mock-contract.test.ts` validates every mock response against `backend/openapi.json` with Ajv, so mocks cannot drift.
- Strings: `messages/he.json` and `messages/en.json` (a test enforces identical keys).
- Pages: `/[locale]` (main), `/[locale]/holding?id=123`, `/import`, `/xray`, `/settings`, `/login`, `/signup`. The holding page is query-based because a static export has no dynamic segment; `lib/routes.ts` builds the link. `/` redirects to the browser language (`he` or `en`).
- Weeks run Sunday to Saturday; the summary's `week_start` is shown in labels.
- Phase 1 buttons (Review / Suggest / Analyze) are intentionally disabled. The app never shows buy/sell verdicts.
- Settings: password prompt for export/delete (wrong password -> 403), signed-in devices list with revoke.
- Logout, login, signup and account deletion clear the SWR cache and the stored portfolio (`lib/session.ts`).

## Screenshot import (privacy)
- Default: **on-device OCR**. `tesseract.js` (Hebrew + English) is loaded lazily (dynamic import, only the import page). Worker, WASM core and language data are self-hosted in `public/tesseract/` (copied by `scripts/copy-ocr-assets.mjs`, git-ignored), so no third-party host is contacted. The image is decoded to a canvas, the top 12% is blanked, OCR runs, the canvas is zeroed; nothing is written to localStorage/IndexedDB and no blob URL is kept. Text is parsed in the browser (`lib/ocr/parse.ts`, a port of `backend/app/importer/parse.py`), then only the rows go to `POST /api/portfolios/{id}/imports/rows`.
- Optional "Use server reading": behind a consent notice; sends a re-encoded, top-blanked PNG as the **raw request body** (`Content-Type: image/png`, not multipart).
- The import screen says: "We keep only the stock list. The screenshot is deleted right away."

## Static export and hosting
`next build` writes `out/` (`output: "export"`, `trailingSlash: true`, both locales), then `scripts/finalize-export.mjs` checks it. `NEXT_PUBLIC_API_URL` defaults to empty (same origin); the browser always calls `/api/...` and the host forwards it.

**Cloudflare Pages** (card-free): build command `npm run build`, output directory `out`, Node 22. Set the environment binding `API_ORIGIN` (e.g. `https://my-api.onrender.com`, no trailing slash). `functions/api/[[path]].ts` forwards `/api/*` to it: same method, body and cookies; `CF-Connecting-IP` is set from the incoming one (client-sent `X-Forwarded-For` and friends are dropped); hop-by-hop headers are stripped; responses are never cached. The API's rate limiter must trust only `CF-Connecting-IP` from this proxy. `public/_headers` supplies the CSP and other security headers for the static files. Test the function locally with `npx wrangler pages dev out --binding API_ORIGIN=http://localhost:8000`. Header logic is in `lib/proxy-headers.ts` (unit-tested).

**Docker** (`docker build -t pm-web frontend`): a static-serve image (Caddy) with the same `/api` proxy; run with `-e API_ORIGIN=http://api:8000`. It replaces the old Node standalone image.

## Security headers
`lib/security-headers.ts` is the single source for the CSP. `next.config.ts` applies it in `next dev`; production uses `public/_headers` (Cloudflare) or `Caddyfile` (Docker); a test fails if they differ. The CSP needs `'wasm-unsafe-eval'` (tesseract's WebAssembly, not JS eval) and `worker-src 'self'` (the OCR worker is same-origin). `script-src` includes `'unsafe-inline'` because a static export has no per-request nonce for Next's inline bootstrap scripts. If `NEXT_PUBLIC_API_URL` points to another origin, add it to `connect-src` in all three places.

PWA: `public/manifest.webmanifest`, `public/sw.js` (offline shell only, never caches `/api`), icons from `scripts/gen-icons.mjs`.
