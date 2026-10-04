"""`GET/PATCH /api/settings`: defaults, strict bodies, bounds, scoping, export and cascade."""

from __future__ import annotations

from collections.abc import Callable
from datetime import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.db import new_session
from app.models import UserSettings
from app.usersettings import in_quiet_hours

SignupFn = Callable[..., TestClient]
PW = "correct horse battery"

IDEA: dict[str, Any] = {
    "min_confidence": 0.6,
    "horizon": "3m",
    "risk_preset": "balanced",
    "markets": ["US", "TASE"],
    "asset_types": ["stock", "etf"],
    "max_per_day": 3,
    "quiet_hours": {"start": "22:00", "end": "07:00"},
}


def test_defaults_before_anything_is_saved(signup: SignupFn) -> None:
    a = signup()
    body = a.get("/api/settings").json()
    assert body["language"] == "en"  # the signup locale
    assert body["week_start_day"] == "sunday"
    assert body["main_currency"] == "ILS"
    assert body["weekly_review"] == {
        "enabled": True,
        "day": "sunday",
        "time": "20:00",
        "timezone": "Asia/Jerusalem",
    }
    assert body["idea_alerts"] == {"state": "not_set", "filter": None}  # no defaults, no pushes
    assert body["quiet_hours"] is None
    assert body["telegram_linked"] is False
    assert body["updated_at"] is None
    with new_session() as db:  # a GET never writes
        assert db.exec(select(UserSettings)).all() == []


def test_patch_saves_and_is_read_back(signup: SignupFn) -> None:
    a = signup()
    r = a.patch(
        "/api/settings",
        json={
            "language": "he",
            "theme": "dark",
            "main_currency": "USD",
            "number_format": "compact",
            "week_start_day": "monday",
            "price_alerts_enabled": False,
            "weekly_review": {"enabled": True, "day": "friday", "time": "09:30"},
            "quiet_hours": {"start": "23:00", "end": "06:30"},
        },
    )
    assert r.status_code == 200, r.text
    got = a.get("/api/settings").json()
    assert got["language"] == "he" and a.get("/api/auth/me").json()["locale"] == "he"
    assert got["theme"] == "dark" and got["main_currency"] == "USD"
    assert got["week_start_day"] == "monday" and got["price_alerts_enabled"] is False
    assert got["weekly_review"]["day"] == "friday" and got["weekly_review"]["time"] == "09:30"
    assert got["quiet_hours"] == {"start": "23:00", "end": "06:30"}
    assert got["updated_at"] is not None
    # a partial patch leaves the rest alone
    a.patch("/api/settings", json={"weekly_review": {"enabled": False}})
    again = a.get("/api/settings").json()
    assert again["weekly_review"] == {
        "enabled": False,
        "day": "friday",
        "time": "09:30",
        "timezone": "Asia/Jerusalem",
    }
    assert again["theme"] == "dark"
    a.patch("/api/settings", json={"quiet_hours": None})
    assert a.get("/api/settings").json()["quiet_hours"] is None


@pytest.mark.parametrize(
    "body",
    [
        {},  # nothing to change
        {"unknown": 1},  # extra=forbid
        {"theme": "purple"},
        {"theme": None},
        {"language": "fr"},
        {"main_currency": "EUR"},
        {"week_start_day": "friday"},
        {"price_alerts_enabled": "yes"},
        {"weekly_review": {}},
        {"weekly_review": {"day": "funday"}},
        {"weekly_review": {"time": "25:00"}},
        {"weekly_review": {"time": "9:30"}},
        {"weekly_review": {"enabled": None}},
        {"weekly_review": {"extra": 1}},
        {"quiet_hours": {"start": "22:00", "end": "22:00"}},
        {"quiet_hours": {"start": "22:00"}},
        {"idea_alerts": {**IDEA, "min_confidence": 1.5}},
        {"idea_alerts": {**IDEA, "min_confidence": -0.1}},
        {"idea_alerts": {**IDEA, "min_confidence": "0.5"}},
        {"idea_alerts": {**IDEA, "max_per_day": 0}},
        {"idea_alerts": {**IDEA, "max_per_day": 21}},
        {"idea_alerts": {**IDEA, "markets": []}},
        {"idea_alerts": {**IDEA, "markets": ["US", "US"]}},
        {"idea_alerts": {**IDEA, "markets": ["MARS"]}},
        {"idea_alerts": {**IDEA, "horizon": "2w"}},
        {"idea_alerts": {**IDEA, "risk_preset": "yolo"}},
        {"idea_alerts": {**IDEA, "extra": 1}},
    ],
)
def test_bad_bodies_are_rejected_and_nothing_is_saved(
    signup: SignupFn, body: dict[str, Any]
) -> None:
    a = signup()
    assert a.patch("/api/settings", json=body).status_code == 422
    with new_session() as db:
        assert db.exec(select(UserSettings)).all() == []


