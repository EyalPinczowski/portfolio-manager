# UI decisions (user, 2026-10-03)

Binding for the frontend. Hebrew right-to-left is the default; English is the toggle.

| Topic | Decision |
|---|---|
| Theme | **Follow the phone setting** (light or dark); polish both. |
| Main currency | **Shekel (₪) large, dollar small** beside it. Each stock keeps its own currency too. |
| Navigation | **Bottom tab bar** on phones (Home, Portfolio, Analyze, Alerts, Settings), thumb-reachable; desktop may use a side or top bar. |
| Holding card | **Balanced:** name and ticker, price, day change, profit/loss, and a small status chip (stop set / needs holding period / stale price). Details open on tap. |
| Gain/loss colours | **Green up / red down, always with a +/- sign and an arrow** (readable without colour). While the launch gate is closed, score bars stay neutral (not green/red). |
| Home screen top | **Total value, today's change and "since you started" first**, then the week/month strip. |
| Actions (Review my portfolio / Suggest new stocks / Analyze a stock) | **A single "+" menu** (a round button that opens the three actions) instead of three big cards. On desktop it is the same menu. The tab bar already has Analyze, so the "+" opens the other actions plus a shortcut to Analyze. |
| First run | **A short guided setup, skippable:** accept the disclaimer → create a portfolio → import a screenshot → choose a risk level → set a holding period per stock (no default is ever filled in). Re-openable from Settings. |

Notes for the implementer:
- The "+" menu and the bottom tab bar must not overlap the last card (add bottom padding equal to the bar height plus the safe area).
- Keep everything in he/en with identical keys and test both directions (RTL/LTR).
- Do not use verdict wording on any new element.

## Phone-style settings (2026-10-04)
Settings looks and behaves like the phone's own Settings app (`components/SettingsUI.tsx` holds the primitives).
- **Hub** (`/settings`): large "Settings" title, a search field that filters rows, an account row (email) on top, then grouped rows: General (language, appearance, main currency, number format, week start), Notifications (price alerts and weekly review as inline switches, weekly review time, quiet hours, Telegram, new-idea alerts), Investing (risk limits), Help and status (system status, setup guide), Admin (admins only). Each row has a coloured icon tile, the label, the current value as muted trailing text and a chevron (mirrored in RTL), at least 48px high.
- **Drill-down** uses the existing `?section=` query (static-export safe): back link, large title, a subtle slide-in (only under `prefers-reduced-motion: no-preference`). `sessions` goes back to Account; everything else to the hub. The bottom tab bar stays and Settings stays the active tab.
- **Simple choices** (language, appearance, currency, number format, week start) are check lists on their own page and save at once. Idea alerts keep "nothing chosen" (no defaults, "Not set"). Time fields are native time inputs in a row.
- **Destructive actions** (sign out, delete account, disconnect Telegram) are red rows at the bottom of their group and always ask in a bottom sheet first; export and delete also ask for the password.
- Every group has a small grey footer for explanations. All text is he/en with identical keys; no buy/sell/hold wording (a test checks both languages).

## Edit and remove a holding by hand (Update 14)
- Every holding card (`HoldingsList.tsx`) and the holding page (`HoldingPage.tsx`) has **Edit** and **Remove** (`components/HoldingActions.tsx`), so a holding can be changed with no screenshot. The buttons sit outside the card link (no button inside a link) and use the plain secondary style, with the holding's name in the accessible label.
- **Edit** opens a modal with quantity, average cost (may be cleared), cost currency and holding period, using the same validation as the add form. It calls `PATCH /portfolios/{id}/holdings/{hid}`. The holding period is only sent if the user changed it, and "Decide later" stays an option (never a default).
- **Remove** always asks first ("Remove {name}?") and only then calls `DELETE`. Nothing is saved or deleted without the user's own action. Quantity 0 is not allowed on edit; removing is the delete.
- `HoldingOut` now carries `avg_cost` and `cost_currency`. All text is he/en (`holdingEdit` block); modals trap focus, close on Escape and work in RTL and at phone width.
