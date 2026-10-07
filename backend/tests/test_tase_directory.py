"""TASE directory (Update 15): provider, daily job, matching, search, migration.

Every fixture is INVENTED (numbers, names, field spellings): no real TASE data and no network.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlmodel import Session, select

from app.config import Settings
from app.db import current_revision, downgrade_migrations, make_engine, run_migrations
from app.importer.match import SecurityIndex, apply_match, resolve_row
from app.importer.parse import ParsedRow
from app.importer.service import finalize_rows
from app.models import Security, TaseDirectoryRow
from app.providers.tase_directory import TaseDirectory, parse_directory
from app.scheduler.jobs import run_tase_directory_if_empty, run_tase_directory_refresh
from app.timeutil import utcnow

SignupFn = Callable[..., TestClient]
TODAY = date(2026, 10, 7)

SAMPLE: list[dict[str, Any]] = [
    {
        "securityId": 7000001,
        "name": "קרן דמה אלפא",
        "englishName": "Sample Alpha Fund",
        "symbol": "ALF1",
        "type": "ETF",
    },
    {
        "id": "7000002",
        "hebrewName": "מניית דמה בטא",
        "nameEn": "Sample Beta Ltd",
        "ticker": "bta",
        "securityType": "Share",
    },
    {"number": "7000003", "securityName": "קרן כפולה", "tradingSymbol": "DUP1"},
    {"IsinOrNumber": "7000004", "nameHe": "קרן כפולה", "type": "Fund"},
    {"securityId": "12", "name": "מספר קצר מדי"},  # not a TASE number
    {"securityId": "7000005"},  # no name at all
    "garbage",
]


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, tase_api_key="invented-key", **kw)


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _provider(handler: Callable[[httpx.Request], httpx.Response], **kw: Any) -> TaseDirectory:
    return TaseDirectory(_settings(**kw), client=_client(handler), sleep=lambda _s: None)


# ------------------------------------------------------------------ parser
@pytest.mark.parametrize(
    "wrap",
    [
        lambda rows: rows,
        lambda rows: {"tradeSecuritiesList": rows},
        lambda rows: {"items": rows},
        lambda rows: {"data": {"items": rows}},
        lambda rows: {"SomethingNew": rows, "total": 3},
    ],
)
def test_parser_finds_the_list_under_any_key(wrap: Callable[[Any], Any]) -> None:
    out = parse_directory(wrap(SAMPLE))
    assert [e.tase_number for e in out] == ["7000001", "7000002", "7000003", "7000004"]


def test_parser_reads_every_field_spelling() -> None:
    by = {e.tase_number: e for e in parse_directory(SAMPLE)}
    a, b = by["7000001"], by["7000002"]
    assert (a.name_he, a.name_en, a.trading_symbol, a.type) == (
        "קרן דמה אלפא",
        "Sample Alpha Fund",
        "ALF1",
        "ETF",
    )
    assert (b.name_he, b.name_en, b.trading_symbol, b.type) == (
        "מניית דמה בטא",
        "Sample Beta Ltd",
        "BTA",
        "Share",
    )
    assert by["7000004"].name_he == "קרן כפולה" and by["7000004"].trading_symbol is None


@pytest.mark.parametrize("data", [None, {}, [], {"x": 1}, "text", [[1, 2]], {"items": "no"}])
def test_parser_unknown_shapes_are_empty(data: Any) -> None:
    assert parse_directory(data) == []


# ------------------------------------------------------------------ provider
def test_no_key_means_disabled_and_no_call() -> None:
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, json=SAMPLE)

    p = TaseDirectory(Settings(_env_file=None), client=_client(handler), sleep=lambda _s: None)
    assert not p.enabled and not p.available()
    assert p.fetch_latest(TODAY) == [] and calls == []


def test_fetch_sends_headers_and_the_dated_url() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"tradeSecuritiesList": SAMPLE})

    out = _provider(handler).fetch_latest(TODAY)
    assert len(out) == 4 and len(seen) == 1
    req = seen[0]
    assert req.url.path == "/v1/basic-securities/trade-securities-list/2026/10/7"
    assert req.url.host == "datawise.tase.co.il"
    assert req.headers["apikey"] == "invented-key"
    assert req.headers["accept-language"] == "he-IL" and req.headers["accept"] == "application/json"


def test_an_empty_day_falls_back_to_an_earlier_day() -> None:
    days: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        days.append(req.url.path.rsplit("/", 3)[-1])
        if len(days) == 1:
            return httpx.Response(200, json={"tradeSecuritiesList": []})
        if len(days) == 2:
            return httpx.Response(404)
        if len(days) == 3:
            return httpx.Response(204)
        return httpx.Response(200, json=SAMPLE)

    out = _provider(handler).fetch_latest(TODAY)
    assert len(out) == 4 and days == ["7", "6", "5", "4"]


def test_lookback_is_bounded() -> None:
    n = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        return httpx.Response(200, json=[])

    assert _provider(handler, tase_directory_lookback_days=2).fetch_latest(TODAY) == []
    assert n == 3


@pytest.mark.parametrize("status", [401, 403, 429])
def test_auth_and_rate_errors_return_empty_and_open_the_breaker(status: int) -> None:
    n = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal n
        n += 1
        return httpx.Response(status, text="secret body invented-key")

    p = _provider(handler)
    assert p.fetch_latest(TODAY) == [] and n == 1
    assert p.budget.breaker_open() and not p.available()
    assert p.fetch_latest(TODAY) == [] and n == 1  # the breaker blocks the next call


def test_server_error_and_bad_json_return_empty() -> None:
    assert _provider(lambda r: httpx.Response(500)).fetch_latest(TODAY) == []
    assert _provider(lambda r: httpx.Response(200, text="<html>")).fetch_latest(TODAY) == []

    def boom(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    assert _provider(boom).fetch_latest(TODAY) == []


def test_key_and_bodies_never_reach_the_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    _provider(lambda r: httpx.Response(403, text="BODY-TEXT")).fetch_latest(TODAY)
    text = caplog.text
    assert "invented-key" not in text and "BODY-TEXT" not in text


# ------------------------------------------------------------------ refresh job
def _count(db: Session) -> int:
    return len(db.exec(select(TaseDirectoryRow)).all())


def test_refresh_replaces_rows_in_one_go(db: Session) -> None:
    db.add(TaseDirectoryRow(tase_number="9999999", name_he="ישן", refreshed_at=utcnow()))
    db.commit()
    p = _provider(lambda r: httpx.Response(200, json=SAMPLE))
    assert run_tase_directory_refresh(db, _settings(), p) == 4
    nums = {r.tase_number for r in db.exec(select(TaseDirectoryRow)).all()}
    assert nums == {"7000001", "7000002", "7000003", "7000004"}


def test_refresh_keeps_the_old_table_when_the_fetch_fails(db: Session) -> None:
    db.add(TaseDirectoryRow(tase_number="9999999", name_he="ישן", refreshed_at=utcnow()))
    db.commit()
    for handler in (lambda r: httpx.Response(500), lambda r: httpx.Response(200, json=[])):
        assert run_tase_directory_refresh(db, _settings(), _provider(handler)) == 0
        assert [r.tase_number for r in db.exec(select(TaseDirectoryRow)).all()] == ["9999999"]


def test_refresh_without_a_key_calls_nothing(db: Session) -> None:
    calls: list[int] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json=SAMPLE)

    s = Settings(_env_file=None)
    p = TaseDirectory(s, client=_client(handler), sleep=lambda _s: None)
    assert run_tase_directory_refresh(db, s, p) == 0 and calls == [] and _count(db) == 0
    assert run_tase_directory_if_empty(db, s) == 0


def test_boot_fill_only_when_empty(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    fetched: list[int] = []

    def fake_fetch(self: TaseDirectory, today: date | None = None) -> list[Any]:
        fetched.append(1)
        return parse_directory(SAMPLE)

    monkeypatch.setattr(TaseDirectory, "fetch_latest", fake_fetch)
    s = _settings()
    assert run_tase_directory_if_empty(db, s) == 4
    assert run_tase_directory_if_empty(db, s) == 0  # already filled
    assert fetched == [1]


# ------------------------------------------------------------------ matching
def _directory(db: Session) -> None:
    now = utcnow()
    db.add_all(
        [
            TaseDirectoryRow(
                tase_number="7000001",
                name_he="קרן דמה אלפא",
                name_en="Sample Alpha Fund",
                refreshed_at=now,
            ),
            TaseDirectoryRow(tase_number="7000003", name_he="קרן כפולה", refreshed_at=now),
            TaseDirectoryRow(tase_number="7000004", name_he="קרן כפולה", refreshed_at=now),
        ]
    )
    db.commit()


def _row(**kw: Any) -> ParsedRow:
    base: dict[str, Any] = {
        "index": 0, "name": "", "quantity": 10, "price": 100.0, "value": 1000.0,
        "currency": "ILS", "unit": "ILS",
    }  # fmt: skip
    return ParsedRow(**{**base, **kw})


def _index(db: Session) -> SecurityIndex:
    return SecurityIndex(
        list(db.exec(select(Security)).all()),
        lambda: list(db.exec(select(TaseDirectoryRow)).all()),
    )


def test_a_number_found_only_in_the_directory(db: Session) -> None:
    _directory(db)
    row = _row(name="garbled ocr", tase_number="7000001")
    res = resolve_row(row, _index(db))
    assert res.method == "directory" and res.security is None
    apply_match(row, res)
    assert row.symbol == "7000001.TA" and row.tase_number == "7000001"
    assert row.matched_name == "Sample Alpha Fund" and "unmatched" not in row.flags


def test_a_hebrew_name_found_in_the_directory(db: Session) -> None:
    _directory(db)
    row = _row(name='קרן דמה אלפא בע"מ')
    res = resolve_row(row, _index(db))
    assert res.method == "directory" and res.directory is not None
    assert res.directory.tase_number == "7000001"


def test_an_ambiguous_name_stays_unmatched(db: Session) -> None:
    _directory(db)
    row = _row(name="קרן כפולה")
    res = resolve_row(row, _index(db))
    assert res.method != "directory" and res.security is None
    apply_match(row, res, _index(db))
    assert "unmatched" in row.flags and row.symbol is None


def test_a_dollar_row_never_uses_the_directory(db: Session) -> None:
    _directory(db)
    row = _row(name="x", tase_number="7000001", currency="USD", unit="USD")
    assert resolve_row(row, _index(db)).method != "directory"


def test_the_seed_wins_over_the_directory(db: Session) -> None:
    _directory(db)
    db.add(
        Security(
            symbol="ALFA.TA",
            name_en="Alpha Seeded",
            tase_number="7000001",
            market="TASE",
            currency="ILS",
        )
    )
    db.commit()
    res = resolve_row(_row(name="x", tase_number="7000001"), _index(db))
    assert res.method == "tase_number" and res.security is not None


def test_finalize_and_confirm_create_a_named_unverified_security(db: Session) -> None:
    from app.importer.service import row_security

    _directory(db)
    rows = finalize_rows(
        db, [_row(name="????", tase_number="7000001")], Settings(_env_file=None), owner_id=1
    )
    assert rows[0].symbol == "7000001.TA" and "unmatched" not in rows[0].flags
    created: list[str] = []
    sec = row_security(db, rows[0], created)
    assert created == ["7000001.TA"] and sec.verified is False
    assert sec.name_en == "Sample Alpha Fund" and sec.name_he == "קרן דמה אלפא"
    assert sec.tase_number == "7000001" and sec.market == "TASE" and sec.currency == "ILS"


# ------------------------------------------------------------------ search
def test_search_finds_a_directory_hit_by_number_and_name(signup: SignupFn) -> None:
    from app.db import new_session

    with new_session() as db:
        _directory(db)
    c = signup()
    hits = c.get("/api/securities/search", params={"q": "70000"}).json()
    assert {h["symbol"] for h in hits} == {"7000001.TA", "7000003.TA", "7000004.TA"}
    h = next(x for x in hits if x["symbol"] == "7000001.TA")
    assert h["source"] == "tase_list" and h["market"] == "TASE" and h["currency"] == "ILS"
    assert h["name_he"] == "קרן דמה אלפא" and h["name_en"] == "Sample Alpha Fund"
    by_name = c.get("/api/securities/search", params={"q": "אלפא"}).json()
    assert "7000001.TA" in [x["symbol"] for x in by_name]
    assert c.get("/api/securities/search", params={"q": "7"}).json() == []  # too short


def test_search_hides_a_directory_hit_the_seed_already_has(signup: SignupFn) -> None:
    from app.db import new_session

    with new_session() as db:
        _directory(db)
        db.add(
            Security(
                symbol="ALFA.TA",
                name_en="Alpha Seeded",
                tase_number="7000001",
                market="TASE",
                currency="ILS",
            )
        )
        db.commit()
    c = signup()
    syms = [h["symbol"] for h in c.get("/api/securities/search", params={"q": "7000001"}).json()]
    assert syms == []  # no `.TA` duplicate of the seeded one; the seed row is not name-matched here


def test_search_with_no_directory_is_unchanged(signup: SignupFn) -> None:
    c = signup()
    assert c.get("/api/securities/search", params={"q": "7000001"}).json() == []


# ------------------------------------------------------------------ migration
def test_migration_0022_up_and_down(tmp_path: Path) -> None:
    eng = make_engine(f"sqlite:///{tmp_path / 'm.db'}")
    run_migrations(eng)
    cols = {c["name"] for c in inspect(eng).get_columns("tase_directory")}
    assert cols == {"tase_number", "name_he", "name_en", "trading_symbol", "kind", "refreshed_at"}
    assert inspect(eng).get_pk_constraint("tase_directory")["constrained_columns"] == [
        "tase_number"
    ]
    downgrade_migrations(eng, "0021_portfolio_risk_chosen_at")
    assert "tase_directory" not in inspect(eng).get_table_names()
    assert current_revision(eng) == "0021_portfolio_risk_chosen_at"
    run_migrations(eng)
    assert "tase_directory" in inspect(eng).get_table_names()
    eng.dispose()
