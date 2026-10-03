"""`yahoo_currency` plausibility, two agreeing lookups and the stored-value fallback (2.0-F item 9)."""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.config import Settings
from app.models import Security
from app.providers.registry import _store_yahoo_currency, _stored_yahoo_currency
from app.providers.yfinance_provider import YFinanceProvider


def wired(**settings: object) -> YFinanceProvider:
    # TTL 0: every raw_currency call is a fresh lookup (the real TTL would only delay the second one)
    prov = YFinanceProvider(
        Settings(
            _env_file=None,
            provider_backoff_base_seconds=0.0,
            currency_cache_ttl_seconds=0.0,
            **settings,
        )  # type: ignore[arg-type]
    )
    prov.stored_currency = _stored_yahoo_currency
    prov.store_currency = _store_yahoo_currency
    return prov


class Ticker:
    """`yf.Ticker(...).fast_info` answering from a script: a currency, None, or an error."""

    def __init__(self, script: list[str | Exception | None]) -> None:
        self.script = list(script)

    def __call__(self, symbol: str) -> Ticker:
        return self

    @property
    def fast_info(self) -> dict[str, str | None]:
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return {"currency": item}


def stored(db: Session, symbol: str) -> str | None:
    db.expire_all()
    sec = db.get(Security, symbol)
    assert sec is not None
    return sec.yahoo_currency


def seed(db: Session, symbol: str, value: str | None) -> None:
    sec = db.get(Security, symbol)
    assert sec is not None
    sec.yahoo_currency = value
    db.add(sec)
    db.commit()


# ---------------------------------------------------------------- plausibility per market
@pytest.mark.parametrize("bad", ["USD", "EUR", "GBP", "JPY"])
def test_a_tase_symbol_never_accepts_a_non_israeli_currency(
    monkeypatch: pytest.MonkeyPatch, db: Session, bad: str
) -> None:
    """Review: a lookup returning USD for TEVA.TA overwrote ILA and survived 429s and restarts."""
    import yfinance as yf

    seed(db, "TEVA.TA", "ILA")
    monkeypatch.setattr(yf, "Ticker", Ticker([bad, bad, bad]))
    prov = wired()
    for _ in range(3):
        assert prov.raw_currency("TEVA.TA") == "ILA"  # the implausible answer is ignored
    assert stored(db, "TEVA.TA") == "ILA"


@pytest.mark.parametrize("ok", ["ILA", "ILS"])
def test_a_tase_symbol_accepts_agorot_and_shekels(
    monkeypatch: pytest.MonkeyPatch, db: Session, ok: str
) -> None:
    import yfinance as yf

    seed(db, "TEVA.TA", None)
    monkeypatch.setattr(yf, "Ticker", Ticker([ok]))
    assert wired().raw_currency("TEVA.TA") == ok
    assert stored(db, "TEVA.TA") == ok  # nothing stored yet: the first plausible answer is kept


def test_the_allow_list_is_config(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    import yfinance as yf

    seed(db, "TEVA.TA", None)
    monkeypatch.setattr(yf, "Ticker", Ticker(["USD"]))
    prov = wired(yahoo_currency_allowed={".TA": ["USD"]})
    assert prov.raw_currency("TEVA.TA") == "USD"


def test_crypto_pairs_accept_usd_only(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    import yfinance as yf

    monkeypatch.setattr(yf, "Ticker", Ticker(["ILA"]))
    assert wired().raw_currency("BTC-USD") is None


def test_symbols_without_a_rule_are_unrestricted(
    monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    import yfinance as yf

    monkeypatch.setattr(yf, "Ticker", Ticker(["CAD"]))
    assert wired().raw_currency("SHOP.TO") == "CAD"


# ---------------------------------------------------------------- two lookups must agree
def test_a_plausible_change_needs_two_agreeing_lookups(
    monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    import yfinance as yf

    seed(db, "TEVA.TA", "ILA")
    monkeypatch.setattr(yf, "Ticker", Ticker(["ILS", "ILS", "ILS"]))
    prov = wired()
    assert prov.raw_currency("TEVA.TA") == "ILA"  # first sighting: not trusted yet
    assert stored(db, "TEVA.TA") == "ILA"
    assert prov.raw_currency("TEVA.TA") == "ILS"  # second agreeing lookup: adopted
    assert stored(db, "TEVA.TA") == "ILS"


def test_a_flapping_answer_never_changes_the_stored_value(
    monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    import yfinance as yf

    seed(db, "TEVA.TA", "ILA")
    monkeypatch.setattr(yf, "Ticker", Ticker(["ILS", "ILA", "ILS", "ILA"]))
    prov = wired()
    for _ in range(4):
        assert prov.raw_currency("TEVA.TA") == "ILA"
    assert stored(db, "TEVA.TA") == "ILA"


# ---------------------------------------------------------------- empty or failed lookups
@pytest.mark.parametrize("answer", [None, "", RuntimeError("429")])
def test_an_empty_or_failed_lookup_falls_back_to_the_stored_value(
    monkeypatch: pytest.MonkeyPatch, db: Session, answer: object
) -> None:
    """Review: a lookup that succeeded with currency None ignored the stored ILA, so the TASE quote
    disappeared for the cache TTL."""
    import yfinance as yf

    seed(db, "TEVA.TA", "ILA")
    monkeypatch.setattr(yf, "Ticker", Ticker([answer]))  # type: ignore[list-item]
    assert wired().raw_currency("TEVA.TA") == "ILA"
    assert stored(db, "TEVA.TA") == "ILA"


def test_without_a_stored_value_an_empty_lookup_stays_unknown(
    monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    import yfinance as yf

    seed(db, "TEVA.TA", None)
    monkeypatch.setattr(yf, "Ticker", Ticker([None]))
    assert wired().raw_currency("TEVA.TA") is None  # never a guess


def test_the_empty_answer_is_cached_as_the_stored_value_for_the_ttl(
    monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    import yfinance as yf

    seed(db, "TEVA.TA", "ILA")
    t = Ticker([None])
    monkeypatch.setattr(yf, "Ticker", t)
    prov = YFinanceProvider(Settings(_env_file=None, currency_cache_ttl_seconds=3600.0))
    prov.stored_currency = _stored_yahoo_currency
    prov.store_currency = _store_yahoo_currency
    assert prov.raw_currency("TEVA.TA") == "ILA"
    assert prov.raw_currency("TEVA.TA") == "ILA"  # cached: the script is not asked again
    assert t.script == []


def test_an_agreeing_lookup_resets_a_pending_change(
    monkeypatch: pytest.MonkeyPatch, db: Session
) -> None:
    import yfinance as yf

    seed(db, "TEVA.TA", "ILA")
    monkeypatch.setattr(yf, "Ticker", Ticker(["ILS", "ILA", "ILS"]))
    prov = wired()
    assert [prov.raw_currency("TEVA.TA") for _ in range(3)] == ["ILA", "ILA", "ILA"]
    assert stored(db, "TEVA.TA") == "ILA"
