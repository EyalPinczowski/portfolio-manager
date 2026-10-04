"""Scout: deterministic data gathering (no LLM) -> `ScoutReport`.

For now the Scout reports the security's identity, the cached price with its freshness and which
signals have no data (fundamentals, analysts, news, sentiment: confidence 0, never a neutral score).
Fundamentals providers are not wired into this route yet, so nothing is fetched beyond the existing
quote and history chain.
"""

from __future__ import annotations

from datetime import datetime

from app.analyze.schemas import ChartReport, MissingInput, PriceInfo, ScoutReport
from app.models import Security
from app.signals.base import Explanation, ExplanationSource
from app.timeutil import as_utc

PLANNED = (
    ("fundamentals", "No fundamentals provider is connected for this route yet."),
    ("analysts", "No analyst-consensus provider is connected for this route yet."),
    ("geo_news", "No news or geopolitical signal is connected for this route yet."),
    ("sentiment", "No market-sentiment signal is connected for this route yet."),
)


def build_scout_report(
    sec: Security,
    *,
    verified: bool,
    price: PriceInfo | None,
    price_reason: str | None,
    chart: ChartReport,
    history_as_of: datetime | None,
) -> ScoutReport:
    missing = [MissingInput(name=n, reason=r) for n, r in PLANNED]
    if sec.asset_type == "crypto":
        missing.append(
            MissingInput(
                name="analyst_and_insider", reason="Crypto has no analyst or insider data."
            )
        )
    sources: list[ExplanationSource] = []
    if price is not None:
        sources.append(
            ExplanationSource(
                name="Quote", as_of=as_utc(price.as_of), detail=f"{price.source} ({price.basis})."
            )
        )
    if history_as_of is not None:
        sources.append(
            ExplanationSource(
                name="Daily price history (OHLCV)",
                as_of=as_utc(history_as_of),
                detail="Provider bars; the last bar's date.",
            )
        )
    summary = (
        f"{sec.symbol} ({sec.name_en}): {sec.asset_type} on {sec.market}, {chart.bars} daily bars. "
        + (
            f"Price {price.price:g} {price.currency} "
            f"({'fresh' if price.is_fresh else 'not fresh enough for exit levels'}). "
            if price
            else f"No price: {price_reason or 'unavailable'}. "
        )
        + "Signals without data are reported as not available and carry no weight."
    )
    return ScoutReport(
        symbol=sec.symbol,
        name_en=sec.name_en,
        name_he=sec.name_he,
        market=sec.market,
        asset_type=sec.asset_type,
        sector=sec.sector,
        country=sec.country,
        currency=sec.currency,
        verified=verified,
        price=price,
        price_reason=price_reason,
        bars=chart.bars,
        history_as_of=history_as_of,
        not_available=missing,
        explanation=Explanation(
            summary=summary[:1900],
            as_of=as_utc(price.as_of) if price else None,
            rules_applied=[
                "A signal with no data has confidence 0; its weight goes to the signals that have data.",
                "No number is guessed: a missing price or history is reported, not filled in.",
            ],
            sources=sources,
        ),
    )
