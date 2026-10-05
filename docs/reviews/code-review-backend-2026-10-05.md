# Backend code review, 2026-10-05 (read-only)

Intended path: `/tmp/claude-0/-home-user-portfolio-manager/72a97710-e735-5fee-9def-69692e19cbdf/scratchpad/code-review-backend-2026-10-05.md`.
Plan mode was on, so the report is saved in this plan file. Copy it to that path once writes are allowed.

Scope: `backend/app` (API scoping, money/FX/agorot, time zones, risk, secrets/logging, scheduler/limits/LLM ledger, deploy code).
Method: I traced each code path by reading it. Two findings were also checked by running the pure functions with `uv run python -c` (no files written).

**Counts:** high 1, medium 4, low 7. All 12 are listed below.

## What I checked that is fine
- **Auth scoping.** Every `{portfolio_id}`, `{holding_id}`, `{draft_id}`, `{alert_id}`, `{notification_id}`, `{conversation_id}` and `{session_id}` route goes through `app/repo.py` (`get_portfolio`, `get_holding`, `get_holding_in_portfolio`, `get_draft`, `get_alert`) or `committee/history.get_conversation`, and each of those checks the owner. A foreign id gets the same 404 as a missing one. The ask tools (`committee/ask.py`) call `repo` with the user id. Admin routes use `AdminDep`. I found no unscoped fetch-by-id.
- **Agorot.** Agorot are converted only by currency, at the provider layer (`providers/base.normalize_price` and `normalize_history`, dividends, importer `price_native` and `cost_native`). An ambiguous `.TA` hint of "ILS" is refused, so a price can never come out 100x too big.
- **FX direction.** `to_ils` multiplies a USD amount by ILS-per-USD. `to_usd` divides.
- **SQLite foreign keys.** SQLite runs with `PRAGMA foreign_keys=ON`, so the delete cascades work.
- **Missing data.** It gives `confidence=0`, and `combine.py` spreads that signal's weight over the others.
- **Secrets in logs.** httpx and httpcore log at WARNING, Telegram tokens are redacted, and `fallback_sources` error messages carry no URL or key.
- **Proxy auth.** The secret is compared in constant time. The production config validation is sound.
- **LLM ledger.** Usage counters are updated atomically and the token bucket uses compare-and-swap.
- **Leader lock.** The logic is correct.

---

## HIGH

### H1. Combined summary counts a later portfolio's first-day flows twice, which shows as a fake loss. CONFIRMED (reproduced)
- `app/portfolio/performance.py:134-136` (`combine_points`)
- **What is wrong.** When a portfolio joins the combined series after the earliest one, its whole first-day value is added as a deposit (`+= pt.value_ils`). Its own first-day `net_flow_ils` is also added (`+= pt.net_flow_ils`). That value already contains those flows, so they are counted twice.
- **Scenario.** Portfolio A starts on Oct 1 at 50,000 ILS. Portfolio B is created on Oct 5. Holding X (10,000) is added, which starts tracking. Then holding Y (10,000, priced) is added the same day, which records a `buy` flow of 10,000 dated Oct 5. The combined summary gives `period_result = pnl -10,000 ILS, -12.5%`, where it should be 0. I reproduced this with the pure functions. The phantom loss stays in "since start", in the weekly and monthly bars, in `/portfolios/combined/summary` and in the ask tool's summary. The importer path does the same when a second import is confirmed on the start day.
- **Fix.** On a portfolio's join day, add `pt.value_ils` as the deposit and skip `pt.net_flow_ils`:
  ```python
  if d == s[0].date and d != earliest:
      totals[d][1] += pt.value_ils
  else:
      totals[d][1] += pt.net_flow_ils
  ```
  Add a test with a same-day buy on the join day.

## MEDIUM

