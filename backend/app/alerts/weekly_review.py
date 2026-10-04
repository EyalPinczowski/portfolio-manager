"""Weekly review: one Telegram message per user per week, from the user's own settings.

The job runs every few minutes. A user gets the message when all of these hold: Telegram is linked,
the account is active, the weekly review is on (default Sunday 20:00 Asia/Jerusalem, per-user
override), the local time has reached the chosen time on the chosen day, this week's review was not
sent yet and the time is outside the user's quiet hours (a quiet moment only delays it, until the
chosen day ends).

The text is a fixed template filled with numbers from existing code (`build_summary`, the
exit-levels review, the post-mortem); no LLM, no free text (portfolio names are not included), so
it can never carry a verdict while the launch gate is closed. It carries the disclaimer.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import update
from sqlmodel import Session, col, select

from app.alerts.telegram import TelegramSender, get_sender
from app.config import DISCLAIMER, Settings, get_settings
from app.models import Portfolio, User, UserSettings
from app.outbound import TemplateText, join_lines, render
from app.portfolio.performance import WEEKDAY_INDEX, week_start
from app.portfolio.summary import build_summary
from app.providers.base import HistoryProvider
from app.usersettings import default_row, effective, in_quiet_hours, parse_hhmm

log = logging.getLogger(__name__)

TITLE = "Weekly review for the week starting {week_start}."
VALUE = "Portfolio value {value} ILS. This week {pnl} ILS ({pct}%)."
PORTFOLIO = "Portfolio {n}: {with_stop} of {positions} positions have a stop level."
RISK = "Total risk to the stops is {risk_pct}% of the portfolio (limit {limit_pct}%)."
OVER = "The total risk to the stops is above your limit."
NEEDS_HORIZON = "Portfolio {n}: {count} positions need a holding period before levels can be shown."
EXPECTATION = "Portfolio {n}: {gap} percentage points against your expected return."
STALE = "Prices are stale in part of this review: it uses the last known values."
FOOTER = "Details are in the app. " + DISCLAIMER


def build_weekly_review(
    db: Session,
    user: User,
    history: HistoryProvider | None,
    settings: Settings,
    week: date,
    now: datetime | None = None,
) -> TemplateText | None:
    """The message text, or None when the user has no portfolio."""
    from app.api.exit_levels import ExitReviewIn, exit_review
    from app.portfolio.postmortem_data import post_mortem_for_portfolio
    from app.timeutil import local_today

    assert user.id is not None
    portfolios = list(db.exec(select(Portfolio).where(Portfolio.owner_id == user.id)).all())
    if not portfolios:
        return None
    s = settings
    summary = build_summary(db, portfolios, history, s, now)
    lines: list[TemplateText] = [render(TITLE, settings=s, week_start=week)]
    wk = summary["week_pnl"]
    lines.append(
        render(
            VALUE,
            settings=s,
            value=round(summary["value"]["ils"]),
            pnl=round(wk["ils"]),
            pct=round(wk["pct"], 2),
        )
    )
    today = local_today(now)
    for n, p in enumerate(portfolios, start=1):
        review = exit_review(p.id or 0, ExitReviewIn(), user, db, s)  # same numbers as the screen
        tot = review.totals
        lines.append(
            render(
                PORTFOLIO,
                settings=s,
                n=n,
                with_stop=tot.positions_with_stop,
                positions=len(review.rows),
            )
        )
        if tot.positions_with_stop:
            lines.append(
                render(
                    RISK,
                    settings=s,
                    risk_pct=round(tot.total_risk_pct_of_portfolio, 2),
                    limit_pct=round(tot.limit_pct, 2),
                )
            )
        if tot.over_limit:
            lines.append(render(OVER, settings=s))
        missing = sum(1 for r in review.rows if r.status == "needs_horizon")
        if missing:
            lines.append(render(NEEDS_HORIZON, settings=s, n=n, count=missing))
        try:
            pm = post_mortem_for_portfolio(db, p, history, s, None, None, today)
        except Exception as exc:  # the review still goes out without this line
            log.warning("weekly review: post-mortem unavailable: %s", type(exc).__name__)
            continue
        if pm.status == "ok" and pm.expectation.gap_pp is not None:
            lines.append(render(EXPECTATION, settings=s, n=n, gap=round(pm.expectation.gap_pp, 2)))
    if summary["fx_stale"]:
        lines.append(render(STALE, settings=s))
    lines.append(render(FOOTER, settings=s))
    return join_lines(*lines)


def _claim_week(db: Session, user_id: int, previous: date | None, week: date) -> bool:
    """Atomically mark `week` as sent-in-progress; False when another process got there first."""
    result = db.execute(
        update(UserSettings)
        .where(
            col(UserSettings.user_id) == user_id,
            col(UserSettings.last_weekly_review_week).is_(None)
            if previous is None
            else col(UserSettings.last_weekly_review_week) == previous,
        )
        .values(last_weekly_review_week=week)
    )
    db.commit()
    return bool(result.rowcount == 1)  # type: ignore[attr-defined]


def _release_week(db: Session, user_id: int, previous: date | None, week: date) -> None:
    db.execute(
        update(UserSettings)
        .where(
            col(UserSettings.user_id) == user_id, col(UserSettings.last_weekly_review_week) == week
        )
        .values(last_weekly_review_week=previous)
    )
    db.commit()


def due(eff_enabled: bool, day: str, at: str, local: datetime) -> bool:
    """On, on the chosen weekday and the chosen time has been reached (same day only)."""
    return eff_enabled and WEEKDAY_INDEX[day] == local.weekday() and local.time() >= parse_hhmm(at)


def run_weekly_review(
    db: Session,
    history: HistoryProvider | None = None,
    settings: Settings | None = None,
    sender: TelegramSender | None = None,
    now: datetime | None = None,
) -> int:
    """Send every review that is due now. Returns how many were sent."""
    s = settings or get_settings()
    send = sender or get_sender(s)
    now = now or datetime.now(UTC)
    local = now.astimezone(ZoneInfo(s.scheduler_timezone))
    users = db.exec(
        select(User).where(col(User.telegram_chat_id).is_not(None), col(User.disabled_at).is_(None))
    ).all()
    sent = 0
    for user in list(users):
        uid = user.id
        if uid is None:
            continue
        try:
            eff = effective(db, user, s)
            if not due(
                eff.weekly_review_enabled, eff.weekly_review_day, eff.weekly_review_time, local
            ):
                continue
            if in_quiet_hours(eff.quiet_hours, local.time()):
                continue
            week = week_start(local.date(), eff.week_start_day)
            previous = eff.last_weekly_review_week
            if previous == week:
                continue
            text = build_weekly_review(db, user, history, s, week, now)
            if text is None:
                continue
            if not eff.has_row:  # the dedupe marker lives on the row
                db.add(default_row(uid, s))
                db.commit()
            if not _claim_week(db, uid, previous, week):
                continue
            ok = False
            try:
                ok = send.send(user.telegram_chat_id, text)
            finally:
                if not ok:
                    _release_week(db, uid, previous, week)  # retried on the next run
            sent += 1 if ok else 0
        except Exception as exc:  # one user never stops the others; no ids or chat data in the log
            db.rollback()
            log.warning("weekly review failed for one user: %s", type(exc).__name__)
    return sent
