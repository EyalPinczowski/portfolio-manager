"""Search history and watchlist: symbols only, caller-scoped, capped, exported, cascaded."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import SQLModel, select

from app.config import get_settings
from app.db import new_session
from app.models import PriceQuote, SearchHistory, WatchlistItem
from app.timeutil import utcnow
from app.userlists import record_search
from tests.conftest import FakeHistory, FakeQuotes
from tests.test_exit_levels import frame

SignupFn = Callable[..., TestClient]
PW = "correct horse battery"


@pytest.fixture
def two(
    signup: SignupFn, quotes: FakeQuotes, history: FakeHistory
) -> tuple[TestClient, TestClient]:
    df = frame()
    for sym in ("AAPL", "MSFT", "NVDA"):
        history.frames[sym] = df
        quotes.set(sym, float(df["Close"].iloc[-1]), "USD")
    return signup("a@mail.com"), signup("b@mail.com")


def test_successful_analyze_records_symbol_only_and_failures_do_not(
    two: tuple[TestClient, TestClient],
) -> None:
    a, b = two
    assert a.get("/api/analyze/AAPL").status_code == 200
    assert a.get("/api/analyze/^GSPC").status_code == 422
    assert a.get("/api/analyze/bad%20sym").status_code == 422
    assert a.get("/api/analyze/ZZZNOPE").status_code == 404
    hist = a.get("/api/search-history").json()
    assert [h["symbol"] for h in hist] == ["AAPL"]
    assert set(hist[0]) == {"symbol", "searched_at"}
    assert b.get("/api/search-history").json() == []


def test_upsert_bumps_time_and_keeps_one_row(two: tuple[TestClient, TestClient]) -> None:
    a, _ = two
    a.get("/api/analyze/AAPL")
    a.get("/api/analyze/MSFT")
    with new_session() as db:
        row = db.exec(select(SearchHistory).where(SearchHistory.symbol == "AAPL")).one()
        row.searched_at = utcnow() - timedelta(days=2)
        db.add(row)
        db.commit()
    a.get("/api/analyze/aapl")  # case-insensitive, same row
    hist = a.get("/api/search-history").json()
    assert [h["symbol"] for h in hist] == ["AAPL", "MSFT"]
    with new_session() as db:
        assert len(db.exec(select(SearchHistory)).all()) == 2


def test_history_cap_prunes_oldest(
    two: tuple[TestClient, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert get_settings().max_search_history_per_user == 50
    assert get_settings().max_watchlist_per_user == 100
    a, b = two
    with new_session() as db:
        for i, sym in enumerate(("AAA", "BBB", "CCC", "DDD")):
            db.add(
                SearchHistory(user_id=1, symbol=sym, searched_at=utcnow() - timedelta(hours=9 - i))
            )
        db.add(SearchHistory(user_id=2, symbol="OTHER", searched_at=utcnow()))
        db.commit()
        record_search(db, 1, "EEE", cap=3)
    assert [h["symbol"] for h in a.get("/api/search-history").json()] == ["EEE", "DDD", "CCC"]
    assert [h["symbol"] for h in b.get("/api/search-history").json()] == ["OTHER"]  # untouched


def test_history_delete_one_and_clear_are_scoped(two: tuple[TestClient, TestClient]) -> None:
    a, b = two
    for c in (a, b):
        c.get("/api/analyze/AAPL")
        c.get("/api/analyze/MSFT")
    assert b.delete("/api/search-history/NVDA").status_code == 404
    assert a.delete("/api/search-history/AAPL").status_code == 204
    assert a.delete("/api/search-history/AAPL").status_code == 404
    assert a.delete("/api/search-history/^GSPC").status_code == 422
    assert [h["symbol"] for h in a.get("/api/search-history").json()] == ["MSFT"]
    assert len(b.get("/api/search-history").json()) == 2  # B's AAPL survived A's delete
    assert a.delete("/api/search-history").status_code == 204
    assert a.get("/api/search-history").json() == []
    assert len(b.get("/api/search-history").json()) == 2


def test_watchlist_add_list_delete_duplicate_and_validation(
    two: tuple[TestClient, TestClient],
) -> None:
    a, b = two
    r = a.post("/api/watchlist", json={"symbol": "aapl"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["symbol"] == "AAPL" and body["market"] == "US" and body["name"]
    assert body["quote"] is None  # no cached quote yet; no provider is called
    assert a.post("/api/watchlist", json={"symbol": "AAPL"}).status_code == 409
    assert b.post("/api/watchlist", json={"symbol": "AAPL"}).status_code == 201  # per user
    for bad in (
        {"symbol": ""},
        {"symbol": "A B"},
        {"symbol": "^GSPC"},
        {"symbol": "AAPL", "x": 1},
        {},
    ):
        assert a.post("/api/watchlist", json=bad).status_code == 422, bad
    assert a.post("/api/watchlist", json={"symbol": "SOMEUNKNOWN"}).status_code == 201
    unknown = next(w for w in a.get("/api/watchlist").json() if w["symbol"] == "SOMEUNKNOWN")
    assert unknown["market"] is None and unknown["name"] is None
    assert b.delete("/api/watchlist/SOMEUNKNOWN").status_code == 404
    assert a.delete("/api/watchlist/SOMEUNKNOWN").status_code == 204
    assert a.delete("/api/watchlist/SOMEUNKNOWN").status_code == 404
    assert [w["symbol"] for w in a.get("/api/watchlist").json()] == ["AAPL"]


def test_watchlist_cap_is_422_and_per_user(
    two: tuple[TestClient, TestClient], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAX_WATCHLIST_PER_USER", "2")
    get_settings.cache_clear()
    a, b = two
    codes = [
        a.post("/api/watchlist", json={"symbol": s}).status_code for s in ("AAPL", "MSFT", "NVDA")
    ]
    assert codes == [201, 201, 422]
    assert b.post("/api/watchlist", json={"symbol": "NVDA"}).status_code == 201
    a.delete("/api/watchlist/AAPL")
    assert a.post("/api/watchlist", json={"symbol": "NVDA"}).status_code == 201


def test_watchlist_shows_cached_quote_only(
    two: tuple[TestClient, TestClient], quotes: FakeQuotes
) -> None:
    a, _ = two
    a.post("/api/watchlist", json={"symbol": "AAPL"})
    with new_session() as db:
        db.add(PriceQuote(symbol="AAPL", price=190.5, currency="USD", as_of=utcnow()))
        db.commit()
    calls_before = list(getattr(quotes, "calls", []))
    q = a.get("/api/watchlist").json()[0]["quote"]
    assert q["price"] == 190.5 and q["currency"] == "USD" and q["basis"] == "live"
    assert isinstance(q["is_fresh"], bool)
    assert list(getattr(quotes, "calls", [])) == calls_before  # no provider call


def test_lists_need_login(client: TestClient) -> None:
    for method, path, body in [
        ("GET", "/api/search-history", None),
        ("DELETE", "/api/search-history", None),
        ("DELETE", "/api/search-history/AAPL", None),
        ("GET", "/api/watchlist", None),
        ("POST", "/api/watchlist", {"symbol": "AAPL"}),
        ("DELETE", "/api/watchlist/AAPL", None),
    ]:
        assert client.request(method, path, json=body).status_code == 401, (method, path)


def test_tables_hold_no_analysis_output() -> None:
    cols = {
        t: set(SQLModel.metadata.tables[t].columns.keys())
        for t in ("search_history", "watchlist_item")
    }
    assert cols["search_history"] == {"id", "user_id", "symbol", "searched_at"}
    assert cols["watchlist_item"] == {"id", "user_id", "symbol", "market", "name", "added_at"}


def test_export_includes_and_user_delete_cascades(two: tuple[TestClient, TestClient]) -> None:
    a, b = two
    a.get("/api/analyze/AAPL")
    a.post("/api/watchlist", json={"symbol": "MSFT"})
    b.get("/api/analyze/NVDA")
    mine = a.post("/api/me/export", json={"password": PW}).json()
    assert [h["symbol"] for h in mine["search_history"]] == ["AAPL"]
    assert [w["symbol"] for w in mine["watchlist"]] == ["MSFT"]
    theirs = b.post("/api/me/export", json={"password": PW}).json()
    assert [h["symbol"] for h in theirs["search_history"]] == ["NVDA"] and theirs["watchlist"] == []
    assert a.request("DELETE", "/api/me", json={"password": PW}).status_code == 204
    with new_session() as db:  # raw count: the database cascaded, not the ORM
        for table in ("search_history", "watchlist_item"):
            n = db.connection().execute(text(f"SELECT count(*) FROM {table} WHERE user_id = 1"))
            assert n.scalar() == 0, table
        assert len(db.exec(select(SearchHistory)).all()) == 1
        assert db.exec(select(WatchlistItem)).all() == []