### M1. Yahoo quotes are stamped with the fetch time, not the bar time, so old closes count as fresh "live" prices. CONFIRMED (code trace)
- `app/providers/yfinance_provider.py:256-267` and `:317`
- **What is wrong.** `_quote_from_frame` takes the last close of a 5-day daily download and `build_quote(..., as_of=now)` stamps it with `basis="live"`. `portfolio/freshness.price_is_fresh` and `exit_level_price` judge freshness only from `as_of` and `basis`.
- **Scenario.** A TASE or US stock is halted or suspended for 3 days, or has had no trades since Thursday. The quote cycle stores Thursday's close with `as_of` set to the current time. `price_is_fresh` returns True, so exit levels, `/exit-review`, the screener and the Analyze "fit" compute stops and take-profits on a 3-day-old price. The "market open, but the quote did not move for a whole window" check can never fire. `change_pct` is also Thursday's move shown as today's. Separately, `store_quotes` never lets a correctly dated Finnhub quote replace a Yahoo row, because the Yahoo row always looks newer.
- **Fix.** Set `as_of` to the timestamp of the last bar, converted from the exchange's zone to naive UTC. If that bar's session date is before the current session, set `basis="last_close"`. A daily bar time is only a date, so a pragmatic version: if `closes.index[-1].date()` is earlier than the local session date, mark the quote `last_close` and set `as_of` to that session's close (from `calendars.session_close`).

### M2. The user's `week_start_day` setting is ignored by the portfolio summary. CONFIRMED (code trace)
- `app/portfolio/summary.py:82` and `:116`
- **What is wrong.** `week_start(today, s.week_start_day)` reads the global config. The per-user setting (`UserSettings.week_start_day`, editable through `PATCH /api/settings`, `api/settings.py:175`) is used only by the weekly review (`alerts/weekly_review.py:175`).
- **Scenario.** A user sets the week to start on Monday. `week_pnl`, the `weekly_bars` buckets and `week_start` in `/summary` still start on Sunday, while that user's weekly review uses Monday. CLAUDE.md says backend and frontend must agree on the week.
- **Fix.** Give `build_summary` a `week_start_day` parameter. Resolve it in the routes (and in the ask tool) with `usersettings.effective(db, user, s).week_start_day`.

### M3. If the 23:59 snapshot is missed on the tracking start day, a same-day buy is booked as profit. CONFIRMED (code trace; needs a missed job)
- `app/portfolio/valuation.py:475-479` (`take_catchup_snapshots`), `:505` (baseline snapshot) and `flows_since_previous_point` (`lo < t.date <= d`)
- **What is wrong.** The baseline snapshot is taken during the day, when the first holding is added. Later buys that day are flows dated that same day. Only the 23:59 upsert brings the baseline value up to date. If that job is missed, the catch-up skips the day because a row with `date >= day` already exists. The baseline then stays at "X only", and the buy flow falls outside the next interval `(baseline, next]`.
- **Scenario.** Holding X (10k) is added on the start day, then Y (10k) the same day, and the free host sleeps at 23:59. The next day shows +10k profit (+100%).
- **Fix.** Re-run `take_snapshot` for the tracking start day when the newest snapshot date equals `tracking_started_at`. Better: on catch-up, upsert the row for `day` whenever any flow is dated on or after that row's date.

### M4. A price alert sends Telegram before the database commit, so a failed commit means duplicate alerts every cycle. SUSPECTED
- `app/alerts/price_alerts.py:96` (send) vs `:101` (the single commit at the end)
- **What is wrong.** Each alert is claimed with an UPDATE and the Telegram message is sent inside one open transaction, which is committed only after the loop. If the commit fails (SQLite busy past 30 s, a Postgres error, a crash), the claims roll back but the messages are already out. The next quote cycle, about 5 minutes later, sends them again. On Postgres, one DB error inside the loop's `try` aborts the transaction, and every later claim in that run fails too.
- **Fix.** Commit the claim and the notification for each alert, or for the whole batch, before sending anything. Send Telegram after the commit. Wrap each alert in `db.begin_nested()`.

## LOW

