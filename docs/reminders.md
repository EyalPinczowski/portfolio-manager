# Reminders for the user

Things the user has said they will provide or decide later. **Claude must raise each one at the milestone listed**, in plain words, and ask for it. Remove an item when it is done.

| Remind at this milestone | Remind about |
|---|---|
| Right after block 2.0-E (importer fixes) is merged, and again before the deploy | **Send more broker screenshots** (the user said on 2026-10-03: "I will send in the future"): another screen or broker, a single holding's detail page, the Tel Aviv holdings list, a Hebrew-heavy screen. Hide names and account numbers. Used for parser fixtures (see `docs/import-formats.md`). |
| Before the deploy (after all Phase 2 features) | Create the free accounts and keys: Telegram bot (@BotFather), Gemini key, Supabase, Render, Cloudflare Pages + Turnstile, UptimeRobot. Exact steps are in `docs/deployment.md`. |
| Before inviting anyone outside the family | A short opinion from an Israeli lawyer (Investment Advice Law and the ISA directive on algorithmic advice). |
| When the Analyze page is built | Decide whether to show the optional TradingView chart widget (display-only, after a tap). |
| When "Review my portfolio" and the weekly review are built | Set your Buy alerts filter and your weekly review time (default Sunday 20:00). |
| Any time the user asks about past profit | Broker statements, if they want "since you started" to include earlier history. |
| Before enabling anything paid | The user must approve the exact service (for example the Claude API slot). |
| Before the deploy (quote fallback) | Create the free keys: **Finnhub** (finnhub.io, free key), **CoinGecko** demo key, **Financial Modeling Prep** (free key); optionally **TASE Data Hub** developer-portal signup (end-of-day securities data, free for internal use) and a **Stooq** key. Then decide whether to email vendors about multi-user use (see the terms note in `docs/reviews/quote-sources-2026-10-03.md`). |
- GemelNet: set `GEMELNET_RESOURCE_IDS={"monthly_returns": "<id>"}` (find via data.gov.il package_search) and run the live test that checks the column names; until then fund search says "unavailable" (manual entry works).
