"""Parsers against real answers recorded from the live sites on 2026-10-04 (no network).

`tests/fixtures/live_2026_10/` holds unedited responses from data.gov.il (GemelNet, fund 103, newest
six months), the Bank of Israel `GetExchangeRates?asXml=true`, Frankfurter `latest` and CoinGecko
`simple/price`. These pin the field and element names the providers rely on; the hand-built
fixtures elsewhere test the logic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.providers.fallback_sources import (
    BoiFxProvider,
    CoinGeckoQuoteProvider,
    FrankfurterFxProvider,
)
from app.providers.gemelnet import GemelNetProvider

FIX = Path(__file__).parent / "fixtures" / "live_2026_10"
GEMELNET_RID = "a30dcbea-a1d2-482c-ae29-8f781f5025fb"


def _client(body: str, content_type: str = "application/json") -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, text=body, headers={"content-type": content_type})
        )
    )


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def _json(name: str) -> Any:
    return json.loads(_read(name))


def test_default_gemelnet_resource_is_the_verified_one() -> None:
    assert Settings().gemelnet_resource_ids["monthly_returns"] == GEMELNET_RID


def test_gemelnet_configured_columns_exist_in_the_real_dataset() -> None:
    data = _json("gemelnet_fund_103.json")
    columns = {f["id"] for f in data["result"]["fields"]}
    assert set(Settings().gemelnet_fields.values()) <= columns


def test_gemelnet_real_answer_parses_into_a_fund_series() -> None:
    p = GemelNetProvider(Settings(), client=_client(_read("gemelnet_fund_103.json")))
    f = p.get_fund("103")
    assert f.value is not None, f.missing_reason
    s = f.value
    assert s.info.fund_id == "103" and s.info.name and s.info.classification
    assert [m.period for m in s.months] == sorted(m.period for m in s.months)
    last = s.months[-1]
    assert last.period == "2026-08"
    assert (last.monthly_return_pct, last.total_assets, last.management_fee_pct) == (
        1.68,
        25546.75,
        0.53,
    )


def test_boi_real_xml_gives_the_usd_representative_rate() -> None:
    boi = BoiFxProvider(Settings(), client=_client(_read("boi_rates.xml"), "application/xml"))
    q = boi.get_quotes(["ILS=X"])["ILS=X"]
    assert (q.price, q.currency, q.source, q.basis) == (3.06, "ILS", "boi", "last_close")
    assert q.as_of.date().isoformat() == "2026-10-02"


def test_frankfurter_real_answer() -> None:
    fx = FrankfurterFxProvider(Settings(), client=_client(_read("frankfurter_latest.json")))
    q = fx.get_quotes(["ILS=X"])["ILS=X"]
    assert (q.price, q.source) == (3.0653, "frankfurter")
    assert q.as_of.date().isoformat() == "2026-10-02"


def test_coingecko_real_answer_covers_every_configured_coin() -> None:
    s = Settings(coingecko_api_key="cg-key")
    cg = CoinGeckoQuoteProvider(s, client=_client(_read("coingecko_simple_price.json")))
    quotes = cg.get_quotes(list(s.coingecko_ids))
    assert set(quotes) == set(s.coingecko_ids)
    assert all(q.currency == "USD" and q.price > 0 for q in quotes.values())