### L1. Dual-listing merge keeps the first line's per-holding cap even when it is looser. CONFIRMED. **Changes risk logic: needs user approval.**
- `app/scoring/risk.py:169-170`. The second listing's override is adopted only when the first has none.
- **Scenario.** `TEVA.TA` has an override of 30% and `TEVA` has 10%. The merged position is checked against 30%, so no breach is reported at 20%.
- **Fix.** Use `min()` of the non-null overrides.

### L2. Committee daily cap is check-then-add, not atomic. CONFIRMED (trace)
- `app/api/analyze.py:300-307`. `quota_used` and `quota_add` are separate steps, and the routes are sync, so they run in a thread pool. Concurrent requests from one user at 9/10 can both pass. The hourly in-memory limit (5) bounds the overshoot.
- **Fix.** Do one conditional increment in the ledger (`UPDATE ... SET requests = requests + 1 WHERE requests < :cap`) and check `rowcount`.

### L3. `bootstrap-admin` can stop the container from starting, and hides other errors. CONFIRMED (trace)
- `app/cli.py:64-68`, which `backend/Dockerfile.slim:56` runs on every boot.
- **What is wrong.** Two instances booting at once can both pass the "exists" check. One then raises an uncaught `IntegrityError`, the `&&` chain fails and the container does not start. Any other `ValueError` is reported as "password too short" with exit code 0.
- **Fix.** Catch `IntegrityError` and report it as "already exists". Return a non-zero exit code on a real error.

### L4. User-scoped LLM answers survive account deletion. CONFIRMED (trace)
- `app/auth/account.py:118` and `models/tables.py` `LlmCache`
- **What is wrong.** Ask-my-portfolio answers are cached under `scope=user:<id>` in `llm_cache`, a table with no user column and no foreign key. `DELETE /me` cascades everything else, but these answers, which can mention the user's holdings, stay until the TTL runs out.
- **Fix.** Add a nullable `user_id` foreign key to `llm_cache` with ON DELETE CASCADE (a migration), or delete the user's keys in `delete_user`.

### L5. Fallback quotes hard-code UTC close times. CONFIRMED
- `app/providers/fallback_sources.py:307` (`21:00` for the US close) and `:438` (`15:00` for FX)
- **What is wrong.** This breaks the rule against hard-coded offsets. In US summer time the close is 20:00 UTC. The effect is small because `basis="last_close"` is never treated as fresh.
- **Fix.** Use `calendars.session_close(market, day)` converted to UTC.

### L6. `fallback_ip_header` is trusted without any check. SUSPECTED (depends on the host)
- `app/auth/ratelimit.py:239`
- **What is wrong.** A request without the proxy secret still has its configured fallback header (for example `CF-Connecting-IP`) trusted. On a host where clients can reach the origin directly and that header is not overwritten, an attacker can rotate the header to get fresh per-IP login and signup limits. With `require_proxy_auth` on, the risk applies only to the open paths, which have no limits.
- **Fix.** Trust the fallback header only when `trusted_proxy_cidrs` matches the peer. Otherwise document that it must only be set where the host overwrites it.

### L7. Unknown stored preset falls back to the code default, not the configured one. CONFIRMED. **Changes risk logic: needs user approval.**
- `app/scoring/risk.py`, `resolve_risk_filter`: `PRESETS.get(name, PRESETS[DEFAULT_PRESET])`
- **What is wrong.** A stored preset name that is invalid uses the hard-coded `balanced_aggressive`, not `settings.default_risk_preset`.
- **Fix.** Fall back to `s.default_risk_preset`.

---

## Fixes that need the user's approval
L1 and L7 change how risk limits are resolved. None of the other fixes touch scoring weights or risk logic. H1, M1 and M3 change displayed P&L and the freshness of exit levels, not weights.

## Suggested tests
- `combine_points` with a same-day flow on the join day (H1).
- `_quote_from_frame` with a frame whose last bar is from days ago, expecting `basis="last_close"` (M1).
- Summary built with a user whose `week_start_day` is Monday (M2).
- Catch-up after a missed snapshot on the baseline day with a same-day buy (M3).
- Price alerts where the commit fails, expecting no send or a single send (M4).
