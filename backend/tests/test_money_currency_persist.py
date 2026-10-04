"""Yahoo's currency is persisted on `Security`; single-ticker retries rotate (2.0-A item 4)."""

from __future__ import annotations

import pandas as pd
import pytest
from sqlmodel import Session

from app.config import Settings
from app.models import Security
from app.providers.registry import _store_yahoo_currency, _stored_yahoo_currency
from app.providers.yfinance_provider import YFinanceProvider
from tests.test_money_quotes import _single_multiindex


def _wired(**settings: float) -> YFinanceProvider:
    prov = YFinanceProvider(Settings(provider_backoff_base_seconds=0.0, **settings))  # type: ignore[arg-type]
    prov.stored_currency = _stored_yahoo_currency
    prov.store_currency = _store_yahoo_currency
    return prov


class _Ticker:
    def __init__(self, currency: str | None, fail: bool = False) -> None:
        self.currency, self.fail = currency, fail

    @property
    def fast_info(self) -> dict[str, str | None]:
        if self.fail:
            raise RuntimeError("429 Too Many Requests")
        return {"currency": self.currency}


def test_successful_lookup_is_written_to_the_security_row(
    monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    import yfinance as yf

    monkeypatch.setattr(yf, "Ticker", lambda s: _Ticker("ILA"))
    prov = _wired()
    assert prov.raw_currency("TEVA.TA") == "ILA"
    db.expire_all()
    sec = db.get(Security, "TEVA.TA")
    assert sec is not None and sec.yahoo_currency == "ILA"
    assert sec.currency == "ILS"  # the normalised column is untouched


def test_restart_under_a_429_still_gets_a_tase_quote_from_the_stored_currency(
    monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    """After a restart the in-memory currency cache is empty; Yahoo answers 429 to fast_info."""
    import yfinance as yf

    sec = db.get(Security, "TEVA.TA")
    assert sec is not None
    sec.yahoo_currency = "ILA"
    db.add(sec)
    db.commit()

    frame = _single_multiindex("TEVA.TA", [6400.0, 6500.0], True)
    monkeypatch.setattr(yf, "download", lambda **kw: frame)
    monkeypatch.setattr(yf, "Ticker", lambda s: _Ticker(None, fail=True))
    fresh = _wired()  # a new process: empty caches
    q = fresh.get_quotes(["TEVA.TA"])
    assert q["TEVA.TA"].price == 65.0 and q["TEVA.TA"].currency == "ILS"  # agorot -> ILS, not x100


def test_without_a_stored_currency_a_429_still_means_no_tase_quote(
    monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    import yfinance as yf

    frame = _single_multiindex("TEVA.TA", [6400.0, 6500.0], True)
    monkeypatch.setattr(yf, "download", lambda **kw: frame)
    monkeypatch.setattr(yf, "Ticker", lambda s: _Ticker(None, fail=True))
    assert _wired().get_quotes(["TEVA.TA"]) == {}  # ambiguous agorot/ILS stays "no price"


def test_stored_currency_beats_the_security_currency_hint(
    monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    import yfinance as yf

    sec = db.get(Security, "TEVA.TA")
    assert sec is not None
    sec.yahoo_currency = "ILA"
    db.add(sec)
    db.commit()
    monkeypatch.setattr(yf, "Ticker", lambda s: _Ticker(None, fail=True))
    prov = _wired()
    prov.currency_hint = lambda s: "USD"  # a wrong hint must lose
    assert prov.resolve_currency("TEVA.TA") == "ILA"


def test_persistence_errors_never_break_a_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    import yfinance as yf

    def boom(*a: object) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(yf, "Ticker", lambda s: _Ticker("USD"))
    prov = YFinanceProvider(Settings())
    prov.store_currency = boom
    prov.stored_currency = boom  # type: ignore[assignment]
    assert prov.raw_currency("AAPL") == "USD"


def test_single_retries_rotate_so_junk_symbols_cannot_starve_real_ones(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import yfinance as yf

    real = _single_multiindex("ZREAL", [10.0, 11.0], True)
    singles: list[str] = []

    def fake_download(**kw: object) -> pd.DataFrame:
        tickers = str(kw["tickers"])
        if " " not in tickers:
            singles.append(tickers)
            return real if tickers == "ZREAL" else pd.DataFrame()
        return pd.DataFrame()  # the batch comes back empty

    monkeypatch.setattr(yf, "download", fake_download)
    prov = YFinanceProvider(
        Settings(
            provider_backoff_base_seconds=0.0,
            provider_single_retry_max=2,
            provider_breaker_threshold=1000,
        )
    )
    monkeypatch.setattr(prov, "raw_currency", lambda s: "USD")
    symbols = ["JUNK1", "JUNK2", "JUNK3", "JUNK4", "ZREAL"]  # ZREAL sorts last
    got: dict[str, object] = {}
    for _ in range(3):
        got = dict(prov.get_quotes(symbols))
    assert "ZREAL" in got  # it took a slot within three cycles (sorted order would never reach it)
    assert set(singles) >= {"JUNK1", "JUNK2", "JUNK3", "JUNK4", "ZREAL"}
