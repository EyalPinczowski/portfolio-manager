"""Holidays and half-days from exchange_calendars (XTAE/XNYS) plus the config override."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlmodel import Session

from app.config import Settings
from app.models import Holding, Portfolio, User
from app.scheduler.calendars import (
    is_post_close_fetch_due,
    is_tase_open,
    is_us_open,
    session_close,
)
from app.scheduler.jobs import symbols_for_cycle

JLM = ZoneInfo("Asia/Jerusalem")
NY = ZoneInfo("America/New_York")


def jl(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=JLM)


def ny(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=NY)


# ---------------------------------------------------------------- TASE
def test_tase_closed_on_yom_kippur_2026() -> None:
    assert not is_tase_open(jl(2026, 9, 21, 11, 0))  # Monday, Yom Kippur
    assert is_tase_open(jl(2026, 9, 22, 11, 0))  # Tuesday after


def test_tase_closed_on_sukkot_friday_2026() -> None:
    assert not is_tase_open(jl(2026, 10, 2, 11, 0))  # Friday 2026-10-02 (Sukkot week)
    assert not is_tase_open(jl(2026, 9, 25, 11, 0))  # Friday, the eve of Sukkot


def test_tase_half_day_on_sukkot_chol_hamoed_2026() -> None:
    assert is_tase_open(jl(2026, 9, 28, 10, 0))  # Monday of Sukkot week
    assert is_tase_open(jl(2026, 9, 28, 14, 14))
    assert not is_tase_open(jl(2026, 9, 28, 14, 15))  # early close, not the regular 17:25
    assert not is_tase_open(jl(2026, 9, 28, 16, 0))
    # an ordinary Monday keeps the config close of 17:25
    assert is_tase_open(jl(2026, 10, 5, 17, 24))
    assert not is_tase_open(jl(2026, 10, 5, 17, 25))


def test_tase_config_override_beats_library() -> None:
    s = Settings(tase_holidays=[date(2026, 9, 22)], tase_extra_open_days=[date(2026, 9, 21)])
    assert not is_tase_open(jl(2026, 9, 22, 11, 0), s)  # forced closed
    assert is_tase_open(jl(2026, 9, 21, 11, 0), s)  # forced open although a holiday
    assert not is_tase_open(jl(2026, 9, 21, 18, 0), s)  # hours still from the config


def test_tase_hours_still_come_from_config_not_library() -> None:
    # The library models 17:15; the user override says 17:25 on Mon-Thu and 13:50 on Friday.
    assert is_tase_open(jl(2026, 3, 9, 17, 20))
    assert is_tase_open(jl(2026, 3, 6, 13, 49)) and not is_tase_open(jl(2026, 3, 6, 13, 50))
    assert not is_tase_open(jl(2026, 3, 8, 11, 0))  # Sunday


def test_tase_date_beyond_library_range_falls_back_to_config() -> None:
    assert is_tase_open(jl(2031, 3, 10, 11, 0))  # Monday, library silent: regular hours


# ---------------------------------------------------------------- US
def test_us_holidays_come_from_the_library_with_empty_config() -> None:
    assert Settings().us_holidays == []
    assert not is_us_open(ny(2026, 11, 26, 11, 0))  # Thanksgiving
    assert not is_us_open(ny(2026, 7, 3, 11, 0))  # Independence Day observed
    assert not is_us_open(ny(2026, 12, 25, 11, 0))
    assert is_us_open(ny(2026, 11, 25, 11, 0))


def test_us_half_days() -> None:
    assert is_us_open(ny(2026, 11, 27, 12, 59))  # day after Thanksgiving closes at 13:00
    assert not is_us_open(ny(2026, 11, 27, 13, 0))
    assert not is_us_open(ny(2026, 12, 24, 13, 0))
    assert is_us_open(ny(2026, 12, 24, 12, 59))
    assert is_us_open(ny(2026, 12, 23, 15, 59))  # a regular day runs to 16:00


def test_us_dst_boundary_uses_new_york_time() -> None:
    assert not is_us_open(datetime(2026, 3, 9, 13, 29, tzinfo=UTC))  # 09:29 EDT
    assert is_us_open(datetime(2026, 3, 9, 13, 30, tzinfo=UTC))
    assert is_us_open(datetime(2026, 3, 2, 14, 30, tzinfo=UTC))  # 09:30 EST


def test_us_config_override() -> None:
    s = Settings(us_holidays=[date(2026, 11, 25)], us_extra_open_days=[date(2026, 11, 26)])
    assert not is_us_open(ny(2026, 11, 25, 11, 0), s)
    assert is_us_open(ny(2026, 11, 26, 11, 0), s)


# ---------------------------------------------------------------- post-close fetch
def test_session_close_values() -> None:
    assert session_close("TASE", date(2026, 10, 5)) == jl(2026, 10, 5, 17, 25)
    assert session_close("TASE", date(2026, 9, 28)) == jl(2026, 9, 28, 14, 15)
    assert session_close("TASE", date(2026, 9, 21)) is None
    assert session_close("US", date(2026, 11, 27)) == ny(2026, 11, 27, 13, 0)


def test_one_post_close_fetch_window_per_session() -> None:
    # TASE closes 17:25 on Monday 2026-10-05; the window is [17:40, 17:45) with a 5-min interval.
    assert not is_post_close_fetch_due("TASE", jl(2026, 10, 5, 17, 39))
    assert is_post_close_fetch_due("TASE", jl(2026, 10, 5, 17, 40))
    assert is_post_close_fetch_due("TASE", jl(2026, 10, 5, 17, 44))
    assert not is_post_close_fetch_due("TASE", jl(2026, 10, 5, 17, 45))
    # half day: the extra fetch follows the early close
    assert is_post_close_fetch_due("TASE", jl(2026, 9, 28, 14, 30))
    assert not is_post_close_fetch_due("TASE", jl(2026, 9, 28, 17, 40))
    assert not is_post_close_fetch_due("TASE", jl(2026, 9, 21, 17, 40))  # holiday: nothing
    assert is_post_close_fetch_due("US", ny(2026, 11, 27, 13, 15))  # after the half day
    assert not is_post_close_fetch_due("CRYPTO", jl(2026, 10, 5, 17, 40))


def test_quote_cycle_includes_symbols_in_the_post_close_window(db: Session) -> None:
    user = User(email="c@mail.com", password_hash="x")
    db.add(user)
    db.commit()
    p = Portfolio(owner_id=user.id or 0, name="p")
    db.add(p)
    db.commit()
    db.add(Holding(portfolio_id=p.id or 0, symbol="TEVA.TA", quantity=1))
    db.commit()
    assert "TEVA.TA" in symbols_for_cycle(db, jl(2026, 10, 5, 17, 41))  # market closed, window
    assert "ILS=X" in symbols_for_cycle(db, jl(2026, 10, 5, 17, 41))
    assert "TEVA.TA" not in symbols_for_cycle(db, jl(2026, 10, 5, 17, 50))
