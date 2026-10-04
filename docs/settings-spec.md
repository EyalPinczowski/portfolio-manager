# App settings spec (user request, 2026-10-03)

One **Settings** area, reachable from the bottom tab bar, organised in sections (a list of sections on a phone, each opening its own screen). All texts he/en with identical keys. Per-user settings are stored in a new `UserSettings` table (one row per user; expand-only Alembic revision) and exposed through typed, bounded, `extra="forbid"` models. **No setting may silently fill in a value the user must choose** (for example a holding period or a risk level).

## 1. Appearance
- Theme: follow phone (default) / light / dark.
- Language: Hebrew (default) / English.
- Main currency on the home screen: ₪ (default) / $ (the other is shown small beside it).
- Number format: full or compact (for example 1.2K).

## 2. Portfolio
- Default portfolio to open (or "all together").
- Week starts on: Sunday (default) / Monday (the global default stays Sunday; this is per user).
- "Remind me to update from screenshots after N days" (default 7, off possible).
- Risk preset per portfolio and per-stock overrides (existing screens, linked from here).

## 3. Notifications
- **Telegram:** Connect / Disconnect. One shared bot for the whole app: the user presses "Connect Telegram", gets a one-time code (valid 15 minutes), sends `/start <code>` to the bot, and the server stores their chat id. Disconnect deletes it. (New endpoints: `POST /api/me/telegram/link-code`, `DELETE /api/me/telegram`; the bot webhook or poller handles `/start`.)
- Price alerts on/off.
- Weekly review: on/off, day and time (default Sunday 20:00, Asia/Jerusalem).
- Buy alerts: filter (minimum confidence, holding period, risk filter, markets, max per day, quiet hours). **Nothing is sent until the user saves this filter, and nothing verdict-like is sent while the launch gate is closed** (the screen says so).
- Quiet hours for all notifications.

## 4. Privacy and AI
- AI explanations: on/off. When on, free AI providers receive **public stock data only**; holdings, amounts and notes never leave the app (templates are used for those). Shows which providers are active (read-only) and a note that free providers may train on prompts.
- Reading screenshots: on this phone (default) or on the server (explicit opt-in with the consent notice; shown as unavailable when the server has no Tesseract).
- Trusted devices and active sessions (list, revoke one, revoke all).
- Export my data (password required) and delete my account (password required, with confirmation).

## 5. System (read-only for members)
- Status from `/api/health` (scheduler leader state, last prices and snapshot times), exchange-rate staleness, launch-gate state with its reasons, app version, the disclaimer text.

## 6. Admin (admin users only)
- **Invites:** create, list, revoke invite codes (replaces the `create-invite` CLI for day-to-day use; copy link/code).
- **Users:** list (email, created, last seen), deactivate; never shows portfolios.
- **AI usage:** today's requests, tokens and fallbacks per provider and model, from the `LlmUsage` ledger; whether a paid provider is enabled (read-only; enabling stays an operator decision).
- **Turnstile and proxy configuration state** from `check-config` (read-only).

## Backend work
1. `UserSettings` model + Alembic revision; `GET /api/me/settings` and `PATCH /api/me/settings` (typed sections, bounds, `extra="forbid"`, scoping tests).
2. Telegram link-code flow with a bot `/start` handler (a long-poll worker in the scheduler process or a webhook route protected by the Telegram secret token), rate-limited, with tests using a fake Telegram client; code is single-use and expires in 15 minutes; `chat_id` can be linked to one account only.
3. Admin endpoints (`/api/admin/invites`, `/api/admin/users`, `/api/admin/llm-usage`), admin-only dependency, tests that a normal user gets 403 and that no endpoint exposes another user's portfolio.
4. The notification code reads `UserSettings` (alert on/off, quiet hours, weekly review time) and respects it; the verdict gate still wins.

## Frontend work
Sections as above, mobile-first; changes save immediately with a small "saved" confirmation (no big Save button), dangerous actions need a confirmation and the password; every control has a clear Hebrew/English label and helper text; tests in both directions (RTL/LTR) and both themes.
