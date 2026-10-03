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