@pytest.mark.parametrize("missing", sorted(IDEA))
def test_the_idea_alerts_filter_has_no_defaults(signup: SignupFn, missing: str) -> None:
    a = signup()
    body = {k: v for k, v in IDEA.items() if k != missing}
    assert a.patch("/api/settings", json={"idea_alerts": body}).status_code == 422
    assert a.get("/api/settings").json()["idea_alerts"]["state"] == "not_set"


def test_non_finite_numbers_are_rejected(signup: SignupFn) -> None:
    a = signup()
    r = a.patch(
        "/api/settings",
        content=b'{"idea_alerts": {"min_confidence": NaN}}',
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422


def test_idea_alerts_filter_set_then_cleared(signup: SignupFn) -> None:
    a = signup()
    r = a.patch("/api/settings", json={"idea_alerts": IDEA})
    assert r.status_code == 200, r.text
    assert r.json()["idea_alerts"] == {"state": "set", "filter": IDEA}
    # no-quiet-hours is an explicit choice (null), not a missing key
    r = a.patch("/api/settings", json={"idea_alerts": {**IDEA, "quiet_hours": None}})
    assert r.json()["idea_alerts"]["filter"]["quiet_hours"] is None
    r = a.patch("/api/settings", json={"idea_alerts": None})
    assert r.json()["idea_alerts"] == {"state": "not_set", "filter": None}


def test_settings_are_scoped_to_the_caller(signup: SignupFn) -> None:
    a = signup("a@mail.com")
    b = signup("b@mail.com")
    a.patch("/api/settings", json={"theme": "dark", "idea_alerts": IDEA})
    got = b.get("/api/settings").json()
    assert got["theme"] == "system" and got["idea_alerts"]["state"] == "not_set"
    b.patch("/api/settings", json={"theme": "light"})
    assert a.get("/api/settings").json()["theme"] == "dark"
    with new_session() as db:
        assert sorted(r.user_id for r in db.exec(select(UserSettings)).all()) == [1, 2]


def test_settings_are_in_the_export_and_cascade_on_account_delete(signup: SignupFn) -> None:
    a = signup("a@mail.com")
    a.patch("/api/settings", json={"theme": "dark", "idea_alerts": IDEA})
    exported = a.post("/api/me/export", json={"password": PW}).json()
    assert exported["settings"]["theme"] == "dark"
    assert exported["settings"]["idea_alerts"]["max_per_day"] == 3
    b = signup("b@mail.com")
    assert b.post("/api/me/export", json={"password": PW}).json()["settings"] is None
    assert a.request("DELETE", "/api/me", json={"password": PW}).status_code == 204
    with new_session() as db:
        assert db.exec(select(UserSettings)).all() == []


def test_unauthenticated_and_csrf(client: TestClient, signup: SignupFn) -> None:
    assert client.get("/api/settings").status_code == 401
    assert client.patch("/api/settings", json={"theme": "dark"}).status_code == 401
    a = signup()
    del a.headers["X-CSRF-Token"]
    assert a.patch("/api/settings", json={"theme": "dark"}).status_code == 403


@pytest.mark.parametrize(
    ("now", "inside"),
    [
        (time(22, 0), True),
        (time(23, 59), True),
        (time(0, 0), True),
        (time(6, 59), True),
        (time(7, 0), False),
        (time(12, 0), False),
        (time(21, 59), False),
    ],
)
def test_quiet_hours_wrap_midnight(now: time, inside: bool) -> None:
    assert in_quiet_hours({"start": "22:00", "end": "07:00"}, now) is inside


def test_quiet_hours_same_day_window_and_none() -> None:
    q = {"start": "13:00", "end": "14:00"}
    assert in_quiet_hours(q, time(13, 30)) and not in_quiet_hours(q, time(14, 0))
    assert not in_quiet_hours(None, time(3, 0))
    assert not in_quiet_hours({"start": "08:00", "end": "08:00"}, time(8, 0))
