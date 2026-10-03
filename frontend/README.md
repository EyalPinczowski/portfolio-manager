# Frontend

Next.js (App Router) + TypeScript (strict) + Tailwind v4 + next-intl (`he` default, RTL; `en`) + SWR (60 s polling) + `lightweight-charts`.

```bash
npm install
npm run dev          # proxies /api to http://localhost:8000 (override with API_PROXY_TARGET)
npm run dev:mock     # NEXT_PUBLIC_API_MOCK=1: fixture data, no backend needed (any login works)
npm run lint && npm run typecheck && npm test && npm run build
```

- Typed API client and types: `lib/api.ts` (cookie auth, `X-CSRF-Token` on mutations). Mock fixtures: `lib/mock.ts`.
- Strings live in `messages/he.json` and `messages/en.json`; a test enforces identical keys.
- Pages: `/[locale]` (main), `/holding/[id]`, `/import`, `/xray`, `/settings`, `/login`, `/signup`.
- PWA: `public/manifest.webmanifest`, `public/sw.js` (offline shell only, never caches `/api`), icons from `scripts/gen-icons.mjs`.
- Docker: `docker build -t pm-web frontend` (standalone output, non-root, node:22-alpine).
- Phase 1 buttons (Review / Suggest / Analyze) are intentionally disabled. The app never shows buy/sell verdicts.
